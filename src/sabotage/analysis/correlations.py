"""同じ時間帯に観測された待ち時間・天気の探索的な相関。

各時刻を1時間に集約し、取得頻度の違う日にも同じ重みを与える。
欠測を補完せず、停止中の待ち時間を0として扱わない。相関は因果関係や
来園者の移動を証明するものではない。
"""

from __future__ import annotations

from numbers import Number

import pandas as pd

from .queries import DEFAULT_TZ

WEATHER_COLUMNS = ["temp_c", "precip_mm", "precip_prob"]


def _empty(columns: list[str] | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        index=pd.DatetimeIndex([], tz=DEFAULT_TZ, name="ts_local"),
        columns=columns,
        dtype=float,
    )


def _timestamps(values) -> pd.DatetimeIndex:
    """UTC/offset付き日時を東京時間に統一。不正値はNaT、naive日時はUTC。"""
    raw = pd.Series(values)
    # 数値のキー等を1970年からのナノ秒として解釈しない。
    raw = raw.where(~raw.map(lambda value: isinstance(value, Number)))
    parsed = pd.to_datetime(raw, errors="coerce", format="mixed", utc=True)
    return pd.DatetimeIndex(parsed).tz_convert(DEFAULT_TZ).rename("ts_local")


def _finite(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").replace(
        [float("inf"), -float("inf")], float("nan")
    )


def hourly_waits(df: pd.DataFrame) -> pd.DataFrame:
    """運営中・非負・有限の待ち時間を時間ごとに平均した横持ち表を返す。

呼び出し側で対象パーク・期間を絞る。入力のts_localはqueriesが返す
タイムゾーン付き日時を想定する。同じ時間の複数pollは1セルになる。
"""
    required = {"ts_local", "name", "status", "wait_minutes"}
    if not required.issubset(df.columns) or df.empty:
        return _empty()
    work = df.loc[:, list(required)].copy().reset_index(drop=True)
    work["ts_local"] = _timestamps(work["ts_local"]).floor("h")
    work["wait_minutes"] = _finite(work["wait_minutes"])
    valid_names = work["name"].map(
        lambda name: isinstance(name, str) and bool(name.strip())
    )
    work = work[
        work["status"].eq("OPERATING")
        & work["ts_local"].notna()
        & valid_names
        & work["wait_minutes"].ge(0)
    ]
    if work.empty:
        return _empty()
    result = work.pivot_table(
        index="ts_local", columns="name", values="wait_minutes", aggfunc="mean"
    ).sort_index()
    result.columns.name = None
    return result


def hourly_weather(weather_df: pd.DataFrame) -> pd.DataFrame:
    """取得成功した天気だけを1時間平均にする。欠測した項目はNaNのまま。"""
    if not {"ts", "http_status"}.issubset(weather_df.columns) or weather_df.empty:
        return _empty(WEATHER_COLUMNS)
    work = weather_df.copy().reset_index(drop=True)
    work["ts_local"] = _timestamps(work["ts"]).floor("h")
    for column in WEATHER_COLUMNS:
        work[column] = (
            _finite(work[column]) if column in work else float("nan")
        )
    work = work[
        pd.to_numeric(work["http_status"], errors="coerce").eq(200)
        & work["ts_local"].notna()
    ]
    if work.empty:
        return _empty(WEATHER_COLUMNS)
    return work.groupby("ts_local", sort=True)[WEATHER_COLUMNS].mean()


def _clean_wide(wide: pd.DataFrame) -> pd.DataFrame:
    if wide.columns.has_duplicates:
        raise ValueError("Correlation columns must have unique names")
    result = wide.copy()
    result.index = _timestamps(result.index)
    for column in result:
        result[column] = _finite(result[column])
    result = result.loc[result.index.notna()]
    # 同じ時間に複数行ある場合も重複した観測数にはしない。
    return result.groupby(level=0, sort=True).mean()


def _pair(clean: pd.DataFrame, left: str, right: str, adjust_hour: bool) -> pd.DataFrame:
    pair = clean.loc[:, [left, right]].dropna().copy()
    if adjust_hour and not pair.empty:
        pair -= pair.groupby(pair.index.hour).transform("mean")
    return pair


def paired_samples(
    wide: pd.DataFrame, left: str, right: str, *, adjust_hour: bool = False
) -> pd.DataFrame:
    """2列の共通観測を返す。散布図と相関で全く同じサンプルを使う。

時間帯調整では、この2列がともに観測された行だけで各時刻の平均を
求め、各値から差し引く。列名は入力と同じ。片方だけの観測は使わない。
"""
    if left == right:
        raise ValueError("Choose two different columns")
    return _pair(_clean_wide(wide), left, right, adjust_hour)


def analyze(
    wide: pd.DataFrame, *, adjust_hour: bool = False, min_pairs: int = 10
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Pearson相関・ペア別有効観測数・表示用の横持ち表を返す。

min_pairs未満、定数、または時間帯調整で1日以下のペアのrはNaN。
時間帯調整のrはペアごとの共通観測で調整する。第3返り値は列ごとの
残差を概観するための表であり、散布図にはpaired_samplesを使うこと。
連続した時間の観測には自己相関があるため、p値や有意性は計算しない。
"""
    if isinstance(min_pairs, bool) or not isinstance(min_pairs, int) or min_pairs < 2:
        raise ValueError("min_pairs must be an integer of at least 2")
    clean = _clean_wide(wide)
    corr = pd.DataFrame(float("nan"), index=clean.columns, columns=clean.columns)
    counts = pd.DataFrame(0, index=clean.columns, columns=clean.columns, dtype=int)
    for i, left in enumerate(clean.columns):
        for j in range(i, len(clean.columns)):
            right = clean.columns[j]
            pair = _pair(clean, left, right, adjust_hour)
            count = len(pair)
            counts.iat[i, j] = counts.iat[j, i] = count
            if count < min_pairs:
                continue
            if adjust_hour and pair.index.normalize().nunique() < 2:
                continue
            if pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
                continue
            value = pair.iloc[:, 0].corr(pair.iloc[:, 1])
            corr.iat[i, j] = corr.iat[j, i] = value
    adjusted = clean.copy()
    if adjust_hour and not adjusted.empty:
        adjusted -= adjusted.groupby(adjusted.index.hour).transform("mean")
    return corr, counts, adjusted
