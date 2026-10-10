"""相関の欠測・運休・時間帯交絡をネットワークなしで検証する。"""

import math

import pandas as pd
import pytest

from sabotage.analysis.correlations import (
    WEATHER_COLUMNS,
    analyze,
    hourly_waits,
    hourly_weather,
    paired_samples,
)


def _wide(values, *, start="2026-07-17 09:00", freq="h"):
    return pd.DataFrame(
        values,
        index=pd.date_range(start, periods=len(next(iter(values.values()))), freq=freq, tz="Asia/Tokyo"),
    )


def test_hourly_waits_excludes_invalid_and_stopped_observations():
    waits = pd.DataFrame(
        [
            ("2026-07-17T01:00:00Z", "A", "OPERATING", 20),
            ("2026-07-17T01:05:00Z", "A", "OPERATING", "40"),
            ("2026-07-17T02:00:00Z", "A", "OPERATING", 0),
            ("2026-07-17T02:00:00Z", "B", "DOWN", 80),
            ("2026-07-17T03:00:00Z", "A", "CLOSED", 0),
            ("2026-07-17T03:00:00Z", "A", "OPERATING", -1),
            ("2026-07-17T03:00:00Z", "A", "OPERATING", float("inf")),
            ("bad-date", "A", "OPERATING", 80),
            (123, "A", "OPERATING", 80),
            ("2026-07-17T03:00:00Z", None, "OPERATING", 80),
            ("2026-07-17T03:00:00Z", 5, "OPERATING", 80),
            ("2026-07-17T03:00:00Z", "A", "OPERATING", "bad"),
        ],
        columns=["ts_local", "name", "status", "wait_minutes"],
    )
    original = waits.copy(deep=True)
    result = hourly_waits(waits)
    assert list(result.columns) == ["A"]
    assert result["A"].tolist() == [30, 0]
    assert result.index.hour.tolist() == [10, 11]
    assert str(result.index.tz) == "Asia/Tokyo"
    pd.testing.assert_frame_equal(waits, original)


def test_many_polls_do_not_inflate_pair_counts():
    observations = []
    for day, minutes in [(17, range(0, 60, 5)), (18, [0])]:
        for minute in minutes:
            for name, value in [("A", day), ("B", 2 * day)]:
                observations.append((f"2026-07-{day}T01:{minute:02d}:00Z", name, "OPERATING", value))
    waits = pd.DataFrame(observations, columns=["ts_local", "name", "status", "wait_minutes"])
    corr, counts, _ = analyze(hourly_waits(waits), min_pairs=2)
    assert counts.loc["A", "B"] == 2
    assert corr.loc["A", "B"] == pytest.approx(1)


def test_hourly_weather_uses_successful_finite_values_only():
    weather = pd.DataFrame({
        "ts": ["2026-07-17T01:00Z", "2026-07-17T01:30Z", "2026-07-17T02:00Z", "bad", "2026-07-17T03:00Z"],
        "http_status": [200, "200", 500, 200, 200],
        "temp_c": [20, 24, 99, 99, float("inf")],
        "precip_mm": [0, 2, 99, 99, "bad"],
    })
    result = hourly_weather(weather)
    assert list(result.columns) == WEATHER_COLUMNS
    assert result.index.hour.tolist() == [10, 12]
    assert result.iloc[0]["temp_c"] == 22
    assert result.iloc[0]["precip_mm"] == 1
    assert result["precip_prob"].isna().all()
    assert result.iloc[1].isna().all()


@pytest.mark.parametrize("input_frame", [pd.DataFrame(), pd.DataFrame({"bad": [1]})])
def test_empty_or_missing_required_columns_are_safe(input_frame):
    assert hourly_waits(input_frame).empty
    assert hourly_weather(input_frame).empty
    assert list(hourly_weather(input_frame).columns) == WEATHER_COLUMNS
    corr, counts, adjusted = analyze(hourly_waits(input_frame))
    assert corr.empty and counts.empty and adjusted.empty


def test_pairwise_missingness_does_not_fill_or_interpolate():
    frame = _wide({"A": [1, 2, None, 4, 5], "B": [2, None, 90, 8, 10], "C": [None, 1, 2, None, None]})
    corr, counts, _ = analyze(frame, min_pairs=3)
    assert counts.loc["A", "B"] == 3
    assert counts.loc["A", "C"] == 1
    assert counts.loc["B", "C"] == 1
    assert corr.loc["A", "B"] == pytest.approx(1)
    assert math.isnan(corr.loc["A", "C"])
    pair = paired_samples(frame, "A", "B")
    assert pair["A"].tolist() == [1, 4, 5]
    assert pair["B"].tolist() == [2, 8, 10]


def test_constants_and_insufficient_sample_size_have_no_correlation():
    frame = _wide({"A": [1, 2, 3], "B": [5, 5, 5], "C": [3, 2, 1]})
    corr, counts, _ = analyze(frame, min_pairs=3)
    assert corr.loc["A", "C"] == pytest.approx(-1)
    assert corr.loc["A", "A"] == pytest.approx(1)
    assert corr["B"].isna().all()
    assert counts.loc["B", "B"] == 3
    assert analyze(frame, min_pairs=4)[0].isna().all().all()


def test_hour_adjustment_removes_shared_daily_curve():
    frame = _wide({"A": [10, 20, 30, 10, 20, 30], "B": [20, 40, 60, 20, 40, 60]})
    frame.index = pd.DatetimeIndex([
        f"2026-07-{day} {hour}:00:00+09:00" for day in (17, 18) for hour in (9, 10, 11)
    ])
    assert analyze(frame, min_pairs=3)[0].loc["A", "B"] == pytest.approx(1)
    corr, counts, _ = analyze(frame, adjust_hour=True, min_pairs=3)
    assert counts.loc["A", "B"] == 6
    assert math.isnan(corr.loc["A", "B"])
    assert paired_samples(frame, "A", "B", adjust_hour=True).eq(0).all().all()


def test_hour_adjustment_uses_only_pairwise_common_observations():
    frame = pd.DataFrame(
        {"A": [10, 100, 20, 90, 1_000_000, 0], "B": [20, 200, 10, 210, None, None]},
        index=pd.DatetimeIndex([
            f"2026-07-{day} {hour}:00:00+09:00" for day in (17, 18, 19) for hour in (9, 10)
        ]),
    )
    pair = paired_samples(frame, "A", "B", adjust_hour=True)
    # 3日目のAだけの値が、2列共通の平均を歪めてはいけない。
    assert pair["A"].tolist() == [-5, 5, 5, -5]
    assert pair["B"].tolist() == [5, -5, -5, 5]
    assert analyze(frame, min_pairs=3)[0].loc["A", "B"] > 0.9
    corr, counts, _ = analyze(frame, adjust_hour=True, min_pairs=3)
    assert counts.loc["A", "B"] == 4
    assert corr.loc["A", "B"] == pytest.approx(-1)
    assert corr.loc["A", "B"] == pytest.approx(pair["A"].corr(pair["B"]))


def test_adjusted_one_day_returns_nan():
    frame = _wide({"A": [1, 3, 2], "B": [2, 6, 4]})
    corr, counts, _ = analyze(frame, adjust_hour=True, min_pairs=2)
    assert corr.isna().all().all()
    assert counts.loc["A", "B"] == 3


def test_nonfinite_values_and_bad_dates_do_not_count():
    frame = pd.DataFrame({"A": [1, "2", float("inf"), 4, 999], "B": [2, 4, 6, "bad", 999]},
                         index=["2026-07-17T01:00Z", "2026-07-17T02:00Z", "2026-07-17T03:00Z", "2026-07-17T04:00Z", "invalid"])
    corr, counts, _ = analyze(frame, min_pairs=2)
    assert counts.loc["A", "B"] == 2
    assert corr.loc["A", "B"] == pytest.approx(1)


def test_duplicate_times_do_not_inflate_sample_count():
    frame = _wide({"A": [1, 1, 2], "B": [2, 2, 4]})
    frame.index = pd.DatetimeIndex([frame.index[0], frame.index[0], frame.index[2]])
    assert analyze(frame, min_pairs=2)[1].loc["A", "B"] == 2


@pytest.mark.parametrize("min_pairs", [0, 1, -1, True, 2.5])
def test_invalid_min_pairs_is_rejected(min_pairs):
    with pytest.raises(ValueError, match="min_pairs"):
        analyze(_wide({"A": [1, 2]}), min_pairs=min_pairs)


def test_pair_selection_requires_different_unique_columns():
    frame = _wide({"A": [1, 2], "B": [2, 4]})
    with pytest.raises(ValueError, match="different"):
        paired_samples(frame, "A", "A")
    frame.columns = ["A", "A"]
    with pytest.raises(ValueError, match="unique"):
        analyze(frame)
