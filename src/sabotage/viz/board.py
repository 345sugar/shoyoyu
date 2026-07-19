"""現況ボード(スマホ向け)。当日その場で「今どうなってる?」を見る1画面。

起動:
    streamlit run src/sabotage/viz/board.py -- --db data/sabotage.db

- 最新スナップショットの各アトラクションを、待ち短い順(=穴場優先。立ち待ちは損失)で表示。
- 停止・休止中は下部にまとめる(木鶏: 騒がず、素直に別へ)。
- トレンド矢印(直近比)と、履歴が溜まれば割安/割高バッジ。
- データ鮮度を明示(古ければ警告)。表示ロジックは analysis 層(テスト済み)に委譲。
"""

from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from sabotage.analysis import board, nowcast, queries
from sabotage.config import DEFAULT_DB_PATH, DEFAULT_INTERVAL_SECONDS, DEFAULT_JITTER_SECONDS
from sabotage.tools.seed_demo import DEMO_SOURCE, META_DEMO_FLAG

FRESH_LIMIT_MIN = 15  # これを超えて更新が無ければ「古い」警告。


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("SABOTAGE_DB", DEFAULT_DB_PATH))
    # --self-poll: このページ自身が裏で5分ごとに取得する(常時稼働の箱が無くても
    #   Streamlit Community Cloud 等の無料URLで5分ライブにできる)。
    parser.add_argument(
        "--self-poll",
        action="store_true",
        default=os.environ.get("SABOTAGE_SELF_POLL", "").lower() in ("1", "true", "yes"),
    )
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--jitter", type=int, default=DEFAULT_JITTER_SECONDS)
    args, _ = parser.parse_known_args()
    return args


@st.cache_resource
def _ensure_background_poller(db_path: str, interval: int, jitter: int) -> bool:
    """サーバープロセスで一度だけ、ポーラーを常駐スレッドとして起動する。

    Streamlit の cache_resource で「1プロセス1回」を担保。スレッドは自前の Storage 接続
    (WAL)を持つので、描画側の読み取りと並行できる。間隔は5分以上(config 準拠)。
    """
    def _run() -> None:
        try:
            from sabotage.data.client import ThemeParksClient
            from sabotage.data.poller import resolve_parks, run_forever
            from sabotage.data.storage import Storage

            with Storage(db_path) as store, ThemeParksClient() as client:
                parks = resolve_parks(store, client)
                run_forever(store, client, parks, interval=interval, jitter=jitter)
        except Exception:  # noqa: BLE001 — 自前ポーリングが死んでも描画は続ける。
            pass

    threading.Thread(target=_run, name="sabotage-selfpoll", daemon=True).start()
    return True


def _fmt_delta(delta) -> str:
    if delta is None or pd.isna(delta):
        return ""
    d = int(delta)
    if d > 0:
        return f"🔺+{d}"
    if d < 0:
        return f"🔻{d}"
    return "➖"


def _value_badge(label) -> str:
    if not label:
        return ""
    color = {"割安": "#1a7f37", "割高": "#cf222e", "適正": "#6e7781"}.get(label, "#6e7781")
    return f'<span style="color:{color};font-weight:600">({label})</span>'


def _wait_text(status, wait) -> str:
    if status in board.STOP_STATUSES:
        jp = {"DOWN": "停止", "CLOSED": "休止", "REFURBISHMENT": "改修"}.get(status, status)
        return f'<span style="color:#cf222e;font-weight:700">{jp}</span>'
    if wait is None or pd.isna(wait):
        return '<span style="color:#6e7781">運営中</span>'
    return f'<b style="font-size:1.5rem">{int(wait)}</b><span style="font-size:.8rem">分</span>'


def _pred_text(r) -> str:
    """到着時予測「着N分↗」。群衆補正込み。停止/予測不可なら空。"""
    pw = r.get("pred_wait")
    if pw is None or pd.isna(pw):
        return ""
    sig = r.get("signal")
    color = {"混む": "#cf222e", "空く": "#1a7f37"}.get(sig, "#6e7781")
    arrow = {"混む": "↗", "空く": "↘"}.get(sig, "→")
    return f'<span style="color:{color};font-weight:600">着{int(pw)}分{arrow}</span>'


def _row(r) -> str:
    left = _wait_text(r["status"], r["wait_minutes"])
    meta = " · ".join(
        x for x in [r.get("area"), _fmt_delta(r.get("delta")), _pred_text(r)] if x
    )
    badge = _value_badge(r.get("value_label"))
    return (
        '<div style="display:flex;align-items:center;gap:.6rem;'
        'padding:.45rem 0;border-bottom:1px solid rgba(128,128,128,.2)">'
        f'<div style="min-width:4.2rem;text-align:right">{left}</div>'
        f'<div style="flex:1"><div style="font-weight:600">{r["name"]} {badge}</div>'
        f'<div style="font-size:.78rem;color:#6e7781">{meta}</div></div>'
        "</div>"
    )


def render(
    db_path: str,
    *,
    self_poll: bool = False,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    jitter: int = DEFAULT_JITTER_SECONDS,
) -> None:
    st.set_page_config(page_title="sabotage 現況", page_icon="🎢", layout="centered")
    st.title("🎢 現況ボード")

    if self_poll:
        # このページ自身が裏で5分ごとに取得(常時稼働の箱が不要)。
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _ensure_background_poller(db_path, max(interval, 300), jitter)
        # 60秒ごとにページを自動リロードして最新を反映(親フレームごと)。
        components.html(
            "<script>setTimeout(function(){window.parent.location.reload();},60000);</script>",
            height=0,
        )
        st.caption(f"🔴 5分ライブ(このページ自身が取得中・{max(interval, 300)//60}分間隔)")

    if not Path(db_path).exists():
        if self_poll:
            st.info("⏳ 初回取得中… 数十秒で最初のデータが出ます(自動更新)。")
        else:
            st.error(f"DB が見つかりません: `{db_path}`")
            st.caption("`sabotage-poll --loop` で貯めるか、`sabotage-seed-demo` で試せます。")
        return

    # 自前ポーリング初回はスキーマ未作成のことがある(スレッドがファイルだけ先に作る)。
    # 冪等にスキーマを用意してレースを避ける。
    try:
        from sabotage.data.storage import Storage as _Storage

        _Storage(db_path).close()
    except Exception:  # noqa: BLE001
        pass

    conn = queries.connect(db_path)
    df = queries.load_observations(conn)
    if df.empty:
        if self_poll:
            st.info("⏳ 取得中… 最初のデータを待っています(自動更新)。")
        else:
            st.info("まだ観測データがありません。")
        return

    # 合成データなら明示。
    demo_flag = conn.execute("SELECT value FROM meta WHERE key=?", (META_DEMO_FLAG,)).fetchone()
    sources = queries.data_sources(conn)
    if demo_flag and not any(s and s != DEMO_SOURCE for s in sources):
        st.warning("⚠️ 合成デモデータ表示中(実際の待ち時間ではありません)", icon="⚠️")

    names = queries.park_names(conn)
    parks = queries.available_parks(conn)
    if not parks:
        st.info("表示できるパークがありません。")
        return

    park_id = st.radio(
        "パーク", parks, format_func=lambda p: names.get(p, p), horizontal=True
    )
    arrival_min = st.slider(
        "到着まで(分)", min_value=5, max_value=45, value=nowcast.DEFAULT_ARRIVAL_MIN, step=5,
        help="今から何分後に着くか。到着時の待ち(群衆補正込み)を予測する。",
    )

    b = nowcast.predict_board(df[df["park_id"] == park_id], park_id, arrival_min=arrival_min)
    if b.empty:
        st.info("このパークの現況データがありません。")
        return

    # データ鮮度。
    latest = pd.Timestamp(b["ts_local"].iloc[0])
    now = pd.Timestamp.now(tz=latest.tz)
    age_min = int((now - latest).total_seconds() // 60)
    fresh = age_min <= FRESH_LIMIT_MIN
    st.caption(
        f"{'🟢' if fresh else '🟠'} 最終更新 {latest.strftime('%H:%M')}(約{max(age_min,0)}分前)"
        + ("" if fresh else " — 古い可能性。ポーラー稼働を確認")
    )

    operating, stopped = board.split_operating(b)

    st.markdown(f"#### 運営中({len(operating)})— 待ち短い順")
    st.caption(
        f"**着N分** = 今から{arrival_min}分後に着いた時の予測(群衆補正込み)。"
        "↗=着く頃は混む(今の低さは罠) / ↘=待てば空く"
    )
    if operating.empty:
        st.caption("運営中のアトラクションがありません。")
    else:
        html = "".join(_row(r) for _, r in operating.iterrows())
        st.markdown(html, unsafe_allow_html=True)

    if not stopped.empty:
        with st.expander(f"🛑 停止・休止・改修({len(stopped)})"):
            html = "".join(_row(r) for _, r in stopped.iterrows())
            st.markdown(html, unsafe_allow_html=True)

    st.divider()
    st.caption("データ元: ThemeParks.wiki(非公式・私的利用)")


def main() -> None:
    args = _parse_args()
    render(
        args.db, self_poll=args.self_poll, interval=args.interval, jitter=args.jitter
    )


if __name__ == "__main__":
    main()
