"""旧DB互換と、保存した天気履歴の読み出しを検証する。"""

import sqlite3

from sabotage.analysis.queries import load_weather


def test_weather_history_without_table_is_empty_with_stable_columns():
    with sqlite3.connect(":memory:") as conn:
        history = load_weather(conn)
    assert history.empty
    assert list(history.columns) == ["ts", "http_status", "temp_c", "precip_mm", "precip_prob"]


def test_weather_history_keeps_missing_fetches_and_orders_rows():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE weather (ts TEXT, http_status INTEGER, temp_c REAL, precip_mm REAL, precip_prob INTEGER)")
        conn.executemany("INSERT INTO weather VALUES (?, ?, ?, ?, ?)", [
            ("2026-07-18T02:00:00Z", 0, None, None, None),
            ("2026-07-18T01:00:00Z", 200, 27.5, 0.1, 30),
        ])
        history = load_weather(conn)
    assert history["http_status"].tolist() == [200, 0]
    assert history.iloc[0]["temp_c"] == 27.5
    assert history.iloc[1][["temp_c", "precip_mm", "precip_prob"]].isna().all()
