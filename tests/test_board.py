"""現況ボード(analysis/board)のテスト。決定的な観測で最新状態・トレンド・割安判定を検証。"""

from __future__ import annotations

import pandas as pd

from sabotage.analysis import board, queries
from sabotage.data.normalize import Observation
from sabotage.data.storage import Storage

TDL = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"


def _seed(st: Storage, ts: str, rows: list[tuple]) -> None:
    """rows: (entity_id, name, status, wait) を1スナップショットとして投入。"""
    sid = st.record_snapshot(ts=ts, source="themeparks.wiki", park_id=TDL, http_status=200, raw_json="{}")
    obs = [
        Observation(park_id=TDL, entity_id=e, name=n, entity_type="ATTRACTION", status=s, wait_minutes=w)
        for e, n, s, w in rows
    ]
    st.record_observations(ts=ts, observations=obs, snapshot_id=sid)


def _load(db):
    return queries.load_observations(queries.connect(str(db)), park_id=TDL)


def test_current_board_uses_latest_snapshot(tmp_path):
    db = tmp_path / "s.db"
    with Storage(db) as st:
        # 2つのスナップショット。最新(02:00Z=11:00JST)を採用。
        _seed(st, "2026-07-17T01:00:00Z", [("e1", "Pooh", "OPERATING", 30)])
        _seed(st, "2026-07-17T02:00:00Z", [("e1", "Pooh", "OPERATING", 45)])

    b = board.current_board(_load(db), TDL)
    assert len(b) == 1
    row = b.iloc[0]
    assert row["wait_minutes"] == 45          # 最新
    assert row["delta"] == 15                 # 45 - 30(直近比)
    assert row["area"] == "その他"  # Pooh は areas 未登録名なので UNKNOWN(名前 "Pooh")


def test_current_board_no_trend_on_first_snapshot(tmp_path):
    db = tmp_path / "s.db"
    with Storage(db) as st:
        _seed(st, "2026-07-17T02:00:00Z", [("e1", "Big Thunder Mountain", "OPERATING", 50)])
    b = board.current_board(_load(db), TDL)
    assert b.iloc[0]["delta"] is None
    assert b.iloc[0]["fair_wait"] is None      # 履歴なし
    assert b.iloc[0]["area"] == "ウエスタンランド"


def test_current_board_down_has_none_wait(tmp_path):
    db = tmp_path / "s.db"
    with Storage(db) as st:
        _seed(st, "2026-07-17T02:00:00Z", [("e1", "Omnibus", "DOWN", None)])
    b = board.current_board(_load(db), TDL)
    assert b.iloc[0]["status"] == "DOWN"
    assert b.iloc[0]["wait_minutes"] is None


def test_fair_value_labels_with_enough_history(tmp_path):
    db = tmp_path / "s.db"
    with Storage(db) as st:
        # 過去の同時間帯(11時台=02:00Z)に平均50分の履歴を6日分。
        for d in range(11, 17):
            _seed(st, f"2026-07-{d:02d}T02:00:00Z", [("e1", "Ride", "OPERATING", 50)])
        # 現在(最新)は 20分 → 50*0.8=40 以下なので割安。
        _seed(st, "2026-07-17T02:05:00Z", [("e1", "Ride", "OPERATING", 20)])

    b = board.current_board(_load(db), TDL, min_samples_for_fair=5)
    row = b.iloc[0]
    assert row["fair_wait"] == 50
    assert row["value_label"] == "割安"


def test_fair_value_none_when_history_thin(tmp_path):
    db = tmp_path / "s.db"
    with Storage(db) as st:
        _seed(st, "2026-07-17T02:00:00Z", [("e1", "Ride", "OPERATING", 50)])
        _seed(st, "2026-07-17T02:05:00Z", [("e1", "Ride", "OPERATING", 20)])
    b = board.current_board(_load(db), TDL, min_samples_for_fair=5)
    assert b.iloc[0]["fair_wait"] is None  # 履歴1件 < 5


def test_split_operating_sorts_short_first_and_separates_stopped(tmp_path):
    db = tmp_path / "s.db"
    with Storage(db) as st:
        _seed(
            st,
            "2026-07-17T02:00:00Z",
            [
                ("e1", "Long", "OPERATING", 90),
                ("e2", "Short", "OPERATING", 5),
                ("e3", "NoWait", "OPERATING", None),
                ("e4", "Down", "DOWN", None),
            ],
        )
    b = board.current_board(_load(db), TDL)
    operating, stopped = board.split_operating(b)
    # 運営中は待ち短い順、未掲出(None)は末尾。
    assert list(operating["name"]) == ["Short", "Long", "NoWait"]
    assert list(stopped["name"]) == ["Down"]


def test_current_board_empty_safe():
    empty = pd.DataFrame(
        columns=["park_id", "entity_type", "ts_local", "hour", "wait_minutes", "status", "name", "entity_id"]
    )
    assert board.current_board(empty, TDL).empty
    op, sp = board.split_operating(board.current_board(empty, TDL))
    assert op.empty and sp.empty
