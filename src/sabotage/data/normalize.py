"""生の /live JSON を observations 行へ正規化する。

実APIの実形状(ThemeParks.wiki v1)から確認したフィールドのみを、防御的に取り出す:
- トップ: {id, name, entityType, timezone, liveData: [...]}
- liveData要素: {id, name, entityType, status?, lastUpdated, queue?, showtimes?,
  operatingHours?, forecast?, ...}
- 待ち時間は queue.STANDBY.waitTime(整数 or null)。他の待ち行列(SINGLE_RIDER,
  RETURN_TIME, PAID_RETURN_TIME, BOARDING_GROUP, PAID_STANDBY)は Phase 0 では
  正規化対象外だが、生JSONは snapshots に丸ごと残るので後から復旧できる。

フィールド名の決め打ちで KeyError を出さない。仕様変更を前提に、欠けていれば None。
正規化に失敗した1要素でパーク全体を落とさない(壊れた要素はスキップし続行)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Observation:
    """正規化済みの1観測(observations テーブル1行に対応)。"""

    park_id: str
    entity_id: str | None
    name: str | None
    entity_type: str | None
    status: str | None
    wait_minutes: int | None


def _extract_standby_wait(entry: dict[str, Any]) -> int | None:
    """queue.STANDBY.waitTime を安全に取り出す。無ければ None。"""
    queue = entry.get("queue")
    if not isinstance(queue, dict):
        return None
    standby = queue.get("STANDBY")
    if not isinstance(standby, dict):
        return None
    wait = standby.get("waitTime")
    if isinstance(wait, bool):  # bool は int のサブクラス。待ち時間ではない。
        return None
    if isinstance(wait, int):
        return wait
    if isinstance(wait, float):
        return int(wait)
    return None


def normalize_live(park_id: str, payload: Any) -> list[Observation]:
    """1パーク分の /live ペイロードを Observation のリストへ。

    payload は fetch_live().json() の結果(dict想定)。想定外の形なら空リスト。
    """
    if not isinstance(payload, dict):
        return []
    live_data = payload.get("liveData")
    if not isinstance(live_data, list):
        return []

    observations: list[Observation] = []
    for entry in live_data:
        if not isinstance(entry, dict):
            continue
        entity_id = entry.get("id")
        observations.append(
            Observation(
                park_id=park_id,
                entity_id=entity_id if isinstance(entity_id, str) else None,
                name=entry.get("name") if isinstance(entry.get("name"), str) else None,
                entity_type=entry.get("entityType")
                if isinstance(entry.get("entityType"), str)
                else None,
                status=entry.get("status") if isinstance(entry.get("status"), str) else None,
                wait_minutes=_extract_standby_wait(entry),
            )
        )
    return observations
