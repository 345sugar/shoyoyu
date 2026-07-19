"""git-scraping スクレイパのテスト。FakeClient で実HTTPを排除。"""

from __future__ import annotations

import json

from conftest import FakeClient, FakeWeatherClient, ok_result

from sabotage.config import Park
from sabotage.data.client import FetchResult
from sabotage.tools import scrape

TDL = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"
TDS = "67b290d5-3478-4f23-b601-2f8fb71ba803"
PARKS = [Park(TDL, "Tokyo Disneyland"), Park(TDS, "Tokyo DisneySea")]
TS = "2026-07-18T07:00:00Z"


def _read_lines(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_scrape_writes_partitioned_ndjson(tmp_path):
    client = FakeClient(live={TDL: ok_result("tdl_live.json"), TDS: ok_result("tds_live.json")})
    written = scrape.scrape_once(client, PARKS, tmp_path, ts=TS)

    assert len(written) == 2
    tdl_file = tmp_path / "themeparks" / "live" / TDL / "2026-07-18.ndjson"
    tds_file = tmp_path / "themeparks" / "live" / TDS / "2026-07-18.ndjson"
    assert tdl_file.exists() and tds_file.exists()

    rec = _read_lines(tdl_file)[0]
    # snapshots 同型の列。
    assert rec["ts"] == TS
    assert rec["source"] == "themeparks.wiki"
    assert rec["park_id"] == TDL
    assert rec["http_status"] == 200
    # raw は構造化 JSON(liveData を持つ実応答)。
    assert isinstance(rec["raw"], dict)
    assert isinstance(rec["raw"]["liveData"], list)
    assert len(rec["raw"]["liveData"]) == 37


def test_scrape_records_failure_as_http_zero(tmp_path):
    # live 未登録 → FakeClient が到達不能(http 0)を返す。
    client = FakeClient(live={})
    written = scrape.scrape_once(client, [Park(TDL, "TDL")], tmp_path, ts=TS)

    rec = written[0]
    assert rec["http_status"] == 0
    assert rec["ok"] is False
    assert "error" in rec
    # 欠測でもファイルに1行残る(欠測は観測)。
    f = tmp_path / "themeparks" / "live" / TDL / "2026-07-18.ndjson"
    assert len(_read_lines(f)) == 1


def test_scrape_survives_client_exception(tmp_path):
    client = FakeClient(raise_on_live=True)
    written = scrape.scrape_once(client, [Park(TDL, "TDL")], tmp_path, ts=TS)
    # 例外でも落ちず、http 0 の欠測として記録。
    assert written[0]["http_status"] == 0
    assert written[0]["ok"] is False


def test_scrape_appends(tmp_path):
    client = FakeClient(live={TDL: ok_result("tdl_live.json")})
    scrape.scrape_once(client, [Park(TDL, "TDL")], tmp_path, ts="2026-07-18T07:00:00Z")
    scrape.scrape_once(client, [Park(TDL, "TDL")], tmp_path, ts="2026-07-18T08:00:00Z")
    f = tmp_path / "themeparks" / "live" / TDL / "2026-07-18.ndjson"
    lines = _read_lines(f)
    assert len(lines) == 2
    assert [ln["ts"] for ln in lines] == [
        "2026-07-18T07:00:00Z",
        "2026-07-18T08:00:00Z",
    ]


def test_scrape_partitions_by_utc_date(tmp_path):
    client = FakeClient(live={TDL: ok_result("tdl_live.json")})
    scrape.scrape_once(client, [Park(TDL, "TDL")], tmp_path, ts="2026-07-18T23:00:00Z")
    scrape.scrape_once(client, [Park(TDL, "TDL")], tmp_path, ts="2026-07-19T00:00:00Z")
    base = tmp_path / "themeparks" / "live" / TDL
    assert (base / "2026-07-18.ndjson").exists()
    assert (base / "2026-07-19.ndjson").exists()


def test_build_record_parse_failure_keeps_string(tmp_path):
    # 2xx だが JSON でない本文 → raw は文字列のまま温存。
    res = FetchResult(ok=True, http_status=200, raw_text="<html>nope</html>")
    rec = scrape.build_record(Park(TDL, "TDL"), res, TS)
    assert rec["raw"] == "<html>nope</html>"
    assert rec["http_status"] == 200


def test_scrape_weather_writes_ndjson(tmp_path):
    rec = scrape.scrape_weather_once(FakeWeatherClient(), tmp_path, ts=TS)
    assert rec["source"] == "open-meteo"
    assert rec["location_id"] == "maihama"
    assert rec["http_status"] == 200
    f = tmp_path / "weather" / "open-meteo" / "2026-07-18.ndjson"
    assert f.exists()
    line = _read_lines(f)[0]
    assert isinstance(line["raw"], dict)
    assert "current" in line["raw"]


def test_scrape_weather_failure_is_http_zero(tmp_path):
    rec = scrape.scrape_weather_once(
        FakeWeatherClient(raise_on_fetch=True), tmp_path, ts=TS
    )
    assert rec["http_status"] == 0
    assert rec["ok"] is False
    f = tmp_path / "weather" / "open-meteo" / "2026-07-18.ndjson"
    assert len(_read_lines(f)) == 1  # 欠測でも1行残る。
