"""合成デモ生成器のテスト。生成→集計が破綻せず、source で区別・削除できること。"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sabotage.analysis import crowd, queries
from sabotage.data.storage import Storage
from sabotage.tools import seed_demo

JST = ZoneInfo("Asia/Tokyo")


def test_seed_creates_synthetic_data_tagged_as_demo(tmp_path):
    db = tmp_path / "s.db"
    fixed_today = dt.datetime(2026, 7, 18, tzinfo=JST)
    with Storage(db) as st:
        n = seed_demo.seed(st, days=2, today=fixed_today)
        assert n > 0
        # source は必ず demo-synthetic(本物と混ざらない)。
        assert queries.data_sources(st.connection) == {seed_demo.DEMO_SOURCE}
        assert st.get_meta(seed_demo.META_DEMO_FLAG) == "true"


def test_seeded_data_flows_through_analysis(tmp_path):
    db = tmp_path / "s.db"
    fixed_today = dt.datetime(2026, 7, 18, tzinfo=JST)
    with Storage(db) as st:
        seed_demo.seed(st, days=2, today=fixed_today)

    conn = queries.connect(str(db))
    df = queries.load_observations(conn, park_id=seed_demo.TDL)
    dates = queries.available_dates(df)
    # 2日分(7/17, 7/18)。
    assert dt.date(2026, 7, 17) in dates
    assert dt.date(2026, 7, 18) in dates

    wave = crowd.waveform(df, dt.date(2026, 7, 17))
    assert not wave.empty
    # 昼過ぎがピーク:12〜16時の最大が朝9時台より大きい。
    pooh = wave.get("Pooh's Hunny Hunt")
    assert pooh is not None
    midday = pooh[(pooh.index.hour >= 12) & (pooh.index.hour <= 16)].max()
    morning = pooh[pooh.index.hour == 9].max()
    assert midday > morning

    pressure = crowd.crowd_pressure(df, dt.date(2026, 7, 17))
    assert pressure["pressure"].max() > 0


def test_refurbishment_and_down_are_represented(tmp_path):
    db = tmp_path / "s.db"
    fixed_today = dt.datetime(2026, 7, 18, tzinfo=JST)
    with Storage(db) as st:
        seed_demo.seed(st, days=1, today=fixed_today)

    conn = queries.connect(str(db))
    df = queries.load_observations(conn, park_id=seed_demo.TDL)
    disruptions = crowd.current_disruptions(df, dt.date(2026, 7, 18))
    statuses = set(disruptions["status"])
    assert "REFURBISHMENT" in statuses  # Space Mountain
    assert "DOWN" in statuses  # Splash Mountain の停止ウィンドウ


def test_purge_removes_only_demo(tmp_path):
    db = tmp_path / "s.db"
    fixed_today = dt.datetime(2026, 7, 18, tzinfo=JST)
    with Storage(db) as st:
        # 本物っぽい行を1つ混ぜる。
        sid = st.record_snapshot(
            ts="2026-07-18T01:00:00Z",
            source="themeparks.wiki",
            park_id=seed_demo.TDL,
            http_status=200,
            raw_json="{}",
        )
        st.record_observations(
            ts="2026-07-18T01:00:00Z",
            observations=[],
            snapshot_id=sid,
        )
        seed_demo.seed(st, days=1, today=fixed_today)
        assert seed_demo.DEMO_SOURCE in queries.data_sources(st.connection)

        seed_demo.purge(st)
        sources = queries.data_sources(st.connection)
        assert seed_demo.DEMO_SOURCE not in sources
        assert "themeparks.wiki" in sources  # 本物は残る。
        assert st.get_meta(seed_demo.META_DEMO_FLAG) is None
