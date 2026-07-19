"""git-scraping で溜めた NDJSON を SQLite にバックフィルする。

`data` ブランチに溜まった `<data>/themeparks/live/<park_id>/<date>.ndjson` を読み、
Phase 0 の Storage(snapshots/observations)へ流し込む。後日、本命の5分間隔ポーラーへ
移行する際、この git 履歴から SQLite を一括再構築するための経路。

- 成功行(http 200 かつ liveData を持つ)→ snapshot + 正規化 observations。
- それ以外(http 0/非200/形状不正)→ record_fetch_failed(欠測1行)。
- 冪等: (ts, source, park_id) が既に snapshots にあればスキップ。再実行しても重複しない。
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Iterator

from ..config import SOURCE_OPEN_METEO, SOURCE_THEMEPARKS, WEATHER_LOCATION_ID
from ..data.normalize import normalize_live
from ..data.storage import Storage
from ..data.weather import normalize_weather

log = logging.getLogger("sabotage.backfill")


def iter_records(data_dir: str | Path) -> Iterator[tuple[Path, int, dict[str, Any]]]:
    """NDJSON を (path, 行番号, レコード) で列挙。壊れた行はスキップして続行。"""
    root = Path(data_dir)
    for path in sorted(root.glob("themeparks/live/*/*.ndjson")):
        with path.open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    log.warning("壊れた行をスキップ %s:%d: %s", path, lineno, exc)
                    continue
                if isinstance(rec, dict):
                    yield path, lineno, rec


def iter_weather_records(data_dir: str | Path) -> Iterator[tuple[Path, int, dict[str, Any]]]:
    """天気 NDJSON を (path, 行番号, レコード) で列挙。壊れた行はスキップ。"""
    root = Path(data_dir)
    for path in sorted(root.glob("weather/*/*.ndjson")):
        with path.open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    log.warning("壊れた行をスキップ %s:%d: %s", path, lineno, exc)
                    continue
                if isinstance(rec, dict):
                    yield path, lineno, rec


def _raw_json_str(raw: Any) -> str:
    """raw を snapshots.raw_json 用の文字列にする。"""
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False)


def backfill(storage: Storage, data_dir: str | Path) -> dict[str, int]:
    """NDJSON を SQLite へ流し込む。統計を返す。"""
    # 既存の (ts, source, park_id) を先読みして冪等性を担保。
    existing: set[tuple[str, str, str]] = {
        (r["ts"], r["source"], r["park_id"])
        for r in storage.connection.execute("SELECT ts, source, park_id FROM snapshots")
    }
    stats = {"snapshots": 0, "observations": 0, "failed": 0, "skipped": 0, "malformed": 0}

    for path, lineno, rec in iter_records(data_dir):
        ts = rec.get("ts")
        park_id = rec.get("park_id")
        source = rec.get("source", SOURCE_THEMEPARKS)
        http = rec.get("http_status", 0)
        if not isinstance(ts, str) or not isinstance(park_id, str):
            log.warning("ts/park_id 欠落をスキップ %s:%d", path, lineno)
            stats["malformed"] += 1
            continue

        key = (ts, source, park_id)
        if key in existing:
            stats["skipped"] += 1
            continue

        raw = rec.get("raw")
        raw_json = _raw_json_str(raw)

        if http == 200 and isinstance(raw, dict) and isinstance(raw.get("liveData"), list):
            snapshot_id = storage.record_snapshot(
                ts=ts, source=source, park_id=park_id, http_status=http, raw_json=raw_json
            )
            observations = normalize_live(park_id, raw)
            storage.record_observations(
                ts=ts, observations=observations, snapshot_id=snapshot_id
            )
            stats["snapshots"] += 1
            stats["observations"] += len(observations)
        else:
            # 欠測(取得失敗・仕様変更疑い)も1行残す。
            storage.record_fetch_failed(
                ts=ts, source=source, park_id=park_id, http_status=http, raw_json=raw_json
            )
            stats["failed"] += 1

        existing.add(key)

    return stats


def backfill_weather(storage: Storage, data_dir: str | Path) -> dict[str, int]:
    """天気 NDJSON を weather テーブルへ流し込む。統計を返す。

    冪等: (ts, source, location_id) が既に weather にあればスキップ。
    成功行(http 200)は正規化して記録、それ以外は生のみの欠測行として残す。
    """
    existing: set[tuple[str, str, str]] = {
        (r["ts"], r["source"], r["location_id"])
        for r in storage.connection.execute(
            "SELECT ts, source, location_id FROM weather"
        )
    }
    stats = {"weather": 0, "weather_failed": 0, "skipped": 0, "malformed": 0}

    for path, lineno, rec in iter_weather_records(data_dir):
        ts = rec.get("ts")
        source = rec.get("source", SOURCE_OPEN_METEO)
        location_id = rec.get("location_id", WEATHER_LOCATION_ID)
        http = rec.get("http_status", 0)
        if not isinstance(ts, str):
            log.warning("ts 欠落をスキップ %s:%d", path, lineno)
            stats["malformed"] += 1
            continue

        key = (ts, source, location_id)
        if key in existing:
            stats["skipped"] += 1
            continue

        raw = rec.get("raw")
        raw_json = _raw_json_str(raw)
        reading = normalize_weather(raw) if http == 200 and isinstance(raw, dict) else None
        storage.record_weather(
            ts=ts,
            source=source,
            location_id=location_id,
            http_status=http,
            raw_json=raw_json,
            reading=reading,
        )
        if reading is not None:
            stats["weather"] += 1
        else:
            stats["weather_failed"] += 1
        existing.add(key)

    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sabotage-backfill",
        description="git-scraping の NDJSON を SQLite(snapshots/observations)へバックフィル。",
    )
    p.add_argument("--data", default="data", help="NDJSON のルートディレクトリ(既定: data)")
    p.add_argument("--db", default="data/sabotage.db", help="SQLite 出力先")
    p.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = p.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    with Storage(args.db) as storage:
        stats = backfill(storage, args.data)
        weather_stats = backfill_weather(storage, args.data)
    print(f"backfill 完了: {stats} 天気={weather_stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
