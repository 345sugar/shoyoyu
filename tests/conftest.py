"""テスト共通のヘルパ。すべてネットワーク非依存(実形状フィクスチャ駆動)。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sabotage.data.client import FetchResult

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def load_fixture_json(name: str):
    return json.loads(load_fixture_text(name))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


class FakeClient:
    """ThemeParksClient の差し替え。実HTTPを一切叩かない。

    live: park_id -> FetchResult のマップ。未登録IDは到達不能(http 0)扱い。
    destinations: FetchResult か None(None なら失敗を返す)。
    raise_on_live: True なら fetch_live で例外を投げる(予期せぬ例外の耐性テスト用)。
    """

    def __init__(
        self,
        *,
        live: dict[str, FetchResult] | None = None,
        destinations: FetchResult | None = None,
        raise_on_live: bool = False,
    ) -> None:
        self.live = live or {}
        self.destinations = destinations
        self.raise_on_live = raise_on_live
        self.calls: dict[str, list | int] = {"live": [], "destinations": 0}

    def fetch_live(self, entity_id: str) -> FetchResult:
        self.calls["live"].append(entity_id)  # type: ignore[union-attr]
        if self.raise_on_live:
            raise RuntimeError("boom (simulated client explosion)")
        if entity_id in self.live:
            return self.live[entity_id]
        return FetchResult(
            ok=False,
            http_status=0,
            raw_text=json.dumps({"error": "unreachable", "type": "ConnectError"}),
            error="ConnectError: unreachable",
        )

    def fetch_destinations(self) -> FetchResult:
        self.calls["destinations"] += 1  # type: ignore[operator]
        if self.destinations is not None:
            return self.destinations
        return FetchResult(
            ok=False, http_status=0, raw_text="{}", error="ConnectError: unreachable"
        )

    def close(self) -> None:  # pragma: no cover - trivial
        pass

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *exc: object) -> None:  # pragma: no cover - trivial
        pass


def ok_result(name: str) -> FetchResult:
    """フィクスチャ名から成功 FetchResult を作る。"""
    return FetchResult(ok=True, http_status=200, raw_text=load_fixture_text(name))
