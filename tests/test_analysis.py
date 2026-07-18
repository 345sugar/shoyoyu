"""analysis 層のテスト。決定的な観測を DB に入れて集計を検証(ネットワーク非依存)。"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from sabotage.analysis import crowd, queries
from sabotage.analysis.areas import AREA_UNKNOWN, area_for
from sabotage.data.normalize import Observation
from sabotage.data.storage import STATUS_FETCH_FAILED, Storage

# 2026-07-17 は金曜。JST 10:00 = 01:00Z、11:00 = 02:00Z。
TDL = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"


def _seed(storage: Storage) -> None:
    """2つの時刻 × 2アトラクション + 欠測1件 + 別パーク1件を投入。"""
    rows = [
        # (ts_utc, park, entity, name, type, status, wait)
        ("2026-07-17T01:00:00Z", TDL, "e1", "Pooh's Hunny Hunt", "ATTRACTION", "OPERATING", 30),
        ("2026-07-17T01:00:00Z", TDL, "e2", "Big Thunder Mountain", "ATTRACTION", "OPERATING", 50),
        ("2026-07-17T02:00:00Z", TDL, "e1", "Pooh's Hunny Hunt", "ATTRACTION", "OPERATING", 40),
        ("2026-07-17T02:00:00Z", TDL, "e2", "Big Thunder Mountain", "ATTRACTION", "DOWN", None),
        # PARK 自己エントリ(名前解決用)。
        ("2026-07-17T01:00:00Z", TDL, TDL, "Tokyo Disneyland", "PARK", "OPERATING", None),
        # 欠測(集計から除外されるべき)。
        ("2026-07-17T01:05:00Z", TDL, None, None, None, STATUS_FETCH_FAILED, None),
    ]
    for ts, park, ent, name, etype, status, wait in rows:
        sid = storage.record_snapshot(
            ts=ts, source="themeparks.wiki", park_id=park, http_status=200, raw_json="{}"
        )
        storage.record_observations(
            ts=ts,
            observations=[
                Observation(
                    park_id=park,
                    entity_id=ent,
                    name=name,
                    entity_type=etype,
                    status=status,
                    wait_minutes=wait,
                )
            ],
            snapshot_id=sid,
        )


def test_load_observations_excludes_missing_and_derives_local(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        _seed(st)
        conn = queries.connect(str(tmp_path / "s.db"))
        df = queries.load_observations(conn)

    # 欠測(FETCH_FAILED)は除外。5行(2+2+park の観測)。
    assert (df["status"] == STATUS_FETCH_FAILED).sum() == 0
    assert len(df) == 5
    # JST 変換:01:00Z → 10:00 JST。
    pooh = df[(df["name"] == "Pooh's Hunny Hunt") & (df["hour"] == 10)]
    assert len(pooh) == 1
    assert pooh.iloc[0]["weekday"] == "Friday"
    assert pooh.iloc[0]["date"] == dt.date(2026, 7, 17)


def test_load_observations_empty_has_columns(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        pass
    conn = queries.connect(str(tmp_path / "s.db"))
    df = queries.load_observations(conn)
    assert df.empty
    for col in ("ts_local", "date", "hour", "weekday", "wait_minutes"):
        assert col in df.columns


def test_waveform_pivot(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        _seed(st)
        conn = queries.connect(str(tmp_path / "s.db"))
        df = queries.load_observations(conn, park_id=TDL)

    wave = crowd.waveform(df, dt.date(2026, 7, 17))
    # 2時刻 × 2アトラクション。Big Thunder は DOWN(wait None)なので 02:00 は欠ける。
    assert list(wave.columns) == ["Big Thunder Mountain", "Pooh's Hunny Hunt"]
    assert wave["Pooh's Hunny Hunt"].tolist() == [30, 40]
    # DOWN の時刻は NaN。
    assert pd.isna(wave["Big Thunder Mountain"].iloc[1])


def test_waveform_name_filter(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        _seed(st)
        conn = queries.connect(str(tmp_path / "s.db"))
        df = queries.load_observations(conn, park_id=TDL)

    wave = crowd.waveform(df, dt.date(2026, 7, 17), names=["Pooh's Hunny Hunt"])
    assert list(wave.columns) == ["Pooh's Hunny Hunt"]


def test_crowd_pressure_sums_waits(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        _seed(st)
        conn = queries.connect(str(tmp_path / "s.db"))
        df = queries.load_observations(conn, park_id=TDL)

    pressure = crowd.crowd_pressure(df, dt.date(2026, 7, 17))
    # 10:00 JST: 30 + 50 = 80。11:00 JST: 40(Big Thunder は DOWN で除外)。
    vals = pressure.set_index(pressure["ts_local"].dt.strftime("%H:%M"))["pressure"].to_dict()
    assert vals["10:00"] == 80
    assert vals["11:00"] == 40


def test_crowd_pressure_by_area(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        _seed(st)
        conn = queries.connect(str(tmp_path / "s.db"))
        df = queries.load_observations(conn, park_id=TDL)

    by_area = crowd.crowd_pressure_by_area(df, dt.date(2026, 7, 17))
    # Pooh=ファンタジーランド, Big Thunder=ウエスタンランド。
    areas = set(by_area["area"].unique())
    assert "ファンタジーランド" in areas
    assert "ウエスタンランド" in areas


def test_heatmap_long_shape(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        _seed(st)
        conn = queries.connect(str(tmp_path / "s.db"))
        df = queries.load_observations(conn, park_id=TDL)

    long = crowd.heatmap_long(df)
    assert set(long.columns) == {"weekday", "hour", "wait_minutes"}
    fri_10 = long[(long["weekday"] == "Friday") & (long["hour"] == 10)]
    # 10:00 の平均待ち = (30 + 50) / 2 = 40。
    assert fri_10["wait_minutes"].iloc[0] == 40


def test_current_disruptions(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        _seed(st)
        conn = queries.connect(str(tmp_path / "s.db"))
        df = queries.load_observations(conn, park_id=TDL)

    disruptions = crowd.current_disruptions(df, dt.date(2026, 7, 17))
    assert "Big Thunder Mountain" in set(disruptions["name"])
    assert "DOWN" in set(disruptions["status"])


def test_available_dates_and_parks(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        _seed(st)
        conn = queries.connect(str(tmp_path / "s.db"))
        df = queries.load_observations(conn)

    assert queries.available_dates(df) == [dt.date(2026, 7, 17)]
    assert queries.available_parks(conn) == [TDL]


def test_park_names_from_park_entity(tmp_path):
    with Storage(tmp_path / "s.db") as st:
        _seed(st)
        conn = queries.connect(str(tmp_path / "s.db"))
    names = queries.park_names(conn)
    assert names[TDL] == "Tokyo Disneyland"


def test_empty_frame_aggregations_are_safe():
    empty = pd.DataFrame(
        columns=["ts", "park_id", "entity_id", "name", "entity_type", "status",
                 "wait_minutes", "ts_local", "date", "hour", "weekday"]
    )
    assert crowd.waveform(empty, dt.date(2026, 7, 17)).empty
    assert crowd.heatmap_long(empty).empty
    assert crowd.crowd_pressure(empty, dt.date(2026, 7, 17)).empty
    assert crowd.crowd_pressure_by_area(empty, dt.date(2026, 7, 17)).empty
    assert crowd.current_disruptions(empty, dt.date(2026, 7, 17)).empty


def test_area_for_known_and_unknown():
    assert area_for("Pooh's Hunny Hunt") == "ファンタジーランド"
    assert area_for("Tower of Terror") == "アメリカンウォーターフロント"
    assert area_for("知らないライド") == AREA_UNKNOWN
    assert area_for(None) == AREA_UNKNOWN
