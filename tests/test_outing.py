"""候補選択と鮮度の境界を、固定時刻・合成観測で検証する。"""

from __future__ import annotations

import pandas as pd
import pytest

from sabotage.analysis.outing import freshness, select_attractions


def _board():
    return pd.DataFrame(
        [
            ("Far", "B", "OPERATING", 25, 0),
            ("Near", "A", "OPERATING", 10, 30),
            ("Also near", "A", "OPERATING", 10, 15),
            ("Closed", "A", "CLOSED", 0, 0),
            ("Unknown state", "A", "UNKNOWN", 0, 0),
            ("Unknown wait", "A", "OPERATING", None, None),
            ("Bad wait", "A", "OPERATING", -5, 5),
            ("Infinite wait", "A", "OPERATING", float("inf"), 5),
        ],
        columns=["name", "area", "status", "pred_wait", "wait_minutes"],
    )


def test_candidates_use_arrival_wait_and_exclude_unreliable_rows():
    board = _board()
    original = board.copy(deep=True)
    result = select_attractions(board)
    assert result["name"].tolist() == ["Also near", "Near", "Far"]
    pd.testing.assert_frame_equal(board, original)


def test_area_favorites_and_wait_limit_apply_together():
    result = select_attractions(
        _board(), areas=["A"], favorites=["Near", "Far"], favorites_only=True, max_wait=10
    )
    assert result["name"].tolist() == ["Near"]
    assert select_attractions(_board(), max_wait=9).empty


def test_empty_favorites_only_returns_no_candidates():
    assert select_attractions(_board(), favorites_only=True).empty
    assert len(select_attractions(_board(), favorites=["Far"])) == 3


def test_zero_wait_is_valid_and_zero_limit_is_not_ignored():
    board = _board()
    board.loc[0, "pred_wait"] = 0
    assert select_attractions(board, max_wait=0)["name"].tolist() == ["Far"]


def test_same_name_and_prediction_preserve_original_order():
    board = pd.DataFrame(
        {"name": ["Same", "Same"], "status": ["OPERATING"] * 2,
         "pred_wait": [10, 10], "marker": ["first", "second"]}
    )
    assert select_attractions(board)["marker"].tolist() == ["first", "second"]


def test_empty_or_missing_prediction_does_not_create_candidates():
    assert select_attractions(pd.DataFrame()).empty
    assert select_attractions(pd.DataFrame({"name": ["Ride"]})).empty
    assert select_attractions(_board().drop(columns="area"), areas=["A"]).empty


@pytest.mark.parametrize("bad_limit", [-1, float("inf"), float("nan")])
def test_invalid_wait_limit_is_rejected(bad_limit):
    with pytest.raises(ValueError, match="max_wait"):
        select_attractions(_board(), max_wait=bad_limit)


def test_freshness_boundary_includes_exactly_fifteen_minutes():
    now = pd.Timestamp("2026-10-10T12:00:00+09:00")
    assert freshness("2026-10-10T02:45:00Z", now=now) == {
        "fresh": True, "age_minutes": 15.0, "reason": "fresh"
    }
    stale = freshness("2026-10-10T11:44:59+09:00", now=now)
    assert stale["fresh"] is False
    assert stale["reason"] == "stale"


def test_future_timestamp_beyond_clock_tolerance_is_not_fresh():
    now = "2026-10-10T12:00:00+09:00"
    assert freshness("2026-10-10T12:01:00+09:00", now=now)["fresh"] is True
    result = freshness("2026-10-10T12:01:01+09:00", now=now)
    assert result["fresh"] is False
    assert result["reason"] == "future"


@pytest.mark.parametrize("latest", [None, pd.NaT, "invalid"])
def test_missing_or_invalid_observation_is_unknown(latest):
    assert freshness(latest, now="2026-10-10T12:00:00+09:00") == {
        "fresh": False, "age_minutes": None, "reason": "unknown"
    }


def test_naive_timestamp_is_interpreted_as_japan_time():
    assert freshness("2026-10-10 12:00", now="2026-10-10T03:05:00Z")["age_minutes"] == 5


def test_custom_freshness_threshold_and_invalid_reference():
    assert freshness("2026-10-10 12:00", now="2026-10-10 12:06", max_age_minutes=5)["fresh"] is False
    assert freshness("2026-10-10 12:00", now=pd.NaT)["reason"] == "unknown"
    with pytest.raises(ValueError, match="max_age_minutes"):
        freshness("2026-10-10 12:00", max_age_minutes=-1)
