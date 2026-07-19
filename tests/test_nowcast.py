"""到着時予測(analysis/nowcast)のテスト。決定的データで数式・ボード拡張・検算を検証。"""

from __future__ import annotations

import math

import pandas as pd

from sabotage.analysis import nowcast

TDL = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"


def _df(rows):
    """rows: (ts_iso, entity_id, name, status, wait) → load_observations 相当の DataFrame。"""
    recs = []
    for ts, eid, name, status, wait in rows:
        t = pd.Timestamp(ts, tz="Asia/Tokyo")
        recs.append(
            dict(
                park_id=TDL,
                entity_id=eid,
                name=name,
                entity_type="ATTRACTION",
                status=status,
                wait_minutes=wait,
                ts_local=t,
                hour=t.hour,
                date=t.date(),
                weekday=t.day_name(),
            )
        )
    return pd.DataFrame(recs)


# --- 純粋な数式 herd_adjusted_value ----------------------------------------


def test_reversion_pulls_low_wait_up_toward_typical():
    # 今20分・平常50/55分 → 着く頃は埋まって上がる。
    pred, method = nowcast.herd_adjusted_value(20, 50, 55, None, 20, tau=30)
    assert method == "reversion"
    expected = round((55 + (20 - 50) * math.exp(-20 / 30)) / 5) * 5
    assert pred == expected
    assert pred > 20  # 低い罠を補正して引き上げる


def test_reversion_eases_high_wait_down():
    # 今80分・平常50分 → 待てば下がる。
    pred, _ = nowcast.herd_adjusted_value(80, 50, 50, None, 20, tau=30)
    assert pred < 80


def test_momentum_when_no_typical():
    pred, method = nowcast.herd_adjusted_value(30, None, None, 2.0, 10)
    assert method == "momentum"
    assert pred == 50  # 30 + 2*10


def test_flat_when_nothing():
    pred, method = nowcast.herd_adjusted_value(30, None, None, None, 20)
    assert method == "flat"
    assert pred == 30


def test_none_current():
    assert nowcast.herd_adjusted_value(None, 50, 55, 1.0, 20) == (None, "none")


def test_clip_negative_to_zero():
    pred, _ = nowcast.herd_adjusted_value(0, 5, 0, -5.0, 20, tau=30)
    assert pred == 0


# --- predict_board ----------------------------------------------------------


def test_predict_board_flags_trap_dip_as_konmu():
    # Splash は平常14時台=50分の履歴が豊富、今は15分に急落(=罠)。
    hist = [
        (f"2026-07-{d:02d}T14:00:00", "e_splash", "Splash Mountain", "OPERATING", 50)
        for d in range(9, 17)
    ]
    now = [
        ("2026-07-17T14:00:00", "e_splash", "Splash Mountain", "OPERATING", 15),
        ("2026-07-17T14:00:00", "e_omni", "Omnibus", "DOWN", None),
    ]
    df = _df(hist + now)

    board = nowcast.predict_board(df, TDL, arrival_min=20)
    splash = board[board["name"] == "Splash Mountain"].iloc[0]
    assert splash["pred_method"] == "reversion"
    assert splash["pred_wait"] > splash["wait_minutes"]  # 着く頃は上がる
    assert splash["signal"] == "混む"

    omni = board[board["name"] == "Omnibus"].iloc[0]
    assert pd.isna(omni["pred_wait"])     # 停止は予測しない
    assert pd.isna(omni["signal"])


def test_predict_board_empty_safe():
    empty = pd.DataFrame(
        columns=["park_id", "entity_type", "ts_local", "hour", "wait_minutes", "status", "name", "entity_id"]
    )
    b = nowcast.predict_board(empty, TDL)
    assert b.empty
    assert "pred_wait" in b.columns


# --- backtest ---------------------------------------------------------------


def test_backtest_produces_metrics():
    # 5分刻み・同一時間帯。dip→回復を含む系列。
    rows = []
    waits = [50, 50, 20, 25, 45, 50, 50]
    for i, w in enumerate(waits):
        m = i * 5
        rows.append((f"2026-07-17T14:{m:02d}:00", "e1", "Ride", "OPERATING", w))
    df = _df(rows)

    res = nowcast.backtest(df, arrival_min=10, tau=30)
    assert res["pairs"] > 0
    assert res["herd_mae"] is not None
    assert res["naive_mae"] is not None
    assert res["beats_naive"] in (True, False)
    # dip(20)からの回復はスパイク(+10以上)を含む。
    assert res["spike_total"] >= 1
    assert 0.0 <= (res["spike_recall"] or 0.0) <= 1.0


def test_backtest_empty_safe():
    empty = pd.DataFrame(
        columns=["entity_type", "wait_minutes", "ts_local", "hour", "entity_id"]
    )
    res = nowcast.backtest(empty)
    assert res["pairs"] == 0
    assert res["herd_mae"] is None
