"""人流ナウキャスティング(Phase 2 の骨組み)。

「今の表示値」ではなく「**到着した頃の待ち**」を出す = 群衆補正込みの到着時予測。
CLAUDE.md 心理設計原則1(自壊する信号)の実装:魅力的な低い数字ほど速く埋まる。

中核モデル: 平均回帰(mean-reversion)。
  乖離 = 現在待ち - その時間帯の平常値
  到着時の乖離 = 乖離 * exp(-Δ/τ)     # τ=群衆弾性(乖離が薄れる速さ)。後日アトラクション別に推定。
  到着時予測 = 到着時刻の平常値 + 到着時の乖離
平常値の履歴が薄いうちは、直近の傾き(モメンタム)にフォールバックし、それも無ければ現状維持。

DoD(過去データでバックテストし的中率を出す)は backtest() で計算する。的中の“数字”は
実データが数日〜数週間貯まってから意味を持つ。ここではロジックと検算の器を用意する。
"""

from __future__ import annotations

import argparse
import math

import pandas as pd

from .board import STOP_STATUSES, current_board

ATTRACTION = "ATTRACTION"
DEFAULT_TAU_MIN = 30.0        # 群衆弾性。乖離が 1/e に薄れるまでの分。まずは全アトラクション共通。
DEFAULT_ARRIVAL_MIN = 20      # 「今から◯分後に着く」の既定。
_MIN_TYPICAL_SAMPLES = 3      # この件数未満の平常値は使わない(モメンタムへ)。


def herd_adjusted_value(
    current: float | None,
    typical_now: float | None,
    typical_arrival: float | None,
    slope_per_min: float | None,
    arrival_min: float,
    *,
    tau: float = DEFAULT_TAU_MIN,
) -> tuple[int | None, str]:
    """到着時待ちの予測値と使った手法を返す(純粋な数式部分)。

    - reversion: 平常値がある → 乖離を τ で減衰させ、到着時刻の平常値に足す。
    - momentum : 平常値が無い → 現在 + 傾き×Δ。
    - flat     : どちらも無い → 現状維持。
    """
    if current is None:
        return None, "none"

    if typical_now is not None and typical_arrival is not None:
        deviation = current - typical_now
        decayed = deviation * math.exp(-arrival_min / tau)
        pred = typical_arrival + decayed
        method = "reversion"
    elif slope_per_min is not None:
        pred = current + slope_per_min * arrival_min
        method = "momentum"
    else:
        pred = current
        method = "flat"

    pred = max(0.0, pred)
    return int(round(pred / 5.0) * 5), method


def _typical_by_hour(sub: pd.DataFrame) -> pd.Series:
    """entity 部分集合の hour→平均待ち(件数が少ない hour は捨てる)。"""
    s = sub[sub["wait_minutes"].notna()]
    if s.empty:
        return pd.Series(dtype=float)
    agg = s.groupby("hour")["wait_minutes"].agg(["mean", "count"])
    agg = agg[agg["count"] >= _MIN_TYPICAL_SAMPLES]
    return agg["mean"]


def _recent_slope(sub_sorted: pd.DataFrame) -> float | None:
    """直近2点の傾き(分あたり)。取れなければ None。"""
    s = sub_sorted[sub_sorted["wait_minutes"].notna()]
    if len(s) < 2:
        return None
    last = s.iloc[-1]
    prev = s.iloc[-2]
    dt_min = (last["ts_local"] - prev["ts_local"]).total_seconds() / 60.0
    if dt_min <= 0:
        return None
    return (last["wait_minutes"] - prev["wait_minutes"]) / dt_min


def _signal(current: int | None, pred: int | None) -> str:
    """到着時予測 vs 現在で、混む/空く/横ばい。"""
    if current is None or pred is None:
        return "不明"
    diff = pred - current
    if diff >= 10:
        return "混む"    # 今低くても着く頃は埋まる(罠)
    if diff <= -10:
        return "空く"    # 今高くても待てば下がる
    return "横ばい"


def predict_board(
    df: pd.DataFrame,
    park_id: str,
    *,
    arrival_min: float = DEFAULT_ARRIVAL_MIN,
    tau: float = DEFAULT_TAU_MIN,
) -> pd.DataFrame:
    """現況ボードに到着時予測を足す。current_board の列 + pred_wait/pred_method/pred_delta/signal。"""
    board = current_board(df, park_id)
    if board.empty:
        board["pred_wait"] = []
        board["pred_method"] = []
        board["pred_delta"] = []
        board["signal"] = []
        return board

    latest_ts = pd.Timestamp(board["ts_local"].iloc[0])
    arrival_hour = int((latest_ts + pd.Timedelta(minutes=arrival_min)).hour)

    hist = df[(df["park_id"] == park_id) & (df["entity_type"] == ATTRACTION)]

    preds, methods, deltas, signals = [], [], [], []
    for _, row in board.iterrows():
        # 停止・休止は予測しない。
        if row["status"] in STOP_STATUSES:
            preds.append(None); methods.append(None); deltas.append(None); signals.append(None)
            continue
        cur = row["wait_minutes"]
        # entity_id を board は持たないので name で引く(current_board は名前一意前提)。
        sub = hist[hist["name"] == row["name"]].sort_values("ts_local")
        typ = _typical_by_hour(sub)
        typ_now = float(typ.loc[latest_ts.hour]) if latest_ts.hour in typ.index else None
        typ_arr = float(typ.loc[arrival_hour]) if arrival_hour in typ.index else None
        slope = _recent_slope(sub)
        pred, method = herd_adjusted_value(
            cur, typ_now, typ_arr, slope, arrival_min, tau=tau
        )
        preds.append(pred)
        methods.append(method)
        deltas.append((pred - cur) if (pred is not None and cur is not None) else None)
        signals.append(_signal(cur, pred))

    board = board.copy()
    board["pred_wait"] = preds
    board["pred_method"] = methods
    board["pred_delta"] = deltas
    board["signal"] = signals
    return board


def backtest(
    df: pd.DataFrame,
    *,
    arrival_min: float = DEFAULT_ARRIVAL_MIN,
    tau: float = DEFAULT_TAU_MIN,
    tolerance_min: float | None = None,
) -> dict:
    """過去データで到着時予測を検算する。到着時予測 MAE を素朴予測(現状維持)と比較。

    各時刻 t について「t の情報だけで t+Δ を当てる」を試し、実際の t+Δ(記録済み)と比べる。
    注意: 平常値は全履歴から算出しており、厳密には未来リークを含む楽観値(上限の目安)。
    素朴予測に勝てるかが最初の判断材料。数字は実データが貯まってから意味を持つ。
    """
    tol = tolerance_min if tolerance_min is not None else max(2.0, arrival_min * 0.34)
    sub_all = df[(df["entity_type"] == ATTRACTION) & df["wait_minutes"].notna()]
    herd_err = []
    naive_err = []
    n_pairs = 0
    hit = 0  # スパイク(実際に +10 以上上がった)を「混む」と当てた数
    spike_total = 0

    for _, g in sub_all.groupby("entity_id"):
        g = g.sort_values("ts_local").reset_index(drop=True)
        if len(g) < 2:
            continue
        typ = _typical_by_hour(g)
        times = g["ts_local"].tolist()
        waits = g["wait_minutes"].tolist()
        hours = g["hour"].tolist()
        for i in range(len(g)):
            t0 = times[i]
            target = t0 + pd.Timedelta(minutes=arrival_min)
            # target に最も近い将来点(許容 tol 分)を探す。
            best_j = None
            best_gap = None
            for j in range(i + 1, len(g)):
                gap = abs((times[j] - target).total_seconds()) / 60.0
                if best_gap is None or gap < best_gap:
                    best_gap, best_j = gap, j
                if times[j] > target and gap > tol and best_gap is not None:
                    break
            if best_j is None or best_gap is None or best_gap > tol:
                continue
            actual = waits[best_j]
            w0 = waits[i]
            arr_hour = int(hours[best_j])
            typ_now = float(typ.loc[int(hours[i])]) if int(hours[i]) in typ.index else None
            typ_arr = float(typ.loc[arr_hour]) if arr_hour in typ.index else None
            slope = None
            if i >= 1:
                dt = (times[i] - times[i - 1]).total_seconds() / 60.0
                if dt > 0:
                    slope = (waits[i] - waits[i - 1]) / dt
            pred, _ = herd_adjusted_value(w0, typ_now, typ_arr, slope, arrival_min, tau=tau)
            if pred is None:
                continue
            herd_err.append(abs(pred - actual))
            naive_err.append(abs(w0 - actual))
            n_pairs += 1
            if actual - w0 >= 10:  # 実際にスパイク。
                spike_total += 1
                if pred - w0 >= 10:
                    hit += 1

    def _mae(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    return {
        "pairs": n_pairs,
        "herd_mae": _mae(herd_err),
        "naive_mae": _mae(naive_err),
        "beats_naive": (
            bool(_mae(herd_err) < _mae(naive_err))
            if herd_err and naive_err
            else None
        ),
        "spike_total": spike_total,
        "spike_hit": hit,
        "spike_recall": round(hit / spike_total, 2) if spike_total else None,
        "arrival_min": arrival_min,
        "tau": tau,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: 蓄積 DB に対して到着時予測をバックテストし、数字を出す(Phase 2 DoD)。"""
    from .queries import connect, load_observations

    p = argparse.ArgumentParser(
        prog="sabotage-backtest",
        description="到着時予測を過去データで検算(到着時MAE を素朴予測と比較・スパイク検知率)。",
    )
    p.add_argument("--db", default="data/sabotage.db", help="SQLite")
    p.add_argument("--arrival", type=float, default=DEFAULT_ARRIVAL_MIN, help="到着まで分")
    p.add_argument("--tau", type=float, default=DEFAULT_TAU_MIN, help="群衆弾性 τ(分)")
    args = p.parse_args(argv)

    df = load_observations(connect(args.db))
    res = backtest(df, arrival_min=args.arrival, tau=args.tau)
    print("=== 到着時予測バックテスト ===")
    if not res["pairs"]:
        print("検算に使えるデータがまだありません(履歴が薄い)。数日貯めてから再実行してください。")
        return 0
    print(f"対象ペア数        : {res['pairs']}")
    print(f"到着時MAE(予測)  : {res['herd_mae']} 分")
    print(f"到着時MAE(素朴)  : {res['naive_mae']} 分  ← 現状維持予測")
    verdict = "○ 素朴予測に勝っている" if res["beats_naive"] else "× まだ素朴予測以下"
    print(f"判定              : {verdict}")
    print(f"スパイク検知      : {res['spike_hit']}/{res['spike_total']}"
          f"(recall={res['spike_recall']})")
    print("注: 平常値は全履歴から算出(楽観値の上限目安)。データが増えるほど信頼できる。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
