"""セレンディピティ枠(鯤の余白)。

思想(CLAUDE.md):
- 鯤 → セレンディピティ枠。計画が偶然の風に乗って鵬に化ける瞬間のための余白。
- 網の原則 → シグナルを追うのではなく、signal の近く(=今いつもより空いている場所)に
  先に寄ってもらう。stay は受け身ではなく待ち伏せ。
- level-2 → 号令(「ここへ行け」)ではなく誘い(「寄り道どう?」)。素直な追随は敗北。

やること: 現況ボード(predict_board の出力)から「**今、その時間帯の平常よりはっきり
空いている**」アトラクションを拾い、ひとつだけ"寄り道"として差し出す。候補が複数あれば
偶然の風(rng)で選ぶ=毎回同じ最適解を出さない。平常より空いた場所が無ければ、
同点stay の原則どおり「無理に動かない」を返す(=余白はそのまま余白にしておく)。

純関数。DB にも Streamlit にも依存しない。fair_wait(=同時間帯の過去平均)は board 層が
既に算出済みなので、ここでは乖離(平常 - 現在)を見るだけ。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import pandas as pd

from .board import STOP_STATUSES

# 「いつもより空いている」と見なす最小の短縮分(平常 - 現在)。ノイズ除け。
DEFAULT_MIN_GAP = 10
# 偶然の風を吹かせる候補数。乖離の大きい上位からこの数だけ取り、rng で1つ選ぶ。
DEFAULT_TOP_N = 3


@dataclass(frozen=True)
class Detour:
    """寄り道の提案(1件)。text は持たず、表示層が文言を組む。"""

    name: str
    area: str
    wait: int           # 現在の待ち(分)
    typical: int        # その時間帯の平常(分)
    saved: int          # typical - wait(いつもよりどれだけ空いているか)
    pred_wait: int | None  # 到着時予測(あれば)


def _candidates(board_df: pd.DataFrame, *, min_gap: int) -> pd.DataFrame:
    """運営中で、平常(fair_wait)より min_gap 以上空いている行だけを返す。"""
    if board_df is None or board_df.empty:
        return board_df.iloc[0:0] if board_df is not None else pd.DataFrame()
    df = board_df.copy()
    df = df[~df["status"].isin(STOP_STATUSES)]
    df = df[df["wait_minutes"].notna() & df["fair_wait"].notna()]
    if df.empty:
        return df
    df["_saved"] = df["fair_wait"] - df["wait_minutes"]
    df = df[df["_saved"] >= min_gap]
    return df.sort_values("_saved", ascending=False)


def serendipity_pick(
    board_df: pd.DataFrame,
    *,
    rng: random.Random | None = None,
    min_gap: int = DEFAULT_MIN_GAP,
    top_n: int = DEFAULT_TOP_N,
) -> Detour | None:
    """今いつもより空いている穴場を1つ返す。無ければ None(=動かない)。

    乖離(平常 - 現在)の大きい上位 top_n から rng で1つ選ぶ。rng 未指定なら random を使う
    (表示側は観測時刻でseedして、短時間は安定・時間が進むと変わるようにする)。
    """
    cands = _candidates(board_df, min_gap=min_gap)
    if cands.empty:
        return None
    pool = cands.head(top_n)
    r = rng or random.Random()
    idx = r.randrange(len(pool))
    row = pool.iloc[idx]
    pred = row.get("pred_wait")
    return Detour(
        name=str(row["name"]),
        area=str(row["area"]) if pd.notna(row["area"]) else "",
        wait=int(row["wait_minutes"]),
        typical=int(round(row["fair_wait"])),
        saved=int(round(row["_saved"])),
        pred_wait=int(pred) if pd.notna(pred) else None,
    )
