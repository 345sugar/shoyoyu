"""Display short, evidence-backed readings of the selected park records."""

from html import escape

import streamlit as st

from sabotage.analysis.insights import Insight
from sabotage.viz.theme import section_intro


def _select_pair(left: str, right: str) -> None:
    st.session_state["corr_left"] = left
    st.session_state["corr_right"] = right


def _card(item: Insight, number: int, *, select_pairs: bool) -> None:
    label = "READING NOTE" if item.kind in {"insufficient", "no_pattern"} else "A LITTLE INSIGHT"
    st.markdown(
        '<article class="mpm-insight">'
        f'<div class="mpm-insight-label">{number:02d} / {label}</div>'
        f'<h3>{escape(item.title)}</h3>'
        f'<p class="mpm-insight-observation">{escape(item.observation)}</p>'
        '<div class="mpm-insight-next"><span>楽しみ方のヒント</span>'
        f'<p>{escape(item.suggestion)}</p></div>'
        f'<p class="mpm-insight-evidence">根拠：{escape(item.evidence)}</p></article>',
        unsafe_allow_html=True,
    )
    if select_pairs and item.left and item.right:
        st.button(
            f"ヒント {number:02d} の2項目を選ぶ",
            key=f"insight_pair_{number}",
            on_click=_select_pair, args=(item.left, item.right),
            help="下の「ふたつの記録を比べる」に、この組み合わせを設定します。",
        )
        if (st.session_state.get("corr_left"), st.session_state.get("corr_right")) == (item.left, item.right):
            st.markdown("[選択した2項目のグラフへ ↓](#insight-pair-details)")


def render_insights(items: list[Insight], *, daily: bool = False) -> None:
    section_intro(
        "THE TAKEAWAY" if daily else "READ BETWEEN THE LINES",
        "今日を楽しむヒント" if daily else "記録から見つけるヒント",
        "待ち時間の予測を、心地よい一日の選択に。" if daily
        else "選択した期間の記録を読み解き、次に確かめたいことを見つけます。",
    )
    if not items:
        return
    _card(items[0], 1, select_pairs=not daily)
    if len(items) > 1:
        columns = st.columns(len(items) - 1)
        for number, (column, item) in enumerate(zip(columns, items[1:]), start=2):
            with column:
                _card(item, number, select_pairs=not daily)
    if not daily:
        st.caption("選択中の履歴に見られる探索的な傾向です。今日の混雑や、人の移動・原因を示すものではありません。")
