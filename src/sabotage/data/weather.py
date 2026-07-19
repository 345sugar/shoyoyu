"""天気(Open-Meteo)の取得と正規化。

Phase 2「雨の再配分イベントの予告」の燃料。待ち時間は遅行指標なので、屋外→屋内退避の
再配分が起きる**前に**兆候(降水確率の立ち上がり)を掴みたい。舞浜1点を定期取得して
待ち時間と並べて貯めれば、後で「雨予報の何分後に室内系がどれだけ埋まるか」を学習できる。

- Open-Meteo はキー不要・商用でない私的利用は無償(CLAUDE.md データ倫理に整合)。
- 生レスポンスは丸ごと保存する(正規化スキーマの設計ミスから復旧できる、Phase 0 と同じ思想)。
- フィールド名の決め打ちで KeyError を出さない。欠ければ None。欠測は観測。

実レスポンス形状(Open-Meteo v1 /forecast、実物は fetch-fixtures で確認・採取する):
    {
      "latitude": 35.625, "longitude": 139.875, "timezone": "Asia/Tokyo",
      "current": {"time": "2026-07-19T21:00", "temperature_2m": 28.5,
                  "precipitation": 0.0, "weather_code": 2},
      "hourly": {"time": ["2026-07-19T21:00", ...],
                 "precipitation_probability": [10, 20, ...],
                 "precipitation": [0.0, ...], "temperature_2m": [28.5, ...]}
    }
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..config import (
    HTTP_TIMEOUT_SECONDS,
    MAIHAMA_LAT,
    MAIHAMA_LON,
    OPEN_METEO_BASE,
    USER_AGENT,
)
from .client import FetchResult

# 「まもなく雨」を検知する先読み幅(時間)。この範囲の最大降水確率を warning に使う。
LOOKAHEAD_HOURS = 2


@dataclass
class WeatherReading:
    """正規化済みの1天気観測(weather テーブル1行に対応)。

    precip_prob は「今から LOOKAHEAD_HOURS 時間以内の最大降水確率(%)」。
    これが高い = もうすぐ雨 = 屋内退避で室内系が混む予兆(Phase 2 の再配分イベント)。
    """

    temp_c: float | None
    precip_mm: float | None
    precip_prob: int | None
    weather_code: int | None


def _as_float(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _as_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    return None


def _lookahead_precip_prob(payload: dict[str, Any]) -> int | None:
    """hourly から「今以降 LOOKAHEAD_HOURS 時間の最大降水確率」を取り出す。

    current.time 以降の hourly スロットの precipitation_probability の最大値。
    現在時刻が取れない/hourly が無ければ None。
    """
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return None
    times = hourly.get("time")
    probs = hourly.get("precipitation_probability")
    if not isinstance(times, list) or not isinstance(probs, list):
        return None

    current = payload.get("current")
    now_str = current.get("time") if isinstance(current, dict) else None

    # current.time 以降のインデックスを探す(文字列 ISO8601 は辞書順=時刻順)。
    start = 0
    if isinstance(now_str, str):
        for i, t in enumerate(times):
            if isinstance(t, str) and t >= now_str:
                start = i
                break
        else:
            return None  # すべて過去 = 予報範囲外。

    window = probs[start : start + LOOKAHEAD_HOURS + 1]
    vals = [_as_int(p) for p in window]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def normalize_weather(payload: Any) -> WeatherReading | None:
    """Open-Meteo /forecast ペイロードを WeatherReading へ。想定外の形なら None。"""
    if not isinstance(payload, dict):
        return None
    current = payload.get("current")
    current = current if isinstance(current, dict) else {}
    return WeatherReading(
        temp_c=_as_float(current.get("temperature_2m")),
        precip_mm=_as_float(current.get("precipitation")),
        precip_prob=_lookahead_precip_prob(payload),
        weather_code=_as_int(current.get("weather_code")),
    )


class WeatherClient:
    """Open-Meteo /forecast の最小クライアント。責務は「叩いて FetchResult を返す」だけ。

    ThemeParksClient と同じく、失敗(到達不能・非2xx)は例外にせず FetchResult に載せる。
    欠測を1行として記録するのは呼び出し側(poller/scrape)の仕事。
    """

    def __init__(
        self,
        base_url: str = OPEN_METEO_BASE,
        *,
        lat: float = MAIHAMA_LAT,
        lon: float = MAIHAMA_LON,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        user_agent: str = USER_AGENT,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._lat = lat
        self._lon = lon
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    def fetch_forecast(self) -> FetchResult:
        """GET /forecast — 舞浜の現在天気 + 当日の時間別予報。"""
        import json

        url = f"{self._base_url}/forecast"
        params = {
            "latitude": self._lat,
            "longitude": self._lon,
            "current": "temperature_2m,precipitation,weather_code",
            "hourly": "precipitation_probability,precipitation,temperature_2m",
            "forecast_days": 1,
            "timezone": "Asia/Tokyo",
        }
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            return FetchResult(
                ok=False,
                http_status=0,
                raw_text=json.dumps(
                    {"error": str(exc), "type": type(exc).__name__, "url": url},
                    ensure_ascii=False,
                ),
                error=f"{type(exc).__name__}: {exc}",
            )
        if resp.status_code // 100 != 2:
            return FetchResult(
                ok=False,
                http_status=resp.status_code,
                raw_text=resp.text,
                error=f"HTTP {resp.status_code}",
            )
        return FetchResult(ok=True, http_status=resp.status_code, raw_text=resp.text)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "WeatherClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
