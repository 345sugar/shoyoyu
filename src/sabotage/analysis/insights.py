"""観測済みの相関から、確かめる価値のある比較を短く案内する。

カードは探索の入口であり、因果関係や当日の混雑を説明するものではない。
共通観測の少ないペア、時間帯調整後に値が一定のペアは強調しない。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from math import isfinite

import pandas as pd

from .correlations import analyze, paired_samples


@dataclass(frozen=True)
class Insight:
    kind: str
    title: str
    observation: str
    suggestion: str
    evidence: str
    left: str | None = None
    right: str | None = None


@dataclass(frozen=True)
class _Pair:
    left: str
    right: str
    raw: float
    adjusted: float
    hours: int
    days: int


def _coefficient(value: float) -> str:
    return f"{value:+.2f}" if isfinite(value) else "算出不可"


def _evidence(pair: _Pair) -> str:
    return (
        f"未調整 r={_coefficient(pair.raw)} / "
        f"時間帯調整 r={_coefficient(pair.adjusted)} · "
        f"共通観測 {pair.hours} 時間・{pair.days} 日"
    )


def _card(pair: _Pair, kind: str, weather: set[str]) -> Insight:
    names = f"「{pair.left}」と「{pair.right}」"
    if kind == "attenuation":
        title = "時間帯をそろえると弱まる関係"
        observation = (
            f"{names}の時間ごとの平均値は、時間帯を調整すると相関が弱まりました。"
        )
        suggestion = "同じ時刻の別の日や、平日・休日に分けて散布図を見比べてみましょう。"
    elif kind == "weather":
        weather_name = pair.left if pair.left in weather else pair.right
        ride_name = pair.right if pair.left in weather else pair.left
        direction = "同じ" if pair.adjusted > 0 else "逆"
        title = "天気と待ち時間を見比べる手がかり"
        observation = (
            f"「{weather_name}」と「{ride_name}」の待ち時間には、"
            f"時間帯を調整しても{direction}方向の相関が見られました。"
        )
        suggestion = (
            "日付・曜日の偏りも確かめる比較候補です。"
            "当日の行動は最新の到着時予測で判断しましょう。"
        )
    elif kind == "positive":
        title = "一緒に混みやすい組み合わせ"
        observation = (
            f"{names}では、この期間の同じ時間帯の平均に比べた待ち時間の長短が、"
            "両方で似る傾向が見られました。"
        )
        suggestion = (
            "片方を空いている代替案と決めず、"
            "当日は両方の到着時予測も見比べましょう。"
        )
    else:
        title = "混雑の傾向が異なる組み合わせ"
        observation = (
            f"{names}では、一方が同じ時間帯の平均より長いとき、"
            "もう一方は短いという傾向が見られました。"
        )
        suggestion = (
            "空き具合の違いを調べる比較候補に。"
            "実際に向かう前には、最新の到着時予測を確かめましょう。"
        )
    return Insight(
        kind, title, observation, suggestion, _evidence(pair), pair.left, pair.right
    )


def correlation_insights(
    wide: pd.DataFrame,
    *,
    min_pairs: int = 10,
    weather_columns: Iterable[str] = (),
) -> list[Insight]:
    """時間ごとの横持ち表から、根拠を添えた日本語カードを最大3件返す。

    各候補は同じ2項目が観測された24時間以上・3日以上を必要とする。
    min_pairsが24より大きい場合は、その観測数を優先する。時間帯調整は
    correlationsと同じペア内の共通観測で行う。天気同士は候補にしない。
    この閾値は表示を控えめにするための条件であり、統計的有意性ではない。
    """
    raw, counts, _ = analyze(wide, min_pairs=min_pairs)
    adjusted, _, _ = analyze(wide, min_pairs=min_pairs, adjust_hour=True)
    minimum = max(min_pairs, 24)
    weather = set(weather_columns)
    pairs: list[_Pair] = []
    for left, right in combinations(raw.columns, 2):
        if left in weather and right in weather:
            continue
        samples = paired_samples(wide.loc[:, [left, right]], left, right)
        pairs.append(
            _Pair(
                left,
                right,
                float(raw.loc[left, right]),
                float(adjusted.loc[left, right]),
                int(counts.loc[left, right]),
                int(samples.index.normalize().nunique()),
            )
        )

    eligible = [p for p in pairs if p.hours >= minimum and p.days >= 3]
    finite = [p for p in eligible if isfinite(p.raw) and isfinite(p.adjusted)]
    attenuation = sorted(
        (
            p for p in finite
            if abs(p.raw) >= 0.5
            and abs(p.raw) - abs(p.adjusted) >= 0.3
            and abs(p.adjusted) < 0.4
        ),
        key=lambda p: -(abs(p.raw) - abs(p.adjusted)),
    )
    weather_pairs = sorted(
        (
            p for p in finite
            if (p.left in weather or p.right in weather) and abs(p.adjusted) >= 0.5
        ),
        key=lambda p: -abs(p.adjusted),
    )
    rides = sorted(
        (p for p in finite if p.left not in weather and p.right not in weather),
        key=lambda p: -abs(p.adjusted),
    )
    positive = next((p for p in rides if p.adjusted >= 0.5), None)
    negative = next((p for p in rides if p.adjusted <= -0.5), None)
    ride_candidates = sorted(
        [(p, kind) for p, kind in ((positive, "positive"), (negative, "negative")) if p],
        key=lambda candidate: -abs(candidate[0].adjusted),
    )
    candidates = (
        [(p, "attenuation") for p in attenuation[:1]]
        + [(p, "weather") for p in weather_pairs[:1]]
        + ride_candidates
    )
    result: list[Insight] = []
    used: set[frozenset[str]] = set()
    for pair, kind in candidates:
        key = frozenset((pair.left, pair.right))
        if key not in used:
            result.append(_card(pair, kind, weather))
            used.add(key)
        if len(result) == 3:
            break
    if result:
        return result

    requirements = f"1組につき共通観測 {minimum} 時間以上・3 日以上が必要です。"
    if eligible:
        return [Insight(
            "no_pattern",
            "この期間は、判断を急がずに",
            "観測数の条件を満たす組み合わせでも、今回取り上げる条件に合う相関は見つかりませんでした。",
            "関係がないとは限りません。期間を変えて比較し、調整後の値が一定の項目も確認しましょう。",
            f"観測数の条件を満たす組み合わせ {len(eligible)} 組 · {requirements}",
        )]
    if pairs:
        best = max(pairs, key=lambda p: (p.hours, p.days))
        observation = (
            f"共通観測が最も多い「{best.left}」と「{best.right}」でも、"
            "時間数または日数がまだ足りません。"
        )
        evidence = f"{_evidence(best)} · {requirements}"
    else:
        observation = "選んだ項目に、待ち時間を含む2項目の組み合わせがありません。"
        evidence = requirements
    return [Insight(
        "insufficient",
        "見どころは、記録が育ってから",
        observation,
        "乗り物を含む2項目以上を選び、記録のある日を広げてから比べましょう。",
        evidence,
    )]
