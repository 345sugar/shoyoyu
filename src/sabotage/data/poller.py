"""ポーラー — データフライホイールの心臓部(Phase 0)。

責務:
- 対象2パークの /live を取得し、生JSONを snapshots に、正規化観測を observations に蓄積。
- 取得失敗・仕様変更(パース不能)は status='FETCH_FAILED' の欠測1行として記録。
- 1サイクル内のどの例外もループを殺さない(24時間無人で回り続ける)。
- パークIDは /destinations から発見して meta にキャッシュ。発見失敗でも既定値で回す。

cron 運用向けに --once(1サイクルで終了)を用意する。常駐は --loop。
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import time
from pathlib import Path
from typing import Any

from ..config import (
    DEFAULT_DB_PATH,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_JITTER_SECONDS,
    DEFAULT_PARKS,
    META_KEY_PARKS,
    MIN_INTERVAL_SECONDS,
    SOURCE_OPEN_METEO,
    SOURCE_THEMEPARKS,
    TOKYO_DESTINATION_SLUG,
    WEATHER_LOCATION_ID,
    Park,
)
from .client import ThemeParksClient
from .normalize import normalize_live
from .storage import Storage, utc_now_iso
from .weather import WeatherClient, normalize_weather

log = logging.getLogger("sabotage.poller")


# --- パーク発見 -------------------------------------------------------------


def _parks_from_destinations(payload: Any) -> list[Park] | None:
    """/destinations ペイロードから東京の2パークを抽出。実形状から確認済み:
    {"destinations": [{id, name, slug, parks: [{id, name}, ...]}, ...]}
    """
    if not isinstance(payload, dict):
        return None
    destinations = payload.get("destinations")
    if not isinstance(destinations, list):
        return None
    for dest in destinations:
        if not isinstance(dest, dict):
            continue
        slug = dest.get("slug")
        name = dest.get("name", "")
        is_tokyo = slug == TOKYO_DESTINATION_SLUG or (
            isinstance(name, str) and "Tokyo Disney" in name
        )
        if not is_tokyo:
            continue
        parks_raw = dest.get("parks")
        if not isinstance(parks_raw, list):
            return None
        parks = [
            Park(park_id=p["id"], name=p.get("name", p["id"]))
            for p in parks_raw
            if isinstance(p, dict) and isinstance(p.get("id"), str)
        ]
        return parks or None
    return None


def discover_parks(client: ThemeParksClient) -> list[Park] | None:
    """/destinations を叩いて東京2パークを発見。失敗時は None。"""
    result = client.fetch_destinations()
    if not result.ok:
        log.warning("destinations 取得失敗: %s", result.error)
        return None
    try:
        payload = result.json()
    except json.JSONDecodeError as exc:
        log.warning("destinations パース失敗: %s", exc)
        return None
    parks = _parks_from_destinations(payload)
    if not parks:
        log.warning("destinations から東京パークを発見できず")
    return parks


def resolve_parks(
    storage: Storage, client: ThemeParksClient, *, force_discover: bool = False
) -> list[Park]:
    """対象パークを決める。

    優先順:
    1. force_discover か meta キャッシュ無しなら /destinations で発見 → 成功なら meta に保存。
    2. 発見できなければ meta キャッシュ。
    3. それも無ければ config の既定値(必ず何かを返し、ループを止めない)。
    """
    cached = storage.get_meta(META_KEY_PARKS)

    if force_discover or cached is None:
        discovered = discover_parks(client)
        if discovered:
            storage.set_meta(
                META_KEY_PARKS,
                json.dumps([{"park_id": p.park_id, "name": p.name} for p in discovered]),
            )
            log.info("パーク発見: %s", ", ".join(p.name for p in discovered))
            return discovered

    if cached is not None:
        try:
            items = json.loads(cached)
            parks = [Park(park_id=i["park_id"], name=i["name"]) for i in items]
            if parks:
                return parks
        except (json.JSONDecodeError, KeyError, TypeError):
            log.warning("meta のパークキャッシュが壊れている。既定値へフォールバック")

    log.info("既定パークを使用(発見なし)")
    return list(DEFAULT_PARKS)


# --- 取得サイクル -----------------------------------------------------------


def _looks_like_live(payload: Any) -> bool:
    """/live の最低限の形(liveData: list)を満たすか。仕様変更検知用。"""
    return isinstance(payload, dict) and isinstance(payload.get("liveData"), list)


def poll_park(storage: Storage, client: ThemeParksClient, park: Park, *, ts: str) -> str:
    """1パークを取得し蓄積する。結果種別の文字列を返す(ログ/テスト用)。

    例外はここで吸収し、欠測として記録する。呼び出し側へは伝播させない。
    """
    try:
        result = client.fetch_live(park.park_id)

        if not result.ok:
            storage.record_fetch_failed(
                ts=ts,
                source=SOURCE_THEMEPARKS,
                park_id=park.park_id,
                http_status=result.http_status,
                raw_json=result.raw_text,
            )
            log.warning("[%s] 取得失敗 → FETCH_FAILED: %s", park.name, result.error)
            return "fetch_failed"

        # 生レスポンスは何があっても丸ごと残す。
        try:
            payload = result.json()
        except json.JSONDecodeError as exc:
            storage.record_fetch_failed(
                ts=ts,
                source=SOURCE_THEMEPARKS,
                park_id=park.park_id,
                http_status=result.http_status,
                raw_json=result.raw_text,
            )
            log.warning("[%s] JSONパース失敗 → FETCH_FAILED: %s", park.name, exc)
            return "parse_failed"

        if not _looks_like_live(payload):
            # 2xx だが形が違う = 仕様変更の疑い。生を残しつつ欠測として印を付ける。
            storage.record_fetch_failed(
                ts=ts,
                source=SOURCE_THEMEPARKS,
                park_id=park.park_id,
                http_status=result.http_status,
                raw_json=result.raw_text,
            )
            log.warning("[%s] 想定外の形 → FETCH_FAILED(仕様変更?)", park.name)
            return "unexpected_shape"

        snapshot_id = storage.record_snapshot(
            ts=ts,
            source=SOURCE_THEMEPARKS,
            park_id=park.park_id,
            http_status=result.http_status,
            raw_json=result.raw_text,
        )
        observations = normalize_live(park.park_id, payload)
        n = storage.record_observations(
            ts=ts, observations=observations, snapshot_id=snapshot_id
        )
        log.info("[%s] OK: %d 観測", park.name, n)
        return "ok"

    except Exception as exc:  # noqa: BLE001 — サイクルを殺さないのが最優先。
        # storage への書き込み自体で失敗しても、ログだけ残して次パークへ進む。
        log.exception("[%s] 予期せぬ例外(スキップして続行): %s", park.name, exc)
        try:
            storage.record_fetch_failed(
                ts=ts,
                source=SOURCE_THEMEPARKS,
                park_id=park.park_id,
                http_status=0,
                raw_json=json.dumps(
                    {"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False
                ),
            )
        except Exception:  # noqa: BLE001
            log.exception("[%s] 欠測の記録にも失敗", park.name)
        return "exception"


def poll_weather(storage: Storage, weather_client: WeatherClient, *, ts: str) -> str:
    """舞浜の天気を1回取得し weather テーブルへ蓄積する。結果種別を返す。

    パーク非依存の1点観測なので1サイクルに1回。例外はここで吸収し、欠測として残す
    (欠測は観測)。天気の失敗で待ち時間サイクルを巻き込まない。
    """
    try:
        result = weather_client.fetch_forecast()
        if not result.ok:
            storage.record_weather(
                ts=ts,
                source=SOURCE_OPEN_METEO,
                location_id=WEATHER_LOCATION_ID,
                http_status=result.http_status,
                raw_json=result.raw_text,
                reading=None,
            )
            log.warning("[天気] 取得失敗 → 欠測記録: %s", result.error)
            return "fetch_failed"

        try:
            payload = result.json()
        except json.JSONDecodeError as exc:
            storage.record_weather(
                ts=ts,
                source=SOURCE_OPEN_METEO,
                location_id=WEATHER_LOCATION_ID,
                http_status=result.http_status,
                raw_json=result.raw_text,
                reading=None,
            )
            log.warning("[天気] JSONパース失敗 → 欠測記録: %s", exc)
            return "parse_failed"

        reading = normalize_weather(payload)
        storage.record_weather(
            ts=ts,
            source=SOURCE_OPEN_METEO,
            location_id=WEATHER_LOCATION_ID,
            http_status=result.http_status,
            raw_json=result.raw_text,
            reading=reading,
        )
        if reading is None:
            log.warning("[天気] 想定外の形 → 生のみ記録(仕様変更?)")
            return "unexpected_shape"
        log.info("[天気] OK: %s℃ 降水確率%s%%", reading.temp_c, reading.precip_prob)
        return "ok"
    except Exception as exc:  # noqa: BLE001 — サイクルを殺さない。
        log.exception("[天気] 予期せぬ例外(スキップして続行): %s", exc)
        try:
            storage.record_weather(
                ts=ts,
                source=SOURCE_OPEN_METEO,
                location_id=WEATHER_LOCATION_ID,
                http_status=0,
                raw_json=json.dumps(
                    {"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False
                ),
                reading=None,
            )
        except Exception:  # noqa: BLE001
            log.exception("[天気] 欠測の記録にも失敗")
        return "exception"


def run_once(
    storage: Storage,
    client: ThemeParksClient,
    parks: list[Park],
    *,
    weather_client: WeatherClient | None = None,
) -> dict[str, str]:
    """全パークを1回ずつ取得。パークID→結果種別 の dict を返す。

    weather_client があれば舞浜の天気も1回取得する(結果は "weather" キーに入る)。
    """
    ts = utc_now_iso()
    results: dict[str, str] = {}
    for park in parks:
        results[park.park_id] = poll_park(storage, client, park, ts=ts)
    if weather_client is not None:
        results["weather"] = poll_weather(storage, weather_client, ts=ts)
    return results


def run_forever(
    storage: Storage,
    client: ThemeParksClient,
    parks: list[Park],
    *,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    jitter: int = DEFAULT_JITTER_SECONDS,
    weather_client: WeatherClient | None = None,
    sleep=time.sleep,
) -> None:
    """常駐ループ。interval + [0,jitter] 秒スリープ。Ctrl-C で綺麗に抜ける。"""
    interval = max(interval, MIN_INTERVAL_SECONDS)  # 礼儀:5分を下回らせない。
    log.info("常駐開始: interval=%ds jitter=0..%ds parks=%d", interval, jitter, len(parks))
    try:
        while True:
            try:
                run_once(storage, client, parks, weather_client=weather_client)
            except Exception:  # noqa: BLE001 — 二重の安全網。ループは死なない。
                log.exception("サイクル全体で例外(続行)")
            nap = interval + random.uniform(0, max(0, jitter))
            log.debug("次サイクルまで %.1fs", nap)
            sleep(nap)
    except KeyboardInterrupt:
        log.info("停止シグナル受信。終了する。")


# --- CLI --------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sabotage-poll",
        description="sabotage Phase 0 ポーラー: 東京ディズニー2パークの待ち時間を蓄積する。",
    )
    p.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite保存先(既定: {DEFAULT_DB_PATH})")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--once", action="store_true", help="1サイクルだけ実行して終了(cron向け)"
    )
    mode.add_argument("--loop", action="store_true", help="常駐して回し続ける(既定)")
    p.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="ループ間隔秒(下限300)"
    )
    p.add_argument(
        "--jitter", type=int, default=DEFAULT_JITTER_SECONDS, help="上乗せジッター秒(0..N)"
    )
    p.add_argument(
        "--discover", action="store_true", help="起動時に /destinations でパークIDを再発見する"
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    db_path = Path(args.db)
    with (
        Storage(db_path) as storage,
        ThemeParksClient() as client,
        WeatherClient() as weather_client,
    ):
        parks = resolve_parks(storage, client, force_discover=args.discover)
        if args.once:
            results = run_once(storage, client, parks, weather_client=weather_client)
            log.info("--once 完了: %s", results)
        else:
            run_forever(
                storage,
                client,
                parks,
                interval=args.interval,
                jitter=args.jitter,
                weather_client=weather_client,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
