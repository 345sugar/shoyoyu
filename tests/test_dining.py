"""食事どきナビのテスト(純関数・ネットワーク非依存)。"""

from __future__ import annotations

from sabotage.analysis import dining
from sabotage.analysis.dining import TDL_ID, TDS_ID


# --- 参考データの整合性 ------------------------------------------------------


def test_dataset_is_wellformed():
    assert len(dining.RESTAURANTS) >= 10
    for r in dining.RESTAURANTS:
        assert r.park_id in (TDL_ID, TDS_ID)
        assert r.name and r.area and r.service
        assert isinstance(r.indoor, bool)


def test_both_parks_have_restaurants():
    assert dining.restaurants(TDL_ID)
    assert dining.restaurants(TDS_ID)


def test_restaurants_filters_by_park():
    assert all(r.park_id == TDL_ID for r in dining.restaurants(TDL_ID))


def test_indoor_only_filter():
    indoor = dining.restaurants(TDS_ID, indoor_only=True)
    assert indoor and all(r.indoor for r in indoor)
    # 室内限定は全件以下。
    assert len(indoor) <= len(dining.restaurants(TDS_ID))


# --- meal_timing -------------------------------------------------------------


def test_rain_takes_priority_and_urges_indoor():
    adv = dining.meal_timing(
        current_pressure=10.0,
        pressure_series=[10, 20, 30, 40],
        weather={"precip_prob": 70, "temp_c": 25},
    )
    assert adv.mode == "rain"
    assert adv.indoor_urgent is True


def test_high_pressure_is_eat_time():
    # 現在が分布の最上位 → 混雑ピーク → 食べ時。
    adv = dining.meal_timing(100.0, [10, 20, 30, 40, 50, 100], weather=None)
    assert adv.mode == "eat"


def test_low_pressure_is_ride_time():
    adv = dining.meal_timing(5.0, [5, 20, 30, 40, 50, 100], weather=None)
    assert adv.mode == "ride"


def test_thin_history_is_neutral_not_a_claim():
    # サンプルが少なすぎる → 断言しない。
    adv = dining.meal_timing(50.0, [50], weather=None)
    assert adv.mode == "neutral"
    assert "薄い" in adv.detail


def test_hot_weather_nudges_indoor_even_when_neutral():
    adv = dining.meal_timing(None, [], weather={"temp_c": 33})
    assert adv.indoor_urgent is True


def test_no_weather_is_safe():
    adv = dining.meal_timing(30.0, [10, 20, 30, 40, 50], weather=None)
    assert adv.mode in ("eat", "ride", "neutral")
    assert adv.indoor_urgent is False
