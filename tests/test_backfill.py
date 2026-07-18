"""NDJSON → SQLite バックフィルのテスト。scrape の出力を実際に流し込む。"""

from __future__ import annotations

from conftest import FakeClient, ok_result

from sabotage.config import Park
from sabotage.data.storage import STATUS_FETCH_FAILED, Storage
from sabotage.tools import backfill, scrape

TDL = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"
TDS = "67b290d5-3478-4f23-b601-2f8fb71ba803"


def test_scrape_then_backfill_end_to_end(tmp_path):
    data_dir = tmp_path / "data"
    client = FakeClient(live={TDL: ok_result("tdl_live.json"), TDS: ok_result("tds_live.json")})
    scrape.scrape_once(
        client,
        [Park(TDL, "Tokyo Disneyland"), Park(TDS, "Tokyo DisneySea")],
        data_dir,
        ts="2026-07-18T07:00:00Z",
    )

    with Storage(tmp_path / "s.db") as st:
        stats = backfill.backfill(st, data_dir)
        assert stats["snapshots"] == 2
        # TDL 37 + TDS 34。
        assert stats["observations"] == 71
        assert stats["failed"] == 0
        assert st.count("snapshots") == 2
        assert st.count("observations") == 71


def test_backfill_is_idempotent(tmp_path):
    data_dir = tmp_path / "data"
    client = FakeClient(live={TDL: ok_result("tdl_live.json")})
    scrape.scrape_once(client, [Park(TDL, "TDL")], data_dir, ts="2026-07-18T07:00:00Z")

    with Storage(tmp_path / "s.db") as st:
        first = backfill.backfill(st, data_dir)
        assert first["snapshots"] == 1
        assert st.count("observations") == 37

        # 2回目: 同じ (ts, source, park_id) はスキップ、重複しない。
        second = backfill.backfill(st, data_dir)
        assert second["snapshots"] == 0
        assert second["skipped"] == 1
        assert st.count("snapshots") == 1
        assert st.count("observations") == 37


def test_backfill_records_failure_as_fetch_failed(tmp_path):
    data_dir = tmp_path / "data"
    # 取得失敗(http 0)を含むスクレイプ結果。
    client = FakeClient(live={})  # unreachable → http 0
    scrape.scrape_once(client, [Park(TDL, "TDL")], data_dir, ts="2026-07-18T07:00:00Z")

    with Storage(tmp_path / "s.db") as st:
        stats = backfill.backfill(st, data_dir)
        assert stats["failed"] == 1
        assert stats["snapshots"] == 0
        row = st.connection.execute("SELECT status FROM observations").fetchone()
        assert row["status"] == STATUS_FETCH_FAILED


def test_backfill_skips_broken_lines(tmp_path):
    # 手書きの壊れた NDJSON(壊れ行・ts欠落・正常行)。
    park_dir = tmp_path / "data" / "themeparks" / "live" / TDL
    park_dir.mkdir(parents=True)
    good = (
        '{"ts":"2026-07-18T07:00:00Z","source":"themeparks.wiki","park_id":"%s",'
        '"http_status":200,"raw":{"liveData":[{"id":"e1","name":"A",'
        '"entityType":"ATTRACTION","status":"OPERATING","queue":{"STANDBY":{"waitTime":15}}}]}}'
        % TDL
    )
    lines = [
        "not json at all",
        '{"source":"themeparks.wiki","park_id":"x","http_status":200,"raw":{}}',  # ts欠落
        good,
        "",
    ]
    (park_dir / "2026-07-18.ndjson").write_text("\n".join(lines), encoding="utf-8")

    with Storage(tmp_path / "s.db") as st:
        stats = backfill.backfill(st, tmp_path / "data")
        assert stats["malformed"] == 1  # ts欠落の1件
        assert stats["snapshots"] == 1  # good の1件
        assert st.count("observations") == 1
        obs = st.connection.execute("SELECT name, wait_minutes FROM observations").fetchone()
        assert obs["name"] == "A"
        assert obs["wait_minutes"] == 15
