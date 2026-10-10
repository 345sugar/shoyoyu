"""提案の根拠が、欠測を埋めない同一ペアの観測と一致することを確かめる。"""

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from sabotage.analysis.correlations import paired_samples
from sabotage.analysis.insights import Insight, correlation_insights


def _frame(*, days=4, hours=12):
    index = pd.DatetimeIndex([
        f"2026-07-{17 + day} {8 + hour:02d}:00:00+09:00"
        for day in range(days) for hour in range(hours)
    ])
    return pd.DataFrame(index=index)


def _related():
    frame = _frame()
    daily = [-3, -1, 1, 3]
    frame["山"] = [50 + hour * 2 + daily[day] for day in range(4) for hour in range(12)]
    frame["船"] = [70 + hour * 3 + daily[day] * 2 for day in range(4) for hour in range(12)]
    frame["汽車"] = [100 - hour * 2 - daily[day] * 3 for day in range(4) for hour in range(12)]
    return frame


def test_shared_hourly_curve_is_highlighted_as_attenuation_not_causation():
    frame = _frame()
    x, y = [-3, -1, 1, 3], [1, -1, -1, 1]
    frame["山"] = [20 + hour * 5 + x[day] for day in range(4) for hour in range(12)]
    frame["船"] = [40 + hour * 5 + y[day] for day in range(4) for hour in range(12)]
    original = frame.copy(deep=True)
    insights = correlation_insights(frame)
    assert [i.kind for i in insights] == ["attenuation"]
    insight = insights[0]
    assert insight.left == "山" and insight.right == "船"
    assert "未調整 r=+0.99" in insight.evidence
    assert "時間帯調整 r=+0.00" in insight.evidence
    assert "48 時間・4 日" in insight.evidence
    assert "散布図" in insight.suggestion
    pd.testing.assert_frame_equal(frame, original)


def test_true_positive_and_negative_survive_hour_adjustment():
    frame = _related()
    insights = correlation_insights(frame)
    assert {i.kind for i in insights} == {"positive", "negative"}
    assert len({frozenset((i.left, i.right)) for i in insights}) == len(insights)
    for insight in insights:
        samples = paired_samples(frame, insight.left, insight.right, adjust_hour=True)
        expected = samples.iloc[:, 0].corr(samples.iloc[:, 1])
        assert f"時間帯調整 r={expected:+.2f}" in insight.evidence
        assert "48 時間・4 日" in insight.evidence
        assert "到着時予測" in insight.suggestion


def test_pair_specific_missingness_does_not_borrow_dates_or_hours():
    frame = _related()
    # 山だけを観測した3・4日目は、船との比較の根拠に数えない。
    frame.loc[frame.index.day >= 19, "船"] = float("nan")
    insights = correlation_insights(frame[["山", "船"]])
    assert insights[0].kind == "insufficient"
    assert "24 時間・2 日" in insights[0].evidence
    assert "3 日以上" in insights[0].evidence


def test_three_dates_with_too_few_shared_hours_are_insufficient():
    frame = _related().iloc[[0, 1, 12, 13, 24, 25]]
    insight = correlation_insights(frame)[0]
    assert insight.kind == "insufficient"
    assert "6 時間・3 日" in insight.evidence


def test_exactly_twenty_four_hours_across_three_dates_can_be_highlighted():
    frame = _related().loc[lambda f: (f.index.day < 20) & (f.index.hour < 16)]
    insight = correlation_insights(frame[["山", "船"]])[0]
    assert insight.kind == "positive"
    assert "24 時間・3 日" in insight.evidence


def test_twenty_four_hours_from_one_date_are_still_insufficient():
    frame = pd.DataFrame(
        {"山": range(24), "船": range(24)},
        index=pd.date_range("2026-07-17", periods=24, freq="h", tz="Asia/Tokyo"),
    )
    insight = correlation_insights(frame)[0]
    assert insight.kind == "insufficient"
    assert "24 時間・1 日" in insight.evidence


def test_duplicate_rows_do_not_inflate_evidence_or_pass_threshold():
    frame = _related().iloc[[0, 1, 12, 13, 24, 25]]
    frame = pd.concat([frame] * 10)
    insight = correlation_insights(frame)[0]
    assert insight.kind == "insufficient"
    assert "6 時間・3 日" in insight.evidence


def test_user_minimum_overrides_default_twenty_four_hours():
    frame = _related()
    assert correlation_insights(frame, min_pairs=48)[0].kind != "insufficient"
    insight = correlation_insights(frame, min_pairs=49)[0]
    assert insight.kind == "insufficient"
    assert "48 時間・4 日" in insight.evidence
    assert "49 時間以上" in insight.evidence


def test_weather_label_keeps_forecast_and_weather_weather_pairs_are_excluded():
    frame = _related()[["山"]]
    forecast = "今後2時間の降水確率（予報）"
    frame[forecast] = frame["山"] * 0.5
    frame["気温"] = frame["山"] * 0.25
    insights = correlation_insights(frame, weather_columns=[forecast, "気温"])
    assert [i.kind for i in insights] == ["weather"]
    assert forecast in insights[0].observation
    assert "山" in (insights[0].left, insights[0].right)
    assert "日付・曜日" in insights[0].suggestion
    weather_only = correlation_insights(frame[[forecast, "気温"]], weather_columns=[forecast, "気温"])
    assert weather_only[0].kind == "insufficient"
    assert weather_only[0].left is None


def test_missing_weather_does_not_hide_observed_ride_findings():
    frame = _related()
    frame["気温"] = float("nan")
    insights = correlation_insights(frame, weather_columns=["気温", "未収集の雨"])
    assert {i.kind for i in insights} == {"positive", "negative"}


def test_priorities_and_three_card_limit():
    frame = _related()
    x, y = [-3, -1, 1, 3], [1, -1, -1, 1]
    frame["広場"] = [20 + hour * 6 + y[day] for day in range(4) for hour in range(12)]
    frame["気温"] = [20 + x[day] for day in range(4) for _ in range(12)]
    insights = correlation_insights(frame, weather_columns=["気温"])
    assert len(insights) == 3
    assert insights[0].kind == "attenuation"
    assert insights[1].kind == "weather"
    assert insights[2].kind in {"positive", "negative"}
    assert len({frozenset((i.left, i.right)) for i in insights}) == 3


@pytest.mark.parametrize("mode", ["constant", "hourly_only", "uncorrelated"])
def test_no_clear_pattern_does_not_invent_a_story(mode):
    frame = _frame()
    x, y = [-3, -1, 1, 3], [1, -1, -1, 1]
    if mode == "constant":
        frame["山"], frame["船"] = 30.0, 40.0
    elif mode == "hourly_only":
        frame["山"] = [10 + hour for _ in range(4) for hour in range(12)]
        frame["船"] = frame["山"] * 2
    else:
        frame["山"] = [40 + x[day] for day in range(4) for _ in range(12)]
        frame["船"] = [40 + y[day] for day in range(4) for _ in range(12)]
    insight = correlation_insights(frame)[0]
    assert insight.kind == "no_pattern"
    assert "関係がないとは限りません" in insight.suggestion
    assert "1 組" in insight.evidence
    assert insight.left is None and insight.right is None


@pytest.mark.parametrize("frame", [pd.DataFrame(), pd.DataFrame({"山": [1, 2]})])
def test_empty_and_one_column_have_honest_fallback(frame):
    insight = correlation_insights(frame)[0]
    assert insight.kind == "insufficient"
    assert "2項目" in insight.observation
    assert "24 時間以上・3 日以上" in insight.evidence


def test_evidence_excludes_nonfinite_values():
    frame = _related()[["山", "船"]].astype(float)
    frame.iloc[0, 0] = float("inf")
    frame.iloc[12, 1] = float("nan")
    insight = correlation_insights(frame)[0]
    assert insight.kind == "positive"
    assert "46 時間・4 日" in insight.evidence


@pytest.mark.parametrize("minimum", [0, True, 2.5])
def test_invalid_minimum_is_rejected(minimum):
    with pytest.raises(ValueError, match="min_pairs"):
        correlation_insights(_related(), min_pairs=minimum)


def test_insight_is_immutable():
    insight = Insight("insufficient", "title", "observation", "suggestion", "evidence")
    with pytest.raises(FrozenInstanceError):
        insight.title = "changed"
