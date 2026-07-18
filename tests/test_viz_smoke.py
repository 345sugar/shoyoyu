"""viz.app のスモークテスト。Streamlit を実起動せず、import と純関数だけ確認する。

app.py の描画は `if __name__ == '__main__'` ガードの内側なので import では走らない。
チャート生成ヘルパは Streamlit 非依存(altair)なのでここで検証できる。
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

# viz は [viz] extra(streamlit/altair)が要る。無い環境ではスキップ。
pytest.importorskip("streamlit")
alt = pytest.importorskip("altair")

from sabotage.analysis import crowd, queries
from sabotage.data.storage import Storage
from sabotage.tools import seed_demo

JST = ZoneInfo("Asia/Tokyo")


def test_app_module_imports():
    # import 時に Streamlit 実行が走らない(ガードされている)ことの確認。
    from sabotage.viz import app

    assert hasattr(app, "render")
    assert hasattr(app, "main")


def test_chart_builders_produce_altair_charts(tmp_path):
    from sabotage.viz import app

    db = tmp_path / "s.db"
    with Storage(db) as st:
        seed_demo.seed(st, days=1, today=dt.datetime(2026, 7, 18, tzinfo=JST))

    conn = queries.connect(str(db))
    df = queries.load_observations(conn, park_id=seed_demo.TDL)
    day = dt.date(2026, 7, 18)

    wave = crowd.waveform(df, day)
    heat = crowd.heatmap_long(df)
    area = crowd.crowd_pressure_by_area(df, day)

    assert isinstance(app._line_chart(wave, "待ち"), alt.Chart)
    assert isinstance(app._heatmap_chart(heat), alt.Chart)
    assert isinstance(app._area_pressure_chart(area), alt.Chart)
