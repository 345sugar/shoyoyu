"""到着時の予測待ちを使う絞り込みと、観測の鮮度判定。

距離・経路は推定しない。候補間で同じ到着時間を仮定した予測値を比較する。
鮮度判定は現在時刻を注入でき、ネットワークや UI なしで検証できる。
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import pandas as pd

DEFAULT_TZ = "Asia/Tokyo"
_CLOCK_SKEW_MINUTES = 1.0


def freshness(
    latest,
    *,
    now=None,
    max_age_minutes: float = 15,
) -> dict:
    """{fresh, age_minutes, reason} を返す。reason は fresh/stale/future/unknown。

    時差情報のない時刻は表示対象の日本時間として扱う。1分までの未来時刻は
    時計差として許容し、それを超える未来時刻や不明な時刻では候補を勧めない。
    """
    if not math.isfinite(max_age_minutes) or max_age_minutes < 0:
        raise ValueError("max_age_minutes must be finite and non-negative")
    unknown = {"fresh": False, "age_minutes": None, "reason": "unknown"}
    if latest is None:
        return unknown
    try:
        observed = pd.Timestamp(latest)
        reference = pd.Timestamp.now(tz=DEFAULT_TZ) if now is None else pd.Timestamp(now)
        if pd.isna(observed) or pd.isna(reference):
            return unknown
        if observed.tzinfo is None:
            observed = observed.tz_localize(DEFAULT_TZ)
        if reference.tzinfo is None:
            reference = reference.tz_localize(DEFAULT_TZ)
        age = (reference - observed).total_seconds() / 60.0
    except (TypeError, ValueError, OverflowError):
        return unknown
    if not math.isfinite(age):
        return unknown
    if age < -_CLOCK_SKEW_MINUTES:
        return {"fresh": False, "age_minutes": age, "reason": "future"}
    age = max(0.0, age)
    fresh = age <= max_age_minutes
    return {"fresh": fresh, "age_minutes": age, "reason": "fresh" if fresh else "stale"}


def select_attractions(
    predicted_board: pd.DataFrame,
    *,
    areas: Iterable[str] = (),
    favorites: Iterable[str] = (),
    favorites_only: bool = False,
    max_wait: float | None = None,
) -> pd.DataFrame:
    """運営中で有効な予測を持つ候補を絞り、予測待ち→名前の順に返す。

    空の areas は全エリア。favorites_only=True でお気に入りが空なら候補も空。
    予測不明・負数・無限値・未知の運営状態を候補に含めず、元のボードは変更しない。
    呼び出し側で freshness() を確認し、古い観測の候補を表示しないこと。
    """
    if max_wait is not None and (not math.isfinite(max_wait) or max_wait < 0):
        raise ValueError("max_wait must be finite and non-negative")
    if predicted_board.empty:
        return predicted_board.copy()
    required = {"status", "pred_wait", "name"}
    if not required.issubset(predicted_board.columns):
        return predicted_board.iloc[:0].copy()
    selected = predicted_board.copy()
    predicted = pd.to_numeric(selected["pred_wait"], errors="coerce")
    valid = predicted.notna() & predicted.ge(0) & predicted.lt(float("inf"))
    selected["pred_wait"] = predicted
    selected = selected[(selected["status"] == "OPERATING") & valid]
    chosen_areas = tuple(areas)
    if chosen_areas:
        if "area" not in selected:
            return selected.iloc[:0].copy()
        selected = selected[selected["area"].isin(chosen_areas)]
    if favorites_only:
        selected = selected[selected["name"].isin(tuple(favorites))]
    if max_wait is not None:
        selected = selected[selected["pred_wait"] <= max_wait]
    # 2回の安定ソートで、予測待ち・名前が同じ行の元の順序も維持する。
    return selected.sort_values("name", kind="stable").sort_values("pred_wait", kind="stable")
