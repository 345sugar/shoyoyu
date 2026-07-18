"""Streamlit 可視化アプリ(Phase 1)。

起動:
    streamlit run src/sabotage/viz/app.py -- --db data/sabotage.db

DoD「ブラウザで昨日の園内が見える」を満たす3画面:
  1. アトラクション別 待ち時間波形(1日分)
  2. 曜日×時間帯 ヒートマップ
  3. 人圧マップ(待ち時間総和=園内需要の相対指標。全体+エリア別)

データ処理は analysis 層(テスト済み純関数)に委ね、ここは描画に徹する。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from sabotage.analysis import crowd, queries
from sabotage.config import DEFAULT_DB_PATH
from sabotage.tools.seed_demo import DEMO_SOURCE, META_DEMO_FLAG


def _db_path_from_args() -> str:
    """`streamlit run app.py -- --db X` と環境変数 SABOTAGE_DB に対応。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("SABOTAGE_DB", DEFAULT_DB_PATH))
    args, _ = parser.parse_known_args()
    return args.db


def _provenance_banner(conn) -> None:
    """データの出所を明示する。合成データなら目立つ警告を出す。"""
    sources = queries.data_sources(conn)
    demo_flag = conn.execute(
        "SELECT value FROM meta WHERE key=?", (META_DEMO_FLAG,)
    ).fetchone()
    has_real = any(s and s != DEMO_SOURCE for s in sources)
    if demo_flag and not has_real:
        st.warning(
            "⚠️ **合成デモデータ**を表示中です(実際の待ち時間ではありません)。"
            "実データは `sabotage-poll` で取得してください。",
            icon="⚠️",
        )
    elif DEMO_SOURCE in sources and has_real:
        st.info("実データと合成デモデータが混在しています(source で区別可能)。")


def _to_wall_clock(series: pd.Series) -> pd.Series:
    """tz-aware(JST)を naive にして、Vega がブラウザTZへ再変換しないようにする。

    こうしないと軸が現地時間ではなく閲覧者のTZ(UTC等)で表示され、開園09:00が
    01:00 などにずれて見える。値そのものは JST の壁時計を保つ。
    """
    if hasattr(series.dtype, "tz") and series.dtype.tz is not None:
        return series.dt.tz_localize(None)
    return series


def _line_chart(pivot: pd.DataFrame, y_title: str) -> alt.Chart:
    long = pivot.reset_index().melt(id_vars="ts_local", var_name="アトラクション", value_name="wait")
    long["ts_local"] = _to_wall_clock(long["ts_local"])
    return (
        alt.Chart(long)
        .mark_line()
        .encode(
            x=alt.X("ts_local:T", title="時刻(JST)"),
            y=alt.Y("wait:Q", title=y_title),
            color=alt.Color("アトラクション:N", title="アトラクション"),
            tooltip=["アトラクション:N", "wait:Q", "ts_local:T"],
        )
        .properties(height=380)
        .interactive()
    )


def _heatmap_chart(long: pd.DataFrame) -> alt.Chart:
    return (
        alt.Chart(long.dropna(subset=["wait_minutes"]))
        .mark_rect()
        .encode(
            x=alt.X("hour:O", title="時"),
            y=alt.Y("weekday:N", title="曜日", sort=None),
            color=alt.Color("wait_minutes:Q", title="平均待ち(分)", scale=alt.Scale(scheme="orangered")),
            tooltip=["weekday:N", "hour:O", alt.Tooltip("wait_minutes:Q", format=".0f")],
        )
        .properties(height=260)
    )


def _area_pressure_chart(long: pd.DataFrame) -> alt.Chart:
    long = long.copy()
    long["ts_local"] = _to_wall_clock(long["ts_local"])
    return (
        alt.Chart(long)
        .mark_area()
        .encode(
            x=alt.X("ts_local:T", title="時刻(JST)"),
            y=alt.Y("pressure:Q", title="人圧(待ち時間総和)", stack=True),
            color=alt.Color("area:N", title="エリア"),
            tooltip=["area:N", "pressure:Q", "ts_local:T"],
        )
        .properties(height=340)
        .interactive()
    )


def render(db_path: str) -> None:
    st.set_page_config(page_title="sabotage — 昨日の園内", page_icon="🏰", layout="wide")
    st.title("🏰 sabotage — 昨日の園内")
    st.caption("東京ディズニーリゾートの待ち時間ログ可視化(Phase 1)")

    if not Path(db_path).exists():
        st.error(f"DB が見つかりません: `{db_path}`")
        st.markdown(
            "先にデータを用意してください:\n\n"
            "- 実データ: `sabotage-poll --once --db " + db_path + "`\n"
            "- 合成デモ: `sabotage-seed-demo --db " + db_path + "`"
        )
        return

    conn = queries.connect(db_path)
    _provenance_banner(conn)

    df = queries.load_observations(conn)
    if df.empty:
        st.info("観測データがまだありません(欠測のみ、または空)。")
        return

    names = queries.park_names(conn)
    parks = queries.available_parks(conn)
    if not parks:
        st.info("表示できるパークがありません。")
        return

    # --- サイドバー:パーク・日付・アトラクション選択 ---
    with st.sidebar:
        st.header("表示設定")
        park_id = st.selectbox(
            "パーク", parks, format_func=lambda p: names.get(p, p)
        )
        park_df = df[df["park_id"] == park_id]
        dates = queries.available_dates(park_df)
        if not dates:
            st.info("この日付に観測がありません。")
            return
        target_date = st.selectbox("日付", dates, format_func=str)

        attractions = sorted(
            park_df[(park_df["entity_type"] == "ATTRACTION")]["name"].dropna().unique()
        )
        default_sel = attractions[: min(6, len(attractions))]
        selected = st.multiselect("アトラクション(波形用)", attractions, default=default_sel)

    st.subheader(f"{names.get(park_id, park_id)} — {target_date}")

    # --- 1. 待ち時間波形 ---
    st.markdown("### ⏱ 待ち時間波形(選択日)")
    wave = crowd.waveform(park_df, target_date, names=selected or None)
    if wave.empty:
        st.info("この日の待ち時間データがありません。")
    else:
        st.altair_chart(_line_chart(wave, "待ち時間(分)"), use_container_width=True)

    # --- 停止/改修(木鶏の材料) ---
    disruptions = crowd.current_disruptions(park_df, target_date)
    if not disruptions.empty:
        with st.expander(f"🛑 この日の停止・改修・休止({len(disruptions)}件)"):
            st.dataframe(disruptions, use_container_width=True, hide_index=True)

    # --- 2. 曜日×時間帯ヒートマップ ---
    st.markdown("### 📅 曜日 × 時間帯 ヒートマップ(全期間平均)")
    heat = crowd.heatmap_long(park_df, names=selected or None)
    if heat.empty:
        st.info("ヒートマップに十分なデータがありません。")
    else:
        st.altair_chart(_heatmap_chart(heat), use_container_width=True)

    # --- 3. 人圧マップ ---
    st.markdown("### 🌊 人圧マップ(待ち時間総和=園内需要の相対指標)")
    st.caption("絶対人数ではなく相対値。エリア別に「どこが厚いか」を見る(網を張る位置の目安)。")
    by_area = crowd.crowd_pressure_by_area(park_df, target_date)
    if by_area.empty:
        st.info("人圧を計算できるデータがありません。")
    else:
        st.altair_chart(_area_pressure_chart(by_area), use_container_width=True)
        total = crowd.crowd_pressure(park_df, target_date)
        peak = total.loc[total["pressure"].idxmax()] if not total.empty else None
        if peak is not None:
            st.metric(
                "人圧ピーク時刻",
                pd.Timestamp(peak["ts_local"]).strftime("%H:%M"),
                help="この日の待ち時間総和が最大だった時刻。",
            )

    st.divider()
    st.caption(
        "データ元: ThemeParks.wiki(非公式・私的利用)。"
        "Queue-Times のデータは本画面では未使用のため表記は不要。"
    )


def main() -> None:
    render(_db_path_from_args())


# `streamlit run` はスクリプトを __main__ として実行する。import 時には走らない。
if __name__ == "__main__":
    main()
