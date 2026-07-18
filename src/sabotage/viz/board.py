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
from pathlib import Path

import pandas as pd
import streamlit as st

from sabotage.analysis import board, queries
from sabotage.config import DEFAULT_DB_PATH
from sabotage.tools.seed_demo import DEMO_SOURCE, META_DEMO_FLAG

FRESH_LIMIT_MIN = 15  # これを超えて更新が無ければ「古い」警告。


def _db_path_from_args() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("SABOTAGE_DB", DEFAULT_DB_PATH))
    args, _ = parser.parse_known_args()
    return args.db


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


def _row(r) -> str:
    left = _wait_text(r["status"], r["wait_minutes"])
    meta = " · ".join(x for x in [r.get("area"), _fmt_delta(r.get("delta"))] if x)
    badge = _value_badge(r.get("value_label"))
    return (
        '<div style="display:flex;align-items:center;gap:.6rem;'
        'padding:.45rem 0;border-bottom:1px solid rgba(128,128,128,.2)">'
        f'<div style="min-width:4.2rem;text-align:right">{left}</div>'
        f'<div style="flex:1"><div style="font-weight:600">{r["name"]} {badge}</div>'
        f'<div style="font-size:.78rem;color:#6e7781">{meta}</div></div>'
        "</div>"
    )


def render(db_path: str) -> None:
    st.set_page_config(page_title="sabotage 現況", page_icon="🎢", layout="centered")
    st.title("🎢 現況ボード")

    if not Path(db_path).exists():
        st.error(f"DB が見つかりません: `{db_path}`")
        st.caption("`sabotage-poll --loop` で実データを貯めるか、`sabotage-seed-demo` で試せます。")
        return

    conn = queries.connect(db_path)
    df = queries.load_observations(conn)
    if df.empty:
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

    b = board.current_board(df[df["park_id"] == park_id], park_id)
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
    render(_db_path_from_args())


if __name__ == "__main__":
    main()
