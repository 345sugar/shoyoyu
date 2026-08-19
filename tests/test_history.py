"""起動時ヒストリ・ブートストラップのテスト(httpx.MockTransport でネットワーク非依存)。"""

from __future__ import annotations

import datetime as dt
import json

import httpx

from sabotage.data import history
from sabotage.data.storage import Storage
from sabotage.tools.backfill import backfill

TDL = history.PARK_IDS[0]


def _live_ndjson(ts: str, park_id: str, wait: int) -> str:
    rec = {
        "ts": ts,
        "park_id": park_id,
        "source": "themeparks.wiki",
        "http_status": 200,
        "raw": {
            "liveData": [
                {
                    "id": "att-1",
                    "name": "Test Attraction",
                    "entityType": "ATTRACTION",
                    "status": "OPERATING",
                    "queue": {"STANDBY": {"waitTime": wait}},
                }
            ]
        },
    }
    return json.dumps(rec, ensure_ascii=False) + "\n"


def _mock_client(available: dict[str, str]) -> httpx.Client:
    """available: URL 末尾(rel path)-> 本文。マッチしないものは 404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        for rel, body in available.items():
            if request.url.path.endswith(rel):
                return httpx.Response(200, text=body)
        return httpx.Response(404, text="")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_date_range_inclusive():
    days = list(history._date_range(dt.date(2026, 7, 18), dt.date(2026, 7, 20)))
    assert days == [dt.date(2026, 7, 18), dt.date(2026, 7, 19), dt.date(2026, 7, 20)]


def test_fetch_history_writes_only_available(tmp_path):
    ds = "2026-07-18"
    rel = f"themeparks/live/{TDL}/{ds}.ndjson"
    client = _mock_client({rel: _live_ndjson(f"{ds}T02:00:00Z", TDL, 30)})
    got = history.fetch_history(
        tmp_path, start=dt.date(2026, 7, 18), end=dt.date(2026, 7, 18), client=client
    )
    assert got == 1
    assert (tmp_path / rel).exists()


def test_bootstrap_skips_when_db_already_full(tmp_path):
    # DB に十分な観測を用意 → フェッチせずスキップ。
    ddir = tmp_path / "seed"
    p = ddir / f"themeparks/live/{TDL}/2026-07-18.ndjson"
    p.parent.mkdir(parents=True)
    p.write_text(_live_ndjson("2026-07-18T02:00:00Z", TDL, 30), encoding="utf-8")
    db = str(tmp_path / "x.db")
    with Storage(db) as s:
        backfill(s, ddir)

    # min_observations=1 なら「足りている」と判断してスキップ。client は呼ばれない。
    called = {"n": 0}

    def handler(request):  # pragma: no cover - 呼ばれないことの確認用
        called["n"] += 1
        return httpx.Response(404)

    res = history.bootstrap_history(
        db, min_observations=1, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert res["skipped"] is True
    assert res["reason"] == "enough-data"
    assert called["n"] == 0


def test_bootstrap_ingests_when_empty(tmp_path):
    ds = "2026-07-18"
    rel = f"themeparks/live/{TDL}/{ds}.ndjson"
    client = _mock_client({rel: _live_ndjson(f"{ds}T02:00:00Z", TDL, 45)})
    db = str(tmp_path / "empty.db")
    res = history.bootstrap_history(
        db,
        min_observations=1500,
        start=dt.date(2026, 7, 18),
        end=dt.date(2026, 7, 18),
        client=client,
    )
    assert res["skipped"] is False
    assert res["observations"] >= 1


def test_bootstrap_handles_no_files(tmp_path):
    # 全部 404 → 取り込むものが無い。落ちずにスキップ扱い。
    client = _mock_client({})
    db = str(tmp_path / "none.db")
    res = history.bootstrap_history(
        db, start=dt.date(2026, 7, 18), end=dt.date(2026, 7, 18), client=client
    )
    assert res["skipped"] is True
    assert res["reason"] == "no-files-fetched"
