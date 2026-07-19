"""天気(Open-Meteo)の取得・正規化・蓄積・配線のテスト。ネットワーク非依存。"""

from __future__ import annotations

from conftest import FakeWeatherClient, load_fixture_json

from sabotage.data.client import FetchResult
from sabotage.data.storage import Storage
from sabotage.data.weather import normalize_weather


# --- normalize --------------------------------------------------------------


def test_normalize_extracts_current_fields():
    payload = load_fixture_json("open_meteo_forecast.json")
    r = normalize_weather(payload)
    assert r is not None
    assert r.temp_c == 29.4
    assert r.precip_mm == 0.0
    assert r.weather_code == 2


def test_normalize_lookahead_precip_prob_is_window_max():
    # current.time=12:00。今以降 LOOKAHEAD_HOURS(2)時間の最大は 13:00 の 60%。
    payload = load_fixture_json("open_meteo_forecast.json")
    r = normalize_weather(payload)
    assert r is not None
    assert r.precip_prob == 60


def test_normalize_handles_missing_hourly():
    r = normalize_weather({"current": {"temperature_2m": 20}})
    assert r is not None
    assert r.temp_c == 20.0
    assert r.precip_prob is None  # hourly 無し → None。


def test_normalize_rejects_non_dict():
    assert normalize_weather("nope") is None
    assert normalize_weather(None) is None
    assert normalize_weather([1, 2]) is None


def test_normalize_ignores_bool_and_strings():
    # フィールドが想定外の型でも落ちない(欠測扱い)。
    payload = {"current": {"temperature_2m": True, "weather_code": "x"}, "hourly": {}}
    r = normalize_weather(payload)
    assert r is not None
    assert r.temp_c is None
    assert r.weather_code is None


def test_normalize_all_past_hours_returns_none():
    # current.time が hourly の最後より後 = 予報範囲外 → None。
    payload = {
        "current": {"time": "2026-07-19T23:30", "temperature_2m": 24},
        "hourly": {"time": ["2026-07-19T00:00"], "precipitation_probability": [10]},
    }
    r = normalize_weather(payload)
    assert r is not None
    assert r.precip_prob is None


# --- storage ----------------------------------------------------------------


def test_record_weather_persists_reading():
    with Storage(":memory:") as store:
        r = normalize_weather(load_fixture_json("open_meteo_forecast.json"))
        store.record_weather(
            ts="2026-07-19T03:00:00Z",
            source="open-meteo",
            location_id="maihama",
            http_status=200,
            raw_json="{}",
            reading=r,
        )
        assert store.count("weather") == 1
        row = store.connection.execute(
            "SELECT temp_c, precip_prob, http_status FROM weather"
        ).fetchone()
        assert row["temp_c"] == 29.4
        assert row["precip_prob"] == 60
        assert row["http_status"] == 200


def test_record_weather_none_reading_is_missing_row():
    # 取得失敗:正規化列は NULL、生だけ残る(欠測は観測)。
    with Storage(":memory:") as store:
        store.record_weather(
            ts="2026-07-19T03:00:00Z",
            source="open-meteo",
            location_id="maihama",
            http_status=0,
            raw_json='{"error":"boom"}',
            reading=None,
        )
        row = store.connection.execute(
            "SELECT temp_c, precip_prob, http_status, raw_json FROM weather"
        ).fetchone()
        assert row["temp_c"] is None
        assert row["precip_prob"] is None
        assert row["http_status"] == 0
        assert "boom" in row["raw_json"]


# --- poller 配線 -------------------------------------------------------------


def test_poll_weather_ok():
    from sabotage.data.poller import poll_weather

    with Storage(":memory:") as store:
        res = poll_weather(store, FakeWeatherClient(), ts="2026-07-19T03:00:00Z")
        assert res == "ok"
        assert store.count("weather") == 1


def test_poll_weather_records_failure():
    from sabotage.data.poller import poll_weather

    failed = FetchResult(ok=False, http_status=503, raw_text="{}", error="HTTP 503")
    with Storage(":memory:") as store:
        res = poll_weather(
            store, FakeWeatherClient(result=failed), ts="2026-07-19T03:00:00Z"
        )
        assert res == "fetch_failed"
        # 欠測でも1行残る。
        assert store.count("weather") == 1


def test_poll_weather_survives_exception():
    from sabotage.data.poller import poll_weather

    with Storage(":memory:") as store:
        res = poll_weather(
            store, FakeWeatherClient(raise_on_fetch=True), ts="2026-07-19T03:00:00Z"
        )
        assert res == "exception"
        assert store.count("weather") == 1


def test_run_once_includes_weather_when_client_given():
    from conftest import FakeClient, ok_result
    from sabotage.config import Park
    from sabotage.data.poller import run_once

    tdl = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"
    client = FakeClient(live={tdl: ok_result("tdl_live.json")})
    with Storage(":memory:") as store:
        results = run_once(
            store, client, [Park(tdl, "TDL")], weather_client=FakeWeatherClient()
        )
        assert results["weather"] == "ok"
        assert store.count("weather") == 1


def test_run_once_without_weather_client_skips_weather():
    from conftest import FakeClient, ok_result
    from sabotage.config import Park
    from sabotage.data.poller import run_once

    tdl = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"
    client = FakeClient(live={tdl: ok_result("tdl_live.json")})
    with Storage(":memory:") as store:
        results = run_once(store, client, [Park(tdl, "TDL")])
        assert "weather" not in results
        assert store.count("weather") == 0
