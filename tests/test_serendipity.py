"""セレンディピティ枠のテスト(純関数・ネットワーク非依存)。"""

from __future__ import annotations

import random

import pandas as pd

from sabotage.analysis import serendipity


def _board(rows: list[dict]) -> pd.DataFrame:
    """predict_board 相当の最小 DataFrame を作る。"""
    cols = {"name": [], "area": [], "status": [], "wait_minutes": [], "fair_wait": [], "pred_wait": []}
    for r in rows:
        for k in cols:
            cols[k].append(r.get(k))
    return pd.DataFrame(cols)


def test_picks_the_unusually_quiet_one():
    b = _board([
        {"name": "A", "area": "X", "status": "OPERATING", "wait_minutes": 10, "fair_wait": 40, "pred_wait": 10},
        {"name": "B", "area": "Y", "status": "OPERATING", "wait_minutes": 35, "fair_wait": 40, "pred_wait": 35},
    ])
    pick = serendipity.serendipity_pick(b, rng=random.Random(0), top_n=1)
    assert pick is not None
    assert pick.name == "A"
    assert pick.saved == 30
    assert pick.typical == 40
    assert pick.wait == 10


def test_none_when_nothing_unusually_quiet():
    # すべて平常並み(gap < min_gap)→ 動かない。
    b = _board([
        {"name": "A", "area": "X", "status": "OPERATING", "wait_minutes": 38, "fair_wait": 40, "pred_wait": 38},
        {"name": "B", "area": "Y", "status": "OPERATING", "wait_minutes": 41, "fair_wait": 40, "pred_wait": 41},
    ])
    assert serendipity.serendipity_pick(b, rng=random.Random(0)) is None


def test_ignores_stopped_and_missing_typical():
    b = _board([
        {"name": "Down", "area": "X", "status": "DOWN", "wait_minutes": 5, "fair_wait": 40, "pred_wait": None},
        {"name": "NoHist", "area": "Y", "status": "OPERATING", "wait_minutes": 5, "fair_wait": None, "pred_wait": 5},
    ])
    assert serendipity.serendipity_pick(b, rng=random.Random(0)) is None


def test_rng_chooses_among_top_candidates():
    # 3件とも十分空いている → rng により選択が変わりうる(偶然の風)。
    b = _board([
        {"name": "A", "area": "X", "status": "OPERATING", "wait_minutes": 5, "fair_wait": 40, "pred_wait": 5},
        {"name": "B", "area": "Y", "status": "OPERATING", "wait_minutes": 5, "fair_wait": 38, "pred_wait": 5},
        {"name": "C", "area": "Z", "status": "OPERATING", "wait_minutes": 5, "fair_wait": 36, "pred_wait": 5},
    ])
    names = {serendipity.serendipity_pick(b, rng=random.Random(s), top_n=3).name for s in range(20)}
    assert len(names) >= 2  # 複数の候補から選ばれている


def test_empty_board_is_safe():
    assert serendipity.serendipity_pick(pd.DataFrame()) is None
