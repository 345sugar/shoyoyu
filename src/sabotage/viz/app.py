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
from contextlib import closing
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from sabotage.analysis import correlations, crowd, queries
from sabotage.config import DEFAULT_DB_PATH
from sabotage.tools.seed_demo import DEMO_SOURCE, META_DEMO_FLAG
from sabotage.viz.theme import APP_NAME, apply_theme, hero


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


WEATHER_LABELS = {
    "temp_c": "天気 / 気温 (℃)",
    "precip_mm": "天気 / 降水量 (mm)",
    "precip_prob": "予報 / 今後2時間の降水確率 (%)",
}


def _correlation_chart(corr: pd.DataFrame, counts: pd.DataFrame) -> alt.Chart:
    rows = [
        {"x": left, "y": right, "r": corr.loc[left, right],
         "n": int(counts.loc[left, right])}
        for left in corr.columns for right in corr.columns
    ]
    frame = pd.DataFrame(rows)
    return (
        alt.Chart(frame).mark_rect(cornerRadius=3).encode(
            x=alt.X("x:N", title=None, sort=list(corr.columns),
                    axis=alt.Axis(labelAngle=-45, labelLimit=160)),
            y=alt.Y("y:N", title=None, sort=list(corr.columns),
                    axis=alt.Axis(labelLimit=200)),
            color=alt.Color("r:Q", title="相関 r", scale=alt.Scale(
                domain=[-1, 0, 1], range=["#e09a4a", "#f0eef5", "#51488a"])),
            tooltip=[alt.Tooltip("x:N", title="項目1"), alt.Tooltip("y:N", title="項目2"),
                     alt.Tooltip("r:Q", title="相関 r", format=".2f"),
                     alt.Tooltip("n:Q", title="共通観測 (時間)")],
        ).properties(height=max(250, 35 * len(corr.columns)))
    )


def _scatter_chart(samples: pd.DataFrame, left: str, right: str, *, adjusted: bool) -> alt.Chart:
    frame = samples.rename(columns={left: "x", right: "y"}).copy()
    frame["時刻"] = frame.index.strftime("%Y-%m-%d %H:%M JST")
    suffix = " · 時間帯平均との差" if adjusted else " · 1時間平均"
    return (
        alt.Chart(frame.reset_index(drop=True)).mark_circle(size=65, opacity=.65, color="#6454a4")
        .encode(
            x=alt.X("x:Q", title=left + suffix, scale=alt.Scale(zero=False)),
            y=alt.Y("y:Q", title=right + suffix, scale=alt.Scale(zero=False)),
            tooltip=["時刻:N", alt.Tooltip("x:Q", title=left, format=".2f"),
                     alt.Tooltip("y:Q", title=right, format=".2f")],
        ).properties(height=330).interactive()
    )


def _correlation_section(park_df: pd.DataFrame, weather_df: pd.DataFrame) -> None:
    st.subheader("一緒に混む？ 雨の日は変わる？")
    st.caption("同じ時間に記録されたログを重ねて、待ち時間と天気の関係を探します。")
    days = queries.available_dates(park_df)
    period = st.date_input(
        "分析する期間", value=(days[-1], days[0]), min_value=days[-1], max_value=days[0],
        key=f"corr_period_{park_df['park_id'].iloc[0]}",
    )
    if not isinstance(period, (tuple, list)) or len(period) != 2:
        st.info("開始日と終了日を選んでください。")
        return
    observations = park_df[park_df["date"].between(period[0], period[1])]
    waits = correlations.hourly_waits(observations)
    if waits.empty:
        st.info("選んだ期間に、運営中の待ち時間データがありません。")
        return
    names = list(waits.columns)
    selected = st.multiselect("比べるアトラクション", names, default=names[:6],
                              key=f"corr_rides_{park_df['park_id'].iloc[0]}")
    if len(selected) > 12:
        st.info("グラフを読みやすくするため、12施設以内で選んでください。")
        return
    wide = waits[selected]
    weather = correlations.hourly_weather(weather_df)
    weather_available = not weather.empty and not weather.reindex(waits.index).dropna(how="all").empty
    include_weather = st.checkbox("気温・雨との関係も比べる", value=False,
                                  disabled=not weather_available)
    if include_weather and weather_available:
        available = weather.dropna(axis=1, how="all").rename(columns=WEATHER_LABELS)
        wide = wide.join(available, how="left")
        st.caption("降水確率は取得時点から今後2時間の最大予報です。実際に降った雨とは区別して見てください。")
    elif not weather_available:
        st.caption("この期間の天気ログはありません。待ち時間どうしを比較できます。")
    adjust = st.checkbox("時間帯の影響を調整する", value=True,
                         help="共通観測だけで各時間帯の平均を引きます。昼の混雑などの影響を抑えます。")
    min_pairs = st.slider("比較に必要な共通観測数（時間）", 10, 100, 10, 5)
    if len(wide.columns) < 2:
        st.info("比較する項目を2つ以上選んでください。")
        return
    corr, counts, _ = correlations.analyze(wide, adjust_hour=adjust, min_pairs=min_pairs)
    st.markdown("#### 相関マップ")
    st.caption("紫は同じ方向、オレンジは逆方向の動き。薄い色は関係が弱く、空白は件数不足・変化なしです。")
    st.altair_chart(_correlation_chart(corr, counts), use_container_width=True)
    if adjust and len(set(wide.index.date)) < 2:
        st.info("時間帯を調整した比較には2日以上のログが必要です。期間を広げるか、調整を外してください。")

    st.markdown("#### 組み合わせを詳しく")
    col1, col2 = st.columns(2)
    with col1:
        left = st.selectbox("項目1", list(wide.columns), key="corr_left")
    with col2:
        right = st.selectbox("項目2", [c for c in wide.columns if c != left], key="corr_right")
    raw_corr, _, _ = correlations.analyze(wide[[left, right]], min_pairs=min_pairs)
    adjusted_corr, _, _ = correlations.analyze(wide[[left, right]], adjust_hour=True, min_pairs=min_pairs)
    metrics = st.columns(3)
    raw_r, adjusted_r = raw_corr.loc[left, right], adjusted_corr.loc[left, right]
    metrics[0].metric("そのままの相関", "—" if pd.isna(raw_r) else f"{raw_r:+.2f}")
    metrics[1].metric("時間帯を調整", "—" if pd.isna(adjusted_r) else f"{adjusted_r:+.2f}")
    metrics[2].metric("共通観測", f"{int(counts.loc[left, right])} 時間")
    samples = correlations.paired_samples(wide, left, right, adjust_hour=adjust)
    selected_r = corr.loc[left, right]
    if pd.isna(selected_r):
        st.info("この組み合わせは観測数・日数が足りないか、値に変化がないため相関を表示できません。")
    else:
        st.altair_chart(_scatter_chart(samples, left, right, adjusted=adjust), use_container_width=True)
    with st.expander("計算方法とデータ件数"):
        st.write("1時間の平均値どうしを同時刻で突合したPearson相関です。欠測を補完せず、"
                 "時間帯調整の平均も各ペアに共通する観測だけから計算しています。")
        st.dataframe(counts, use_container_width=True)
    st.caption("相関は因果関係ではありません。曜日・季節・運休・営業時間などの影響は残ります。"
               "連続する時間の記録は独立ではなく、件数が多くても因果を示すものではありません。")


def render(db_path: str) -> None:
    st.set_page_config(page_title=f"{APP_NAME} · Park Stories", page_icon="🐭", layout="wide")
    apply_theme()
    hero("PARK STORIES / ログを振り返る", "パークの一日を振り返って、次のお出かけをもっと気ままに。")

    if not Path(db_path).exists():
        st.error(f"DB が見つかりません: `{db_path}`")
        st.markdown(
            "先にデータを用意してください:\n\n"
            "- 実データ: `sabotage-poll --once --db " + db_path + "`\n"
            "- 合成デモ: `sabotage-seed-demo --db " + db_path + "`"
        )
        return

    with closing(queries.connect(db_path)) as conn:
        _provenance_banner(conn)
        df = queries.load_observations(conn)
        weather_df = queries.load_weather(conn)
        names = queries.park_names(conn)
        parks = queries.available_parks(conn)
    if df.empty:
        st.info("観測データがまだありません(欠測のみ、または空)。")
        return

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
        target_date = st.selectbox("波形を見る日", dates, format_func=str)

        attractions = sorted(
            park_df[(park_df["entity_type"] == "ATTRACTION")]["name"].dropna().unique()
        )
        default_sel = attractions[: min(6, len(attractions))]
        selected = st.multiselect("アトラクション(波形用)", attractions, default=default_sel)

    st.subheader(names.get(park_id, park_id))
    correlation_tab, history_tab = st.tabs(["🔗 相関をみる", "📈 一日のログ"])
    with correlation_tab:
        _correlation_section(park_df, weather_df)
    with history_tab:
        _history_section(park_df, target_date, selected)
    st.divider()
    st.caption("データ元: ThemeParks.wiki / 天気ログがある場合はOpen-Meteo。非公式・私的利用。")


def _history_section(park_df: pd.DataFrame, target_date, selected: list[str]) -> None:
    st.caption(f"{target_date} の記録")

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

def main() -> None:
    render(_db_path_from_args())


# `streamlit run` はスクリプトを __main__ として実行する。import 時には走らない。
if __name__ == "__main__":
    main()
