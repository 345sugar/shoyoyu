"""ポーラーのテスト。FakeClient で実HTTPを排除し、実形状フィクスチャで駆動。"""

from __future__ import annotations

import json

import pytest
from conftest import FakeClient, ok_result

from sabotage.config import DEFAULT_PARKS, META_KEY_PARKS, Park
from sabotage.data.client import FetchResult
from sabotage.data.poller import (
    discover_parks,
    poll_park,
    resolve_parks,
    run_forever,
    run_once,
)
from sabotage.data.storage import STATUS_FETCH_FAILED, Storage

TDL = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"
TDS = "67b290d5-3478-4f23-b601-2f8fb71ba803"


def _both_parks_client() -> FakeClient:
    return FakeClient(
        live={TDL: ok_result("tdl_live.json"), TDS: ok_result("tds_live.json")},
        destinations=ok_result("destinations.json"),
    )


# --- run_once / poll_park: 正常系 -------------------------------------------


def test_run_once_success_writes_snapshots_and_observations(tmp_path):
    client = _both_parks_client()
    with Storage(tmp_path / "s.db") as st:
        results = run_once(st, client, list(DEFAULT_PARKS))

    assert results[TDL] == "ok"
    assert results[TDS] == "ok"
    with Storage(tmp_path / "s.db") as st:
        assert st.count("snapshots") == 2
        # 実データ:TDL 37観測 + TDS 34観測。
        assert st.count("observations") == 71
        # FETCH_FAILED は無い。
        n_failed = st.connection.execute(
            "SELECT COUNT(*) FROM observations WHERE status=?", (STATUS_FETCH_FAILED,)
        ).fetchone()[0]
        assert n_failed == 0


def test_poll_park_links_observations_to_snapshot(tmp_path):
    client = _both_parks_client()
    with Storage(tmp_path / "s.db") as st:
        poll_park(st, client, Park(TDL, "Tokyo Disneyland"), ts="2026-07-18T05:20:00Z")
        rows = st.connection.execute(
            "SELECT DISTINCT snapshot_id FROM observations"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["snapshot_id"] is not None


# --- 欠測系:取得失敗・パース不能・仕様変更・予期せぬ例外 --------------------


def test_fetch_failure_records_fetch_failed(tmp_path):
    # live 未登録 → FakeClient が到達不能(http 0)を返す。
    client = FakeClient(live={})
    with Storage(tmp_path / "s.db") as st:
        results = run_once(st, client, list(DEFAULT_PARKS))
        assert results[TDL] == "fetch_failed"
        assert results[TDS] == "fetch_failed"
        assert st.count("snapshots") == 2  # 生の失敗証跡も残る。
        rows = st.connection.execute("SELECT status FROM observations").fetchall()
        assert len(rows) == 2
        assert all(r["status"] == STATUS_FETCH_FAILED for r in rows)
        # 到達不能は http_status=0 で記録。
        snap = st.connection.execute("SELECT http_status FROM snapshots").fetchone()
        assert snap["http_status"] == 0


def test_non_2xx_records_fetch_failed_with_status(tmp_path):
    client = FakeClient(
        live={TDL: FetchResult(ok=False, http_status=429, raw_text="rate limited", error="HTTP 429")}
    )
    with Storage(tmp_path / "s.db") as st:
        res = poll_park(st, client, Park(TDL, "TDL"), ts="2026-07-18T05:20:00Z")
        assert res == "fetch_failed"
        snap = st.connection.execute("SELECT http_status, raw_json FROM snapshots").fetchone()
        assert snap["http_status"] == 429  # 非2xxのステータスを保持。
        assert snap["raw_json"] == "rate limited"


def test_invalid_json_records_parse_failed(tmp_path):
    client = FakeClient(
        live={TDL: FetchResult(ok=True, http_status=200, raw_text="<html>not json</html>")}
    )
    with Storage(tmp_path / "s.db") as st:
        res = poll_park(st, client, Park(TDL, "TDL"), ts="2026-07-18T05:20:00Z")
        assert res == "parse_failed"
        obs = st.connection.execute("SELECT status FROM observations").fetchone()
        assert obs["status"] == STATUS_FETCH_FAILED


def test_unexpected_shape_records_fetch_failed(tmp_path):
    # 2xx だが liveData を欠く(仕様変更疑い)。
    client = FakeClient(live={TDL: ok_result("live_malformed.json")})
    with Storage(tmp_path / "s.db") as st:
        res = poll_park(st, client, Park(TDL, "TDL"), ts="2026-07-18T05:20:00Z")
        assert res == "unexpected_shape"
        obs = st.connection.execute("SELECT status FROM observations").fetchone()
        assert obs["status"] == STATUS_FETCH_FAILED


def test_poll_park_never_raises_even_if_client_explodes(tmp_path):
    client = FakeClient(raise_on_live=True)
    with Storage(tmp_path / "s.db") as st:
        # 例外を投げず、欠測として記録して 'exception' を返す。
        res = poll_park(st, client, Park(TDL, "TDL"), ts="2026-07-18T05:20:00Z")
        assert res == "exception"
        obs = st.connection.execute("SELECT status FROM observations").fetchone()
        assert obs["status"] == STATUS_FETCH_FAILED


def test_poll_park_survives_broken_storage():
    """storage への書き込み自体が壊れても、サイクルを殺さない。"""

    class BrokenStorage:
        def record_fetch_failed(self, **kwargs):
            raise RuntimeError("disk full")

        def record_snapshot(self, **kwargs):
            raise RuntimeError("disk full")

    client = FakeClient(raise_on_live=True)
    # 例外は全て飲み込まれ、'exception' を返す(プロセスは生き残る)。
    res = poll_park(BrokenStorage(), client, Park(TDL, "TDL"), ts="2026-07-18T05:20:00Z")
    assert res == "exception"


# --- パーク発見 / resolve ---------------------------------------------------


def test_discover_parks_from_real_shape():
    client = FakeClient(destinations=ok_result("destinations.json"))
    parks = discover_parks(client)
    assert parks is not None
    ids = {p.park_id for p in parks}
    assert ids == {TDL, TDS}


def test_discover_parks_returns_none_on_failure():
    client = FakeClient(destinations=None)  # 失敗を返す。
    assert discover_parks(client) is None


def test_resolve_parks_discovers_then_caches(tmp_path):
    client = _both_parks_client()
    with Storage(tmp_path / "s.db") as st:
        parks = resolve_parks(st, client)
        assert {p.park_id for p in parks} == {TDL, TDS}
        # meta にキャッシュされた。
        assert st.get_meta(META_KEY_PARKS) is not None
        assert client.calls["destinations"] == 1

        # 2回目はキャッシュを使い、/destinations を叩かない。
        parks2 = resolve_parks(st, client)
        assert {p.park_id for p in parks2} == {TDL, TDS}
        assert client.calls["destinations"] == 1  # 増えていない。


def test_resolve_parks_falls_back_to_defaults(tmp_path):
    # 発見失敗 & キャッシュ無し → 既定値。ループは止めない。
    client = FakeClient(destinations=None)
    with Storage(tmp_path / "s.db") as st:
        parks = resolve_parks(st, client)
        assert {p.park_id for p in parks} == {p.park_id for p in DEFAULT_PARKS}


def test_resolve_parks_recovers_from_corrupt_cache(tmp_path):
    client = FakeClient(destinations=None)
    with Storage(tmp_path / "s.db") as st:
        st.set_meta(META_KEY_PARKS, "{ this is not valid json")
        parks = resolve_parks(st, client)
        # 壊れたキャッシュ → 既定値へフォールバック。
        assert {p.park_id for p in parks} == {p.park_id for p in DEFAULT_PARKS}


def test_resolve_parks_uses_valid_cache_without_discovery(tmp_path):
    client = FakeClient(destinations=None)
    with Storage(tmp_path / "s.db") as st:
        st.set_meta(META_KEY_PARKS, json.dumps([{"park_id": "X", "name": "Cached Park"}]))
        parks = resolve_parks(st, client)
        assert [p.park_id for p in parks] == ["X"]
        assert client.calls["destinations"] == 0  # 発見は試みない。


# --- run_forever ------------------------------------------------------------


def test_run_forever_loops_then_stops_on_interrupt(tmp_path):
    client = _both_parks_client()
    calls = {"sleep": 0}

    def fake_sleep(_seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise KeyboardInterrupt

    with Storage(tmp_path / "s.db") as st:
        # 例外を投げずに綺麗に戻る。
        run_forever(st, client, list(DEFAULT_PARKS), interval=300, jitter=0, sleep=fake_sleep)
        # sleep が2回呼ばれる = run_once が2サイクル走った。
        assert calls["sleep"] == 2
        assert st.count("snapshots") == 4  # 2パーク × 2サイクル。


def test_run_forever_enforces_min_interval(tmp_path, monkeypatch):
    """礼儀:interval が5分未満でも下限300に引き上げる。"""
    client = _both_parks_client()
    captured = {}

    def fake_sleep(seconds):
        captured["seconds"] = seconds
        raise KeyboardInterrupt

    with Storage(tmp_path / "s.db") as st:
        run_forever(st, client, list(DEFAULT_PARKS), interval=5, jitter=0, sleep=fake_sleep)
    assert captured["seconds"] >= 300


# --- CLI main ---------------------------------------------------------------


def test_main_once_end_to_end(tmp_path, monkeypatch):
    import sabotage.data.poller as poller_mod

    client = _both_parks_client()
    monkeypatch.setattr(poller_mod, "ThemeParksClient", lambda *a, **k: client)

    db = tmp_path / "out" / "sabotage.db"
    rc = poller_mod.main(["--once", "--db", str(db)])
    assert rc == 0
    assert db.exists()

    with Storage(db) as st:
        assert st.count("snapshots") == 2
        assert st.count("observations") == 71
