"""相関画面の選択操作と、散布図・件数・数値の一致を検証する。"""

import datetime as dt

import pandas as pd
import pytest

pytest.importorskip("streamlit")
pytest.importorskip("altair")
from streamlit.testing.v1 import AppTest

from sabotage.tools.seed_demo import TDL
from sabotage.viz import app


def _render_demo(with_weather=True):
    import datetime as dt
    from zoneinfo import ZoneInfo

    import pandas as pd

    from sabotage.analysis import correlations, queries
    from sabotage.data.storage import Storage
    from sabotage.tools.seed_demo import TDL, seed
    from sabotage.viz.app import _correlation_section

    with Storage(":memory:") as storage:
        seed(storage, days=3, today=dt.datetime(2026, 7, 18, tzinfo=ZoneInfo("Asia/Tokyo")))
        waits = queries.load_observations(storage.connection, park_id=TDL)
    weather = pd.DataFrame()
    if with_weather:
        hourly = correlations.hourly_waits(waits).iloc[::2]
        # 欠測を含む合成天気。最初の施設と気温の既知の線形関係を作る。
        weather = pd.DataFrame({
            "ts": hourly.index.tz_convert("UTC").astype(str), "http_status": 200,
            "temp_c": hourly.iloc[:, 0].to_numpy() / 10 + 10,
            "precip_mm": [i % 3 for i in range(len(hourly))],
            "precip_prob": [i % 5 * 20 for i in range(len(hourly))],
        })
    _correlation_section(waits, weather)


@pytest.fixture
def recorded_charts(monkeypatch):
    charts = []
    original = app.st.altair_chart

    def record(chart, **kwargs):
        # Streamlit自身のシリアライズと描画処理も実行する。
        charts.append(chart)
        return original(chart, **kwargs)

    monkeypatch.setattr(app.st, "altair_chart", record)
    return charts


def test_adjustment_and_weather_pair_keep_scatter_counts_and_metrics_consistent(recorded_charts):
    at = AppTest.from_function(_render_demo).run(timeout=20)
    assert not at.exception
    assert at.checkbox[0].disabled is False
    assert at.metric[0].value != at.metric[1].value
    metrics = [item.value for item in at.metric]
    adjusted = recorded_charts[-1].data.copy()
    assert adjusted["x"].mean() == pytest.approx(0, abs=1e-12)
    assert len(adjusted) == int(at.metric[2].value.split()[0])

    at.checkbox[1].set_value(False).run(timeout=20)
    assert not at.exception
    raw = recorded_charts[-1].data
    assert not raw.equals(adjusted)
    assert [item.value for item in at.metric] == metrics  # 両方のrは常に併記。
    assert float(at.metric[0].value) == pytest.approx(raw["x"].corr(raw["y"]), abs=.0051)

    at.checkbox[0].set_value(True).run(timeout=20)
    at.selectbox(key="corr_right").select("天気 / 気温 (℃)").run(timeout=20)
    assert not at.exception
    samples = recorded_charts[-1].data
    count = int(at.metric[2].value.split()[0])
    assert count == len(samples) == 20  # 3日39時間のうち、天気がある20時間だけ。
    assert at.metric[0].value == "+1.00"
    counts = at.dataframe[0].value
    assert counts.loc[at.selectbox(key="corr_left").value, "天気 / 気温 (℃)"] == count
    spec = recorded_charts[-1].to_dict()
    assert "1時間平均" in spec["encoding"]["x"]["title"]
    assert "datasets" in spec


def test_no_weather_and_partial_period_or_few_attractions_do_not_crash():
    at = AppTest.from_function(_render_demo, args=(False,)).run(timeout=20)
    assert not at.exception
    assert at.checkbox[0].disabled is True
    assert any("天気ログはありません" in caption.value for caption in at.caption)

    at.date_input[0].set_value((dt.date(2026, 7, 17),)).run(timeout=20)
    assert not at.exception
    assert any("開始日と終了日" in info.value for info in at.info)
    at.date_input[0].set_value((dt.date(2026, 7, 17), dt.date(2026, 7, 17))).run(timeout=20)
    assert not at.exception
    assert any("2日以上" in info.value for info in at.info)
    assert at.metric[1].value == "—"

    at.multiselect(key=f"corr_rides_{TDL}").set_value([]).run(timeout=20)
    assert not at.exception
    assert any("2つ以上" in info.value for info in at.info)
    at.multiselect(key=f"corr_rides_{TDL}").set_value(["Big Thunder Mountain"]).run(timeout=20)
    assert not at.exception
    assert any("2つ以上" in info.value for info in at.info)


def test_pair_options_remain_valid_when_left_and_attraction_set_change():
    at = AppTest.from_function(_render_demo).run(timeout=20)
    first_right = at.selectbox(key="corr_right").value
    at.selectbox(key="corr_left").select(first_right).run(timeout=20)
    assert not at.exception
    assert at.selectbox(key="corr_left").value != at.selectbox(key="corr_right").value
    at.multiselect(key=f"corr_rides_{TDL}").set_value(["Pooh's Hunny Hunt", "Splash Mountain"]).run(timeout=20)
    assert not at.exception
    assert set([at.selectbox(key="corr_left").value, at.selectbox(key="corr_right").value]) == {
        "Pooh's Hunny Hunt", "Splash Mountain"
    }


def test_chart_builders_serialize_null_correlations_and_jst_time():
    corr = pd.DataFrame([[1, float("nan")], [float("nan"), float("nan")]], index=["A", "B"], columns=["A", "B"])
    counts = pd.DataFrame([[12, 2], [2, 2]], index=corr.index, columns=corr.columns)
    spec = app._correlation_chart(corr, counts).to_dict()
    assert spec["encoding"]["color"]["scale"]["domain"] == [-1, 0, 1]
    rows = next(iter(spec["datasets"].values()))
    assert any(row["r"] is None and row["n"] == 2 for row in rows)
    samples = pd.DataFrame({"A": [1, 2], "B": [2, 4]}, index=pd.date_range("2026-07-18 09:00", periods=2, freq="h", tz="Asia/Tokyo"))
    scatter = app._scatter_chart(samples, "A", "B", adjusted=True).to_dict()
    assert "時間帯平均との差" in scatter["encoding"]["x"]["title"]
    assert next(iter(scatter["datasets"].values()))[0]["時刻"] == "2026-07-18 09:00 JST"
