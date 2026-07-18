"""ThemeParks.wiki API クライアント。

httpx の薄いラッパ。責務は「叩いて、生テキストとステータスを返す」だけ。
- 生レスポンスは丸ごと保存できるよう text をそのまま返す(JSONパースの成否と分離)。
- 取得失敗(ネットワーク例外・タイムアウト・非2xx)は例外を投げず FetchResult に載せる。
  欠測を1行として記録するのは呼び出し側(poller)の仕事。ここは判断しない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import (
    API_BASE,
    HTTP_TIMEOUT_SECONDS,
    USER_AGENT,
)


@dataclass
class FetchResult:
    """1回の取得結果。成功/失敗どちらも表現する。"""

    ok: bool
    # HTTPステータス。ネットワーク層で到達すらできなかった場合は 0。
    http_status: int
    # 生レスポンス本文(成功時)または失敗の説明JSON(失敗時)。常に文字列。
    raw_text: str
    # 失敗理由の短い説明(成功時は None)。
    error: str | None = None

    def json(self) -> Any:
        """raw_text をJSONとして解釈する。壊れていれば JSONDecodeError。"""
        return json.loads(self.raw_text)


class ThemeParksClient:
    """ThemeParks.wiki v1 の最小クライアント。"""

    def __init__(
        self,
        base_url: str = API_BASE,
        *,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        user_agent: str = USER_AGENT,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    def _get(self, path: str) -> FetchResult:
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as exc:
            # 到達不能・タイムアウト等。http_status=0 で欠測として扱わせる。
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
            # 非2xx。本文は残す(仕様変更やレート制限の証跡になる)が失敗扱い。
            return FetchResult(
                ok=False,
                http_status=resp.status_code,
                raw_text=resp.text,
                error=f"HTTP {resp.status_code}",
            )

        return FetchResult(ok=True, http_status=resp.status_code, raw_text=resp.text)

    def fetch_live(self, entity_id: str) -> FetchResult:
        """GET /entity/{id}/live — パーク配下全エンティティのライブデータ。"""
        return self._get(f"entity/{entity_id}/live")

    def fetch_destinations(self) -> FetchResult:
        """GET /destinations — 全パーク一覧(パークID発見用)。"""
        return self._get("destinations")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "ThemeParksClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
