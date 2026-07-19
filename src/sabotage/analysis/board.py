"""現況ボード — 「今どうなってる?」を計算する純関数(当日スマホ利用向け)。

最新スナップショットを1行/アトラクションに整形する。DBにもStreamlitにも依存しない。
- wait_minutes: 現在の STANDBY 待ち(運営中でも未掲出なら None)
- delta: 直近スナップショットからの変化(トレンド矢印用)
- fair_wait / value_label: 同アトラクションの同時間帯(hour)の過去平均に対する割安/割高。
  履歴が薄いうちは None(データが溜まるほど賢くなる)。
"""

from __future__ import annotations

import pandas as pd

from .areas import area_for

ATTRACTION = "ATTRACTION"
STOP_STATUSES = {"DOWN", "CLOSED", "REFURBISHMENT"}

_COLS = [
    "name",
    "area",
    "status",
    "wait_minutes",
    "delta",
    "fair_wait",
    "value_label",
    "ts_local",
]


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=_COLS)


def current_board(df: pd.DataFrame, park_id: str, *, min_samples_for_fair: int = 5) -> pd.DataFrame:
    """指定パークの最新スナップショットを現況ボードに整形して返す。

    df は queries.load_observations() の結果。空/該当なしなら空 DataFrame。
    """
    if df is None or df.empty:
        return _empty()
    sub = df[(df["park_id"] == park_id) & (df["entity_type"] == ATTRACTION)].copy()
    sub = sub[sub["ts_local"].notna()]
    if sub.empty:
        return _empty()

    latest_ts = sub["ts_local"].max()
    cur = sub[sub["ts_local"] == latest_ts]

    # 直近1つ前のスナップショット → トレンド(delta)。
    prev_map: dict = {}
    earlier = sub[sub["ts_local"] < latest_ts]
    if not earlier.empty:
        prev_ts = earlier["ts_local"].max()
        prev = sub[sub["ts_local"] == prev_ts]
        prev_map = dict(zip(prev["entity_id"], prev["wait_minutes"]))

    # フェアバリュー: 同 hour の過去平均(現在時刻より前のみ)。
    hour = int(latest_ts.hour)
    hist = sub[
        (sub["hour"] == hour) & (sub["ts_local"] < latest_ts) & sub["wait_minutes"].notna()
    ]
    fair_stats = (
        hist.groupby("entity_id")["wait_minutes"].agg(["mean", "count"])
        if not hist.empty
        else None
    )

    rows = []
    for _, r in cur.iterrows():
        eid = r["entity_id"]
        w = r["wait_minutes"]
        w_int = int(w) if pd.notna(w) else None

        delta = None
        if eid in prev_map and pd.notna(prev_map[eid]) and pd.notna(w):
            delta = int(w - prev_map[eid])

        fair = None
        label = None
        if (
            fair_stats is not None
            and eid in fair_stats.index
            and fair_stats.loc[eid, "count"] >= min_samples_for_fair
            and pd.notna(w)
        ):
            fair = float(fair_stats.loc[eid, "mean"])
            if w <= fair * 0.8:
                label = "割安"
            elif w >= fair * 1.2:
                label = "割高"
            else:
                label = "適正"

        rows.append(
            {
                "name": r["name"],
                "area": area_for(r["name"]),
                "status": r["status"],
                "wait_minutes": w_int,
                "delta": delta,
                "fair_wait": (round(fair) if fair is not None else None),
                "value_label": label,
                "ts_local": latest_ts,
            }
        )

    return pd.DataFrame(rows, columns=_COLS)


def split_operating(board: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(運営中, 停止/休止中) に分ける。運営中は待ち短い順(=穴場優先)。"""
    if board.empty:
        return _empty(), _empty()
    operating = board[~board["status"].isin(STOP_STATUSES)].copy()
    stopped = board[board["status"].isin(STOP_STATUSES)].copy()
    # 待ち未掲出(None)は末尾へ。立ち待ちは損失 → 短い順で穴場を上に。
    operating["_sort"] = operating["wait_minutes"].fillna(10**9)
    operating = operating.sort_values(["_sort", "name"]).drop(columns="_sort")
    stopped = stopped.sort_values("name")
    return operating, stopped
