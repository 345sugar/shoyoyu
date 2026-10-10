"""現況ボードの操作と、デモが通信しないことを Streamlit で検証する。"""

from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from sabotage.tools.seed_demo import TDL
from sabotage.viz import board


def _render_demo():
    from sabotage.viz.board import render
    render("uncreated-demo-folder/must-not-exist.db", demo=True, self_poll=True)


def test_demo_never_bootstraps_or_polls_and_filters_persist(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("demo must not fetch data")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(board, "_bootstrap_history_once", forbidden)
    monkeypatch.setattr(board, "_ensure_background_poller", forbidden)
    board._demo_data.clear()
    at = AppTest.from_function(_render_demo).run(timeout=20)
    assert not at.exception
    assert not (tmp_path / "uncreated-demo-folder").exists()
    assert any("合成デモ" in warning.value for warning in at.warning)
    assert any("2026年7月18日 14:00" in warning.value for warning in at.warning)
    assert int(at.metric[1].value.removesuffix("件")) > 1
    attraction = "Pirates of the Caribbean"
    at.multiselect(key=f"fav_{TDL}").select(attraction).run()
    at.toggle(key="favorites_only").set_value(True).run()
    assert not at.exception
    assert at.metric[1].value == "1件"
    assert attraction in str(at.query_params[f"fav_{TDL}"])
    assert "1" in str(at.query_params["fav_only"])
    at.multiselect(key=f"area_{TDL}").select("ファンタジーランド").run()
    assert at.metric[1].value == "0件"
    assert any("条件に合う候補がありません" in item.value for item in at.info)
    assert "ファンタジーランド" in str(at.query_params[f"area_{TDL}"])


def test_query_parameters_restore_on_new_session():
    at = AppTest.from_function(_render_demo)
    at.query_params["park"] = TDL
    at.query_params[f"fav_{TDL}"] = ["Pirates of the Caribbean"]
    at.query_params["fav_only"] = "1"
    at.query_params["max_wait"] = "60"
    at.run(timeout=20)
    assert not at.exception
    assert at.multiselect(key=f"fav_{TDL}").value == ["Pirates of the Caribbean"]
    assert at.toggle(key="favorites_only").value is True
    assert at.selectbox(key="max_wait").value == 60
    assert at.metric[1].value == "1件"


def test_row_escapes_remote_text_and_does_not_display_current_wait():
    row = {"name": '<script>alert("x")</script>', "area": '<img src=x onerror="x">',
           "status": "OPERATING", "pred_wait": 20, "wait_minutes": 999, "pred_method": "flat"}
    html = board._row(row, favorite=True)
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    assert "999" not in html
    unknown = dict(row, status="UNRECOGNIZED", pred_wait=5)
    unknown_html = board._row(unknown)
    assert "状態不明" in unknown_html
    assert "分・到着時予測" not in unknown_html


def test_demo_contains_no_future_observations():
    df, _, parks = board._demo_data()
    assert not df.empty and len(parks) == 2
    assert df["ts_local"].max() == board.DEMO_NOW
    assert len(df["date"].unique()) == 3


def test_stale_demo_observations_suppress_pick_and_meal_advice(monkeypatch):
    original = board._demo_data()
    stale = original[0].copy()
    stale["ts_local"] = stale["ts_local"] - pd.Timedelta(minutes=20)
    monkeypatch.setattr(board, "_demo_data", lambda: (stale, original[1], original[2]))
    at = AppTest.from_function(_render_demo).run(timeout=20)
    assert not at.exception
    assert any("移動候補と食事どきの提案を止めています" in item.value for item in at.warning)
    assert not any('<div class="mpm-pick">' in item.value for item in at.markdown)
    assert not any("#### ひと息つく時間" in item.value for item in at.markdown)
    assert any("過去の観測を使った参考一覧" in item.value for item in at.markdown)


def test_correlation_view_can_be_opened_from_board():
    at = AppTest.from_function(_render_demo).run(timeout=20)
    at.radio(key="view_choice").set_value("correlations").run(timeout=20)
    assert not at.exception
    assert "correlations" in str(at.query_params["view"])
    assert all(item.key != "arrival_minutes" for item in at.slider)
    assert any("相関" in item.value for item in at.markdown)
