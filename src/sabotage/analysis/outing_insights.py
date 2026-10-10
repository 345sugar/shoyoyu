"""絞り込み後の到着予測から、余白のある過ごし方を一つ示す。"""

from __future__ import annotations

import math

import pandas as pd

from .insights import Insight


def _minutes(value: float) -> str:
    return f"{float(value):g}"


def outing_insights(
    chosen: pd.DataFrame, *, fresh: bool, arrival_min: int
) -> list[Insight]:
    """現在待ちや経路を推測せず、有効な到着時予測だけを比較する。

    鮮度が足りない場合には候補の良し悪しも判断しない。候補数と統計量は
    呼び出し側で絞った範囲に限り、パーク全体の混雑とは解釈しない。
    """
    if not math.isfinite(arrival_min) or arrival_min < 0:
        raise ValueError("arrival_min must be finite and non-negative")
    horizon = f"いまから{_minutes(arrival_min)}分後の到着予測"
    if not fresh:
        return [Insight(
            kind="insufficient",
            title="新しい記録が届くまで、判断をひと休み",
            observation="観測の鮮度を確認できないため、いまの候補は比較できません。",
            suggestion="新しい記録が届いてから、到着時の予測を確かめてください。",
            evidence="鮮度不足のため、保存された待ち時間から現在の状況を推測していません。",
        )]

    required = {"name", "status", "pred_wait"}
    valid = chosen.iloc[:0].copy()
    if required.issubset(chosen.columns):
        values = pd.to_numeric(chosen["pred_wait"], errors="coerce")
        names = chosen["name"].map(
            lambda name: isinstance(name, str) and bool(name.strip())
        ).astype(bool)
        mask = (
            chosen["status"].eq("OPERATING")
            & values.notna() & values.ge(0) & values.lt(float("inf")) & names
        )
        valid = chosen.loc[mask].copy()
        valid["pred_wait"] = values.loc[mask]

    if valid.empty:
        return [Insight(
            kind="insufficient",
            title="条件を少し広げて、次の候補を待つ",
            observation="選んだ条件では、比較できる到着時予測がありません。",
            suggestion="エリアや待ち時間の条件を見直すか、新しい記録を待ってみてください。",
            evidence=f"{horizon} · 有効な候補 0件",
        )]

    waits = valid["pred_wait"]
    minimum, maximum, median = float(waits.min()), float(waits.max()), float(waits.median())
    best_name = valid.loc[waits.eq(minimum), "name"].iloc[0]
    count = len(valid)
    evidence = (
        f"{horizon} · 選択中の候補 {count}件 · "
        f"予測の範囲 {_minutes(minimum)}〜{_minutes(maximum)}分 · "
        f"予測の中央値 {_minutes(median)}分"
    )
    if minimum >= 60:
        return [Insight(
            kind="comfort",
            title="心地よく待てる長さを、先に決めておく",
            observation=f"選択中の{count}件は、到着時の予測待ちがいずれも{_minutes(minimum)}分以上です。",
            suggestion="長く並びたい気分でなければ、待てる上限を下げて探すことや、食事・休憩を挟むことも選択肢に。",
            evidence=evidence,
        )]
    if count >= 3 and median - minimum >= 15:
        return [Insight(
            kind="choice",
            title="並ぶ時間に、選べる余白がある",
            observation=(
                f"{best_name}は到着時の予測待ちが{_minutes(minimum)}分。"
                f"選択中の候補の中央値{_minutes(median)}分との差は{_minutes(median - minimum)}分です。"
            ),
            suggestion="食事や休憩の時間も楽しみたいなら、予測待ちが短い候補を比べてみてください。移動時間や実際の待ち時間は別に確かめましょう。",
            evidence=evidence,
        )]
    if count >= 2 and maximum - minimum < 15:
        return [Insight(
            kind="balanced",
            title="待ち時間の差より、今日の気分で",
            observation=f"選択中の{count}件では、到着時の予測待ちの最大差が{_minutes(maximum - minimum)}分です。",
            suggestion="予測の数字が近いので、好きな雰囲気や移動のしやすさも選ぶ手がかりに。",
            evidence=evidence,
        )]
    if count <= 2:
        return [Insight(
            kind="limited",
            title="少ない候補から、気分に合うものを",
            observation=f"有効な到着時予測は{count}件。{best_name}の予測待ちは{_minutes(minimum)}分です。",
            suggestion="この範囲だけで決めにくければ、エリアやお気に入りの条件を広げて比べてみてください。",
            evidence=evidence,
        )]
    return [Insight(
        kind="compare",
        title="予測待ちと、行きたい気持ちを見比べる",
        observation=f"{best_name}の到着時の予測待ちは{_minutes(minimum)}分。選択中の候補には最大{_minutes(maximum - minimum)}分の予測差があります。",
        suggestion="短い予測待ちを手がかりにしつつ、好きな体験や移動のしやすさも並べて選んでみてください。",
        evidence=evidence,
    )]
