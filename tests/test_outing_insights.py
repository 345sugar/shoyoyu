"""当日の示唆は、新鮮で有効な到着予測だけを根拠にする。"""

import pandas as pd
import pytest

from sabotage.analysis.outing_insights import outing_insights


def _board(waits):
    return pd.DataFrame({
        "name": [f"Ride {i}" for i in range(len(waits))],
        "status": ["OPERATING"] * len(waits),
        "pred_wait": waits,
        "wait_minutes": [999] * len(waits),
    })


def _hint(frame, **kwargs):
    hints = outing_insights(frame, fresh=kwargs.get("fresh", True), arrival_min=20)
    assert len(hints) == 1
    return hints[0]


def test_large_difference_explains_median_and_shortest_arrival_prediction():
    frame = _board([55, 10, 40])
    original = frame.copy(deep=True)
    hint = _hint(frame)
    assert hint.kind == "choice"
    assert "Ride 1" in hint.observation
    assert "中央値40分" in hint.observation
    assert "差は30分" in hint.observation
    assert "20分後" in hint.evidence
    assert "3件" in hint.evidence
    assert "10〜55分" in hint.evidence
    assert "999" not in str(hint)
    pd.testing.assert_frame_equal(frame, original)


def test_stale_data_does_not_recommend_a_ride_or_compare_old_values():
    hint = _hint(_board([5, 70, 100]), fresh=False)
    assert hint.kind == "insufficient"
    assert "Ride" not in str(hint)
    assert "食事" not in hint.suggestion
    assert "休憩" not in hint.suggestion
    assert "中央値" not in hint.evidence


@pytest.mark.parametrize("frame", [pd.DataFrame(), pd.DataFrame({"pred_wait": [10]})])
def test_empty_or_missing_required_columns_has_no_candidate(frame):
    hint = _hint(frame)
    assert hint.kind == "insufficient"
    assert "0件" in hint.evidence


@pytest.mark.filterwarnings("error")
def test_empty_typed_string_columns_do_not_warn_or_create_candidates():
    frame = pd.DataFrame({
        "name": pd.Series(dtype="str"),
        "status": pd.Series(dtype="str"),
        "pred_wait": pd.Series(dtype="float64"),
    })
    hint = _hint(frame)
    assert hint.kind == "insufficient"
    assert "0件" in hint.evidence


def test_invalid_values_states_and_names_are_excluded():
    frame = _board([10, float("nan"), -1, float("inf"), -float("inf"), "bad", 20, 25, 0, 5])
    frame.loc[6, "status"] = "CLOSED"
    frame.loc[7, "status"] = None
    frame.loc[8, "name"] = None
    frame.loc[9, "name"] = " "
    hint = _hint(frame)
    assert hint.kind == "limited"
    assert "1件" in hint.evidence
    assert "10〜10分" in hint.evidence
    assert "Ride 0" in hint.observation


def test_no_valid_predictions_is_insufficient():
    hint = _hint(_board([None, float("inf"), -3]))
    assert hint.kind == "insufficient"


def test_close_predictions_invite_preference_without_invented_crowding():
    hint = _hint(_board([0, 5, 14]))
    assert hint.kind == "balanced"
    assert "最大差が14分" in hint.observation
    assert "0〜14分" in hint.evidence
    assert "混雑" not in hint.observation


def test_fifteen_minute_difference_is_not_described_as_close():
    hint = _hint(_board([0, 1, 15]))
    assert hint.kind == "compare"


def test_small_choice_set_does_not_claim_a_population_pattern():
    hint = _hint(_board([10, 40]))
    assert hint.kind == "limited"
    assert "2件" in hint.observation


def test_all_long_predictions_prioritize_comfort_limit():
    hint = _hint(_board([60, 80, 100]))
    assert hint.kind == "comfort"
    assert "60分以上" in hint.observation
    assert "気分でなければ" in hint.suggestion
    assert "混雑" not in hint.observation


@pytest.mark.parametrize("arrival", [-1, float("inf"), float("nan")])
def test_invalid_arrival_horizon_is_rejected(arrival):
    with pytest.raises(ValueError, match="arrival_min"):
        outing_insights(_board([10]), fresh=True, arrival_min=arrival)
