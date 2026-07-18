"""蓄積層のテスト。WALモード・欠測記録・再起動耐性・meta。"""

from __future__ import annotations

from sabotage.data.normalize import Observation
from sabotage.data.storage import STATUS_FETCH_FAILED, Storage, utc_now_iso


def _obs(park="P", entity="e1", name="Ride", wait=10, status="OPERATING"):
    return Observation(
        park_id=park,
        entity_id=entity,
        name=name,
        entity_type="ATTRACTION",
        status=status,
        wait_minutes=wait,
    )


def test_wal_mode_enabled(tmp_path):
    db = tmp_path / "s.db"
    with Storage(db) as st:
        mode = st.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


def test_snapshot_and_observations_roundtrip(tmp_path):
    ts = utc_now_iso()
    with Storage(tmp_path / "s.db") as st:
        sid = st.record_snapshot(
            ts=ts, source="themeparks.wiki", park_id="P", http_status=200, raw_json='{"a":1}'
        )
        assert sid > 0
        n = st.record_observations(
            ts=ts, observations=[_obs(entity="e1"), _obs(entity="e2", wait=None)], snapshot_id=sid
        )
        assert n == 2
        assert st.count("snapshots") == 1
        assert st.count("observations") == 2

        row = st.connection.execute(
            "SELECT raw_json FROM snapshots WHERE id=?", (sid,)
        ).fetchone()
        assert row["raw_json"] == '{"a":1}'  # 生JSONを丸ごと保持。


def test_record_observations_empty_is_noop(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        assert st.record_observations(ts=utc_now_iso(), observations=[]) == 0
        assert st.count("observations") == 0


def test_fetch_failed_writes_one_observation_row(tmp_path):
    ts = utc_now_iso()
    with Storage(tmp_path / "s.db") as st:
        st.record_fetch_failed(
            ts=ts,
            source="themeparks.wiki",
            park_id="P",
            http_status=0,
            raw_json='{"error":"ConnectError"}',
        )
        # 欠測は「生の証跡(snapshot) + FETCH_FAILED 観測1行」。
        assert st.count("snapshots") == 1
        assert st.count("observations") == 1
        row = st.connection.execute(
            "SELECT status, entity_id, wait_minutes, park_id FROM observations"
        ).fetchone()
        assert row["status"] == STATUS_FETCH_FAILED
        assert row["entity_id"] is None
        assert row["wait_minutes"] is None
        assert row["park_id"] == "P"


def test_meta_set_get_and_upsert(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        assert st.get_meta("missing") is None
        st.set_meta("tokyo_parks", "v1")
        assert st.get_meta("tokyo_parks") == "v1"
        st.set_meta("tokyo_parks", "v2")  # upsert で上書き。
        assert st.get_meta("tokyo_parks") == "v2"
        assert st.count("meta") == 1


def test_restart_persists_and_schema_is_idempotent(tmp_path):
    db = tmp_path / "s.db"
    ts = utc_now_iso()
    # 1回目:書いて閉じる。
    with Storage(db) as st:
        sid = st.record_snapshot(
            ts=ts, source="s", park_id="P", http_status=200, raw_json="{}"
        )
        st.record_observations(ts=ts, observations=[_obs()], snapshot_id=sid)
        st.set_meta("k", "v")

    # 2回目:同じファイルを開き直す(=再起動)。スキーマ再作成でも壊れない。
    with Storage(db) as st:
        assert st.count("snapshots") == 1
        assert st.count("observations") == 1
        assert st.get_meta("k") == "v"
        # 追記できる(既存データを壊さない)。
        sid2 = st.record_snapshot(
            ts=utc_now_iso(), source="s", park_id="P", http_status=200, raw_json="{}"
        )
        assert sid2 > sid
        assert st.count("snapshots") == 2


def test_in_memory_db_works_without_wal():
    # :memory: は WAL 不可でも動く(テスト/一時利用向け)。
    with Storage(":memory:") as st:
        st.record_snapshot(ts=utc_now_iso(), source="s", park_id="P", http_status=200, raw_json="{}")
        assert st.count("snapshots") == 1


def test_count_rejects_unknown_table(tmp_path):
    import pytest

    with Storage(tmp_path / "s.db") as st:
        with pytest.raises(ValueError):
            st.count("dropme; --")


def test_utc_now_iso_format():
    s = utc_now_iso()
    assert s.endswith("Z")
    assert "T" in s
    assert "+00:00" not in s
