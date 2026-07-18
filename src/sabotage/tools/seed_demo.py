"""合成デモデータ生成器(⚠️ 本物ではない)。

実API(api.themeparks.wiki)がこの環境では遮断されており実データが無いため、Phase 1 の
可視化を実際に「ブラウザで見る」ために、実形状と同じ観測を人工的に作って DB に入れる。

- snapshots.source は必ず "demo-synthetic"。本物("themeparks.wiki")と混ざらない。
- meta["demo_seeded"]="true" を立て、viz 側で「合成データ」バナーを出せるようにする。
- 実データが入ったら、このデータは source で区別・除外できる(下記 --purge)。

これは分析のロジック検証と DoD 実証のためのものであり、待ち時間は現実の値ではない。
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import DEFAULT_DB_PATH
from ..data.normalize import Observation
from ..data.storage import Storage

DEMO_SOURCE = "demo-synthetic"
META_DEMO_FLAG = "demo_seeded"
JST = ZoneInfo("Asia/Tokyo")

OPEN_HOUR = 9
CLOSE_HOUR = 21
STEP_MINUTES = 5

TDL = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"
TDS = "67b290d5-3478-4f23-b601-2f8fb71ba803"


@dataclass
class DemoRide:
    name: str
    popularity: int  # ピーク時のおおよその待ち分。
    refurb: bool = False  # 終日改修中。
    # (開始時, 終了時) の停止(DOWN)ウィンドウ(ローカル時, 小数可)。
    down_window: tuple[float, float] | None = None


_PARKS: dict[str, tuple[str, list[DemoRide]]] = {
    TDL: (
        "Tokyo Disneyland",
        [
            DemoRide("Enchanted Tale of Beauty and the Beast", 120),
            DemoRide("Pooh's Hunny Hunt", 80),
            DemoRide("Monsters, Inc. Ride & Go Seek!", 70),
            DemoRide("Splash Mountain", 90, down_window=(13.0, 13.75)),
            DemoRide("Big Thunder Mountain", 60),
            DemoRide("Star Tours: The Adventures Continue", 45),
            DemoRide("Haunted Mansion", 40),
            DemoRide("Pirates of the Caribbean", 25),
            DemoRide("Western River Railroad", 15),
            DemoRide("Space Mountain", 0, refurb=True),
        ],
    ),
    TDS: (
        "Tokyo DisneySea",
        [
            DemoRide("Soaring: Fantastic Flight", 110),
            DemoRide("Toy Story Mania!", 85),
            DemoRide("Journey to the Center of the Earth", 60),
            DemoRide("Tower of Terror", 55, down_window=(11.0, 11.5)),
            DemoRide("Indiana Jones Adventure: Temple of the Crystal Skull", 50),
            DemoRide("Nemo & Friends SeaRider", 30),
        ],
    ),
}


def _shape(hour_frac: float) -> float:
    """開園[0]→閉園[1]の混雑シェイプ。昼過ぎにピーク、開閉で0付近。"""
    if hour_frac <= 0 or hour_frac >= 1:
        return 0.05
    base = math.sin(math.pi * hour_frac) ** 0.8  # 中央で最大。
    afternoon_skew = 1.0 + 0.25 * math.sin(math.pi * (hour_frac - 0.15))
    return max(0.05, base * afternoon_skew)


def _wait_for(ride: DemoRide, local_dt: datetime, rng: random.Random) -> int | None:
    """あるローカル時刻の待ち分。停止・改修中は None。"""
    if ride.refurb:
        return None
    hour = local_dt.hour + local_dt.minute / 60.0
    if ride.down_window and ride.down_window[0] <= hour < ride.down_window[1]:
        return None
    frac = (hour - OPEN_HOUR) / (CLOSE_HOUR - OPEN_HOUR)
    raw = ride.popularity * _shape(frac)
    raw *= 1.0 + rng.uniform(-0.15, 0.15)  # ノイズ。
    return max(0, int(round(raw / 5.0)) * 5)  # 5分刻みに丸める。


def _status_for(ride: DemoRide, wait: int | None, local_dt: datetime) -> str:
    if ride.refurb:
        return "REFURBISHMENT"
    hour = local_dt.hour + local_dt.minute / 60.0
    if ride.down_window and ride.down_window[0] <= hour < ride.down_window[1]:
        return "DOWN"
    return "OPERATING"


def _timestamps(day: datetime):
    """その日の開園〜閉園を STEP_MINUTES 刻みで(ローカルaware)。"""
    t = day.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)
    end = day.replace(hour=CLOSE_HOUR, minute=0, second=0, microsecond=0)
    while t <= end:
        yield t
        t += timedelta(minutes=STEP_MINUTES)


def _iso_z(local_dt: datetime) -> str:
    return local_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def seed(storage: Storage, *, days: int = 3, today: datetime | None = None, seed_value: int = 42) -> int:
    """days 日分(本日を含む直近)の合成観測を投入。書いた観測行数を返す。"""
    rng = random.Random(seed_value)
    if today is None:
        today = datetime.now(JST)
    today = today.replace(hour=0, minute=0, second=0, microsecond=0)

    total_obs = 0
    for d in range(days):
        day = today - timedelta(days=(days - 1 - d))
        for park_id, (park_name, rides) in _PARKS.items():
            for local_ts in _timestamps(day):
                ts = _iso_z(local_ts)
                snapshot_id = storage.record_snapshot(
                    ts=ts,
                    source=DEMO_SOURCE,
                    park_id=park_id,
                    http_status=200,
                    raw_json=json.dumps({"demo": True, "park_id": park_id}),
                )
                obs: list[Observation] = []
                for ride in rides:
                    wait = _wait_for(ride, local_ts, rng)
                    obs.append(
                        Observation(
                            park_id=park_id,
                            entity_id=f"demo-{ride.name}",
                            name=ride.name,
                            entity_type="ATTRACTION",
                            status=_status_for(ride, wait, local_ts),
                            wait_minutes=wait,
                        )
                    )
                # PARK 自己エントリ(park 名の解決に使える)。
                obs.append(
                    Observation(
                        park_id=park_id,
                        entity_id=park_id,
                        name=park_name,
                        entity_type="PARK",
                        status="OPERATING",
                        wait_minutes=None,
                    )
                )
                total_obs += storage.record_observations(
                    ts=ts, observations=obs, snapshot_id=snapshot_id
                )
    storage.set_meta(META_DEMO_FLAG, "true")
    return total_obs


def purge(storage: Storage) -> tuple[int, int]:
    """合成データを DB から消す。(消したsnapshot数, observation数)。"""
    conn = storage.connection
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM snapshots WHERE source=?", (DEMO_SOURCE,)
    )]
    n_obs = 0
    if ids:
        qmarks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"DELETE FROM observations WHERE snapshot_id IN ({qmarks})", ids
        )
        n_obs = cur.rowcount
    cur = conn.execute("DELETE FROM snapshots WHERE source=?", (DEMO_SOURCE,))
    conn.execute("DELETE FROM meta WHERE key=?", (META_DEMO_FLAG,))
    conn.commit()
    return cur.rowcount, n_obs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sabotage-seed-demo",
        description="⚠️ 合成デモデータを DB に投入/削除する(本物ではない)。Phase 1 の可視化実証用。",
    )
    p.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite保存先(既定: {DEFAULT_DB_PATH})")
    p.add_argument("--days", type=int, default=3, help="生成する日数(本日を含む直近)")
    p.add_argument("--purge", action="store_true", help="合成データを削除して終了")
    args = p.parse_args(argv)

    with Storage(args.db) as storage:
        if args.purge:
            ns, no = purge(storage)
            print(f"合成データを削除: snapshots={ns}, observations={no}")
            return 0
        n = seed(storage, days=args.days)
        print(f"⚠️ 合成デモデータ投入完了: observations={n} 行 / {args.days}日分 / db={args.db}")
        print("   本物ではありません。実データは `sabotage-poll` で取得してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
