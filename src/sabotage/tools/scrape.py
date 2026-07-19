"""git-scraping スクレイパ(暫定フライホイール)。

常時稼働マシンが用意できるまでの繋ぎとして、GitHub Actions の cron から1時間ごとに
両パークの /live を1回取得し、生JSONを NDJSON として `data` ブランチに追記する
(Simon Willison の git scraping 方式)。

- 出力: `<out>/themeparks/live/<park_id>/<UTC日付>.ndjson`
- 1行 = 1スナップショット。列は Phase 0 の snapshots テーブルと同型:
  `{ts, source, park_id, http_status, raw, [ok], [error]}`
  → これがそのまま `tools/backfill` で SQLite に流し込める(後日5分間隔の本命ポーラーへ移行時、
     この git 履歴からバックフィルする)。
- 取得失敗も http_status=0 の行として必ず残す(欠測は観測、Phase 0 と同じ思想)。
- 礼儀(CLAUDE.md):User-Agent 明示、1時間間隔(cron 側で固定)、私的利用の範囲。
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from ..config import (
    DEFAULT_PARKS,
    SOURCE_OPEN_METEO,
    SOURCE_THEMEPARKS,
    WEATHER_LOCATION_ID,
    Park,
)
from ..data.client import FetchResult, ThemeParksClient
from ..data.storage import utc_now_iso
from ..data.weather import WeatherClient

log = logging.getLogger("sabotage.scrape")


def _raw_value(res: FetchResult) -> Any:
    """レスポンス本文を、可能なら構造化 JSON、無理なら文字列で返す。

    構造化して埋めておくと git の差分が読みやすく、backfill も json.loads 不要。
    """
    try:
        return res.json()
    except (json.JSONDecodeError, ValueError):
        return res.raw_text


def build_record(park: Park, res: FetchResult, ts: str) -> dict[str, Any]:
    """1パーク分のスナップショット行(snapshots 同型)を作る。"""
    rec: dict[str, Any] = {
        "ts": ts,
        "source": SOURCE_THEMEPARKS,
        "park_id": park.park_id,
        "http_status": res.http_status,
        "raw": _raw_value(res),
    }
    if not res.ok:
        rec["ok"] = False
        if res.error:
            rec["error"] = res.error
    return rec


def ndjson_path(out_dir: str | Path, park_id: str, ts: str) -> Path:
    """`<out>/themeparks/live/<park_id>/<UTC日付>.ndjson`。日付は ts の先頭10文字。"""
    return Path(out_dir) / "themeparks" / "live" / park_id / f"{ts[:10]}.ndjson"


def weather_ndjson_path(out_dir: str | Path, ts: str) -> Path:
    """`<out>/weather/open-meteo/<UTC日付>.ndjson`。天気は舞浜1点なので park で分けない。"""
    return Path(out_dir) / "weather" / SOURCE_OPEN_METEO / f"{ts[:10]}.ndjson"


def build_weather_record(res: FetchResult, ts: str) -> dict[str, Any]:
    """1回分の天気スナップショット行を作る(backfill が weather テーブルへ流し込める形)。"""
    rec: dict[str, Any] = {
        "ts": ts,
        "source": SOURCE_OPEN_METEO,
        "location_id": WEATHER_LOCATION_ID,
        "http_status": res.http_status,
        "raw": _raw_value(res),
    }
    if not res.ok:
        rec["ok"] = False
        if res.error:
            rec["error"] = res.error
    return rec


def append_record(out_dir: str | Path, rec: dict[str, Any]) -> Path:
    """レコードを該当 NDJSON ファイルへ1行追記する。"""
    path = ndjson_path(out_dir, rec["park_id"], rec["ts"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def scrape_once(
    client: ThemeParksClient,
    parks: list[Park],
    out_dir: str | Path,
    *,
    ts: str | None = None,
) -> list[dict[str, Any]]:
    """全パークを1回取得し NDJSON へ追記。書いたレコードのリストを返す。

    どの例外もクラッシュさせず、欠測(http 0)として記録して続行する。
    """
    ts = ts or utc_now_iso()
    written: list[dict[str, Any]] = []
    for park in parks:
        try:
            res = client.fetch_live(park.park_id)
            rec = build_record(park, res, ts)
        except Exception as exc:  # noqa: BLE001 — 1パークの失敗で全体を止めない。
            log.warning("[%s] 取得中に例外 → 欠測記録: %s", park.name, exc)
            rec = {
                "ts": ts,
                "source": SOURCE_THEMEPARKS,
                "park_id": park.park_id,
                "http_status": 0,
                "raw": {"error": str(exc), "type": type(exc).__name__},
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        path = append_record(out_dir, rec)
        log.info("[%s] http=%s → %s", park.name, rec["http_status"], path)
        written.append(rec)
    return written


def scrape_weather_once(
    client: WeatherClient, out_dir: str | Path, *, ts: str | None = None
) -> dict[str, Any]:
    """舞浜の天気を1回取得し weather NDJSON へ追記。書いたレコードを返す。

    失敗しても例外にせず、欠測(http 0)として記録して続行する(欠測は観測)。
    """
    ts = ts or utc_now_iso()
    try:
        res = client.fetch_forecast()
        rec = build_weather_record(res, ts)
    except Exception as exc:  # noqa: BLE001 — 天気の失敗で全体を止めない。
        log.warning("[天気] 取得中に例外 → 欠測記録: %s", exc)
        rec = {
            "ts": ts,
            "source": SOURCE_OPEN_METEO,
            "location_id": WEATHER_LOCATION_ID,
            "http_status": 0,
            "raw": {"error": str(exc), "type": type(exc).__name__},
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    path = weather_ndjson_path(out_dir, ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.info("[天気] http=%s → %s", rec["http_status"], path)
    return rec


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sabotage-scrape",
        description="git-scraping: 両パークの /live を1回取得し NDJSON へ追記(暫定フライホイール)。",
    )
    p.add_argument("--out", default="data", help="出力ディレクトリ(既定: data)")
    p.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = p.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    ts = utc_now_iso()
    with ThemeParksClient() as client:
        written = scrape_once(client, list(DEFAULT_PARKS), args.out, ts=ts)
    with WeatherClient() as weather_client:
        weather_rec = scrape_weather_once(weather_client, args.out, ts=ts)
    ok = sum(1 for r in written if r["http_status"] == 200)
    wx = "OK" if weather_rec["http_status"] == 200 else "欠測"
    print(
        f"scrape 完了: {len(written)} レコード(HTTP200={ok})+ 天気={wx} → {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
