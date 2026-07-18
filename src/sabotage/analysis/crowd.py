"""集計 — 待ち時間波形・曜日×時間帯ヒートマップ・人圧マップ。

すべて load_observations() が返す DataFrame を入力に取る純関数。DBにもStreamlitにも
依存しない(テストしやすさのため)。
"""

from __future__ import annotations

import pandas as pd

from .areas import area_for
from .queries import WEEKDAY_ORDER

ATTRACTION = "ATTRACTION"


def _attractions_with_wait(df: pd.DataFrame) -> pd.DataFrame:
    """待ち時間を持つアトラクション行だけに絞る。"""
    if df.empty:
        return df
    m = (df["entity_type"] == ATTRACTION) & df["wait_minutes"].notna()
    return df[m]


def waveform(df: pd.DataFrame, target_date, *, names: list[str] | None = None) -> pd.DataFrame:
    """指定日の待ち時間波形。index=ローカル時刻, columns=アトラクション名, 値=待ち分。

    同一(時刻×名前)が複数あれば平均。names 指定でアトラクションを絞れる。
    """
    sub = _attractions_with_wait(df)
    if sub.empty:
        return pd.DataFrame()
    sub = sub[sub["date"] == target_date]
    if names:
        sub = sub[sub["name"].isin(names)]
    if sub.empty:
        return pd.DataFrame()
    pivot = sub.pivot_table(
        index="ts_local", columns="name", values="wait_minutes", aggfunc="mean"
    )
    return pivot.sort_index()


def weekday_hour_heatmap(df: pd.DataFrame, *, names: list[str] | None = None) -> pd.DataFrame:
    """曜日×時間帯の平均待ち時間。index=曜日(月→日), columns=時, 値=平均待ち分。"""
    sub = _attractions_with_wait(df)
    if sub.empty:
        return pd.DataFrame()
    if names:
        sub = sub[sub["name"].isin(names)]
    if sub.empty:
        return pd.DataFrame()
    grid = sub.pivot_table(
        index="weekday", columns="hour", values="wait_minutes", aggfunc="mean"
    )
    # 曜日を月→日で並べ替え(存在する曜日だけ)。
    order = [d for d in WEEKDAY_ORDER if d in grid.index]
    return grid.reindex(order)


def heatmap_long(df: pd.DataFrame, *, names: list[str] | None = None) -> pd.DataFrame:
    """ヒートマップを long 形式(weekday, hour, wait)で返す。Altair 用。"""
    grid = weekday_hour_heatmap(df, names=names)
    if grid.empty:
        return pd.DataFrame(columns=["weekday", "hour", "wait_minutes"])
    long = grid.reset_index().melt(
        id_vars="weekday", var_name="hour", value_name="wait_minutes"
    )
    long["weekday"] = pd.Categorical(
        long["weekday"], categories=[d for d in WEEKDAY_ORDER if d in set(long["weekday"])], ordered=True
    )
    return long


def crowd_pressure(df: pd.DataFrame, target_date) -> pd.DataFrame:
    """人圧指数(1日分)。各時刻での待ち時間総和 = 園内需要の相対指標。

    返り値: columns=[ts_local, pressure]。待ち時間総和は「園内総人数の相対値」の
    雛形(絶対人数ではない)。roadmap の心理設計に従い、生の絶対値ではなく相対で扱う。
    """
    sub = _attractions_with_wait(df)
    if sub.empty:
        return pd.DataFrame(columns=["ts_local", "pressure"])
    sub = sub[sub["date"] == target_date]
    if sub.empty:
        return pd.DataFrame(columns=["ts_local", "pressure"])
    series = sub.groupby("ts_local")["wait_minutes"].sum()
    return series.rename("pressure").reset_index().sort_values("ts_local")


def crowd_pressure_by_area(df: pd.DataFrame, target_date) -> pd.DataFrame:
    """エリア別 人圧(1日分)。long 形式 columns=[ts_local, area, pressure]。"""
    sub = _attractions_with_wait(df)
    if sub.empty:
        return pd.DataFrame(columns=["ts_local", "area", "pressure"])
    sub = sub[sub["date"] == target_date].copy()
    if sub.empty:
        return pd.DataFrame(columns=["ts_local", "area", "pressure"])
    sub["area"] = sub["name"].map(area_for)
    series = sub.groupby(["ts_local", "area"])["wait_minutes"].sum()
    return series.rename("pressure").reset_index().sort_values(["ts_local", "area"])


def current_disruptions(df: pd.DataFrame, target_date) -> pd.DataFrame:
    """指定日に停止(DOWN)/改修(REFURBISHMENT)が観測されたアトラクション一覧。

    木鶏(障害時の平常心)の材料。columns=[name, status, count]。
    """
    if df.empty:
        return pd.DataFrame(columns=["name", "status", "count"])
    sub = df[
        (df["entity_type"] == ATTRACTION)
        & (df["date"] == target_date)
        & (df["status"].isin(["DOWN", "REFURBISHMENT", "CLOSED"]))
    ]
    if sub.empty:
        return pd.DataFrame(columns=["name", "status", "count"])
    out = (
        sub.groupby(["name", "status"]).size().rename("count").reset_index()
    )
    return out.sort_values(["status", "count"], ascending=[True, False])
