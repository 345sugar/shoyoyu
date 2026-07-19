"""SQLite 蓄積層。

roadmap 準拠のテーブル:
- snapshots(ts, source, park_id, http_status, raw_json) — 生レスポンスを丸ごと保存
  (正規化スキーマの設計ミスから常に復旧できるように)
- observations(ts, park_id, entity_id, name, entity_type, status, wait_minutes) — 正規化済み
- meta(key, value) — 発見したパークIDのキャッシュ

原則:
- WALモード(並行読み書き・クラッシュ耐性)。
- 取得失敗・欠測も status='FETCH_FAILED' の1行として observations に残す(欠測は観測)。
- スキーマは冪等(再起動しても壊れない)。CREATE TABLE IF NOT EXISTS。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .normalize import Observation
from .weather import WeatherReading

# 欠測(取得失敗・パース失敗)を表す観測ステータス。
STATUS_FETCH_FAILED = "FETCH_FAILED"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    park_id     TEXT    NOT NULL,
    http_status INTEGER NOT NULL,
    raw_json    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    park_id      TEXT    NOT NULL,
    entity_id    TEXT,
    name         TEXT,
    entity_type  TEXT,
    status       TEXT,
    wait_minutes INTEGER,
    snapshot_id  INTEGER REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 天気(Open-Meteo)。舞浜1点の時系列。待ち時間と並べて Phase 2「雨の再配分」に使う。
-- raw_json を同じ行に持つので、正規化列の設計ミスからでも生から復旧できる。
-- 取得失敗は http_status=0・正規化列 NULL の欠測行として残す(欠測は観測)。
CREATE TABLE IF NOT EXISTS weather (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    location_id  TEXT    NOT NULL,
    http_status  INTEGER NOT NULL,
    temp_c       REAL,
    precip_mm    REAL,
    precip_prob  INTEGER,
    weather_code INTEGER,
    raw_json     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_park_ts ON snapshots(park_id, ts);
CREATE INDEX IF NOT EXISTS idx_observations_park_ts ON observations(park_id, ts);
CREATE INDEX IF NOT EXISTS idx_observations_entity_ts ON observations(entity_id, ts);
CREATE INDEX IF NOT EXISTS idx_weather_ts ON weather(ts);
"""


def utc_now_iso() -> str:
    """UTCの ISO8601(秒精度、末尾Z)。全 ts の統一表現。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Storage:
    """SQLite への蓄積。1接続を保持する。with 文で使う。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._init_schema()

    def _configure(self) -> None:
        # WALモード:読み書き並行・クラッシュ耐性。:memory: では WAL 不可なので分岐。
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --- snapshots / observations -------------------------------------------

    def record_snapshot(
        self, *, ts: str, source: str, park_id: str, http_status: int, raw_json: str
    ) -> int:
        """生レスポンスを1行保存し、snapshot id を返す。"""
        cur = self._conn.execute(
            "INSERT INTO snapshots (ts, source, park_id, http_status, raw_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, source, park_id, http_status, raw_json),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def record_observations(
        self, *, ts: str, observations: list[Observation], snapshot_id: int | None = None
    ) -> int:
        """正規化観測をまとめて保存。書いた行数を返す。"""
        rows = [
            (
                ts,
                obs.park_id,
                obs.entity_id,
                obs.name,
                obs.entity_type,
                obs.status,
                obs.wait_minutes,
                snapshot_id,
            )
            for obs in observations
        ]
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT INTO observations "
            "(ts, park_id, entity_id, name, entity_type, status, wait_minutes, snapshot_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def record_fetch_failed(
        self,
        *,
        ts: str,
        source: str,
        park_id: str,
        http_status: int,
        raw_json: str,
    ) -> int:
        """取得失敗を欠測として記録する。

        生の失敗証跡を snapshots に残し、observations に status='FETCH_FAILED' の
        1行を書く(欠測は観測である)。snapshot id を返す。
        """
        snapshot_id = self.record_snapshot(
            ts=ts, source=source, park_id=park_id, http_status=http_status, raw_json=raw_json
        )
        self.record_observations(
            ts=ts,
            observations=[
                Observation(
                    park_id=park_id,
                    entity_id=None,
                    name=None,
                    entity_type=None,
                    status=STATUS_FETCH_FAILED,
                    wait_minutes=None,
                )
            ],
            snapshot_id=snapshot_id,
        )
        return snapshot_id

    # --- weather -------------------------------------------------------------

    def record_weather(
        self,
        *,
        ts: str,
        source: str,
        location_id: str,
        http_status: int,
        raw_json: str,
        reading: WeatherReading | None,
    ) -> int:
        """天気を1行保存する。id を返す。

        reading が None(取得失敗・パース不能・想定外の形)なら正規化列は NULL のまま、
        生 raw_json だけを残す(欠測は観測)。http_status で成否を後から判別できる。
        """
        r = reading
        cur = self._conn.execute(
            "INSERT INTO weather "
            "(ts, source, location_id, http_status, temp_c, precip_mm, precip_prob, "
            " weather_code, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                source,
                location_id,
                http_status,
                r.temp_c if r else None,
                r.precip_mm if r else None,
                r.precip_prob if r else None,
                r.weather_code if r else None,
                raw_json,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # --- meta ----------------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    # --- misc ----------------------------------------------------------------

    def count(self, table: str) -> int:
        if table not in {"snapshots", "observations", "meta", "weather"}:
            raise ValueError(f"unknown table: {table}")
        row = self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
