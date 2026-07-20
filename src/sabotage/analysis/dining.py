"""食事どきナビ — 「いつ・どこで食べると得か」を出す(Phase 2 の実用化)。

思想的背景(CLAUDE.md 目的関数):最適化対象は「乗った数」ではなく、着席・飲食時間の
最大化と「最高の一瞬×最高の締め」。だから食事は"余り"ではなく戦術。混雑ピークは並ぶのが
最も損なので、その時間帯こそ座って食べる。雨が来るなら屋外で立ち往生する前に室内へ移す。

データの正直な限界:
- 非公式API(ThemeParks.wiki)は東京のレストランを持たない(/children で確認、0件)。
  公式アプリのリバースエンジニアリングは禁止(CLAUDE.md)。よって「今この店が何分待ち/空席」
  はリアルタイムには取れない。
- そこでこのモジュールは (1) 店の**静的な参考メタデータ**(名前・エリア・室内か・提供形態)を
  自前で持ち、(2) それを**実データ(人流の人圧・天気)**に掛け合わせて「食べ時/室内へ」の
  判断だけを出す。時刻・メニュー・空席は出さない(無いものは出さない=自壊する信号を作らない)。
- 店データは参考(改装・閉店・営業変更はあり得る)。RESTAURANTS のコメント参照。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# 人圧パーセンタイルの区切り(今の混み具合を「空/並/混」に3分割)。
LOW_PCTL = 0.34
HIGH_PCTL = 0.66
# この件数未満の人圧履歴では混雑判定をしない(薄いデータで断言しない)。
_MIN_PRESSURE_SAMPLES = 4
# 「まもなく雨」で室内を急ぐ降水確率(board の RAIN_ALERT_PROB と揃える)。
RAIN_URGENT_PROB = 50
# この気温以上は暑さ回避で室内を勧める一言を足す。
HOT_TEMP_C = 30.0

TDL_ID = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"
TDS_ID = "67b290d5-3478-4f23-b601-2f8fb71ba803"


@dataclass(frozen=True)
class Restaurant:
    """レストランの静的な参考メタデータ(実データではない)。

    indoor=True は「主に屋内の座席がある」。proximity/雨対策の判断に使う。
    service: テーブル(予約/案内あり)/ カウンター(食券・トレー)/ ビュッフェ / ショー(食事+ショー)。
    """

    name: str
    park_id: str
    area: str
    indoor: bool
    service: str
    note: str = ""


# 東京ディズニーランド/シーの代表的レストラン(参考データ。改装・営業変更あり得る)。
# エリア名は analysis/areas.py のランド名に極力合わせている(将来の突合のため)。
# indoor は主要な座席が屋内かどうかの目安(雨・暑さ回避の判断材料)。
RESTAURANTS: tuple[Restaurant, ...] = (
    # --- Tokyo Disneyland ---
    Restaurant("クリスタルパレス・レストラン", TDL_ID, "ワールドバザール", True, "ビュッフェ", "ブッフェ形式・室内"),
    Restaurant("イーストサイド・カフェ", TDL_ID, "ワールドバザール", True, "テーブル", "パスタ・室内・案内制"),
    Restaurant("れすとらん北齋", TDL_ID, "ワールドバザール", True, "テーブル", "和食・室内"),
    Restaurant("センターストリート・コーヒーハウス", TDL_ID, "ワールドバザール", True, "テーブル", "洋食・室内"),
    Restaurant("ブルーバイユー・レストラン", TDL_ID, "アドベンチャーランド", True, "テーブル", "カリブの海賊内・室内・案内制"),
    Restaurant("ザ・ガゼーボ", TDL_ID, "アドベンチャーランド", False, "ワゴン", "屋外・軽食"),
    Restaurant("ハングリーベア・レストラン", TDL_ID, "ウエスタンランド", True, "カウンター", "カレー・室内席多め"),
    Restaurant("プラザパビリオン・レストラン", TDL_ID, "ウエスタンランド", True, "カウンター", "室内/テラス"),
    Restaurant("グランマ・サラのキッチン", TDL_ID, "クリッターカントリー", True, "カウンター", "煮込み系・室内"),
    Restaurant("クイーン・オブ・ハートのバンケットホール", TDL_ID, "ファンタジーランド", True, "カウンター", "不思議の国・室内"),
    Restaurant("トゥモローランド・テラス", TDL_ID, "トゥモローランド", True, "カウンター", "大型・室内席多め・雨に強い"),
    Restaurant("プラズマ・レイズ・ダイナー", TDL_ID, "トゥモローランド", True, "カウンター", "丼・室内"),
    Restaurant("ポリネシアンテラス・レストラン", TDL_ID, "アドベンチャーランド", True, "ショー", "食事+ショー・要予約"),
    # --- Tokyo DisneySea ---
    Restaurant("マゼランズ", TDS_ID, "メディテレーニアンハーバー", True, "テーブル", "コース・室内・要予約級"),
    Restaurant("リストランテ・ディ・カナレット", TDS_ID, "メディテレーニアンハーバー", True, "テーブル", "イタリアン・室内"),
    Restaurant("ザンビーニ・ブラザーズ・リストランテ", TDS_ID, "メディテレーニアンハーバー", True, "カウンター", "パスタ/ピザ・室内席多め"),
    Restaurant("カフェ・ポルトフィーノ", TDS_ID, "メディテレーニアンハーバー", True, "カウンター", "洋食・室内"),
    Restaurant("ヴォルケイニア・レストラン", TDS_ID, "ミステリアスアイランド", True, "カウンター", "中華・室内・雨に強い"),
    Restaurant("セイリングデイ・ブッフェ", TDS_ID, "アメリカンウォーターフロント", True, "ビュッフェ", "ブッフェ・室内"),
    Restaurant("S.S.コロンビア・ダイニングルーム", TDS_ID, "アメリカンウォーターフロント", True, "テーブル", "コース級・室内"),
    Restaurant("ケープコッド・クックオフ", TDS_ID, "アメリカンウォーターフロント", True, "カウンター", "ダッフィーのショー併設・室内"),
    Restaurant("ニューヨーク・デリ", TDS_ID, "アメリカンウォーターフロント", True, "カウンター", "サンド・室内"),
    Restaurant("ホライズンベイ・レストラン", TDS_ID, "ポートディスカバリー", True, "カウンター", "洋食・室内・雨に強い"),
    Restaurant("ユカタン・ベースキャンプ・グリル", TDS_ID, "ロストリバーデルタ", False, "カウンター", "スモーク肉・主にテラス"),
    Restaurant("ミゲルズ・エルドラドキャンティーナ", TDS_ID, "ロストリバーデルタ", False, "カウンター", "メキシカン・主にテラス"),
)


def restaurants(park_id: str, *, indoor_only: bool = False) -> list[Restaurant]:
    """パークのレストラン一覧(参考データ)。indoor_only=True で室内席ありに絞る。"""
    out = [r for r in RESTAURANTS if r.park_id == park_id]
    if indoor_only:
        out = [r for r in out if r.indoor]
    return out


def _percentile_rank(value: float, series: list[float]) -> float:
    """value が series の中で下から何割の位置か(0..1)。"""
    if not series:
        return 0.5
    below = sum(1 for x in series if x <= value)
    return below / len(series)


@dataclass
class MealAdvice:
    """食事どき判定の結果。"""

    mode: str          # "eat"(食べ時) / "ride"(乗り時) / "neutral" / "rain"
    headline: str      # 一言(執事の指示調)
    detail: str        # 理由
    indoor_urgent: bool  # 雨/暑さで室内を急ぐべきか


def meal_timing(
    current_pressure: float | None,
    pressure_series: list[float],
    weather: dict | None,
) -> MealAdvice:
    """今が食べ時かを判定する純関数。

    優先順:
    1. まもなく雨 → 立ち往生する前に室内で座って食事(最優先・indoor_urgent)。
    2. 人圧が高い(混雑ピーク)→ 並ぶのが最も損。座って食べるのが得(eat)。
    3. 人圧が低い(空いている)→ 乗るなら今。食事は混む時間へ回す(ride)。
    4. 履歴が薄い/ふつう → neutral。
    暑さ(HOT_TEMP_C以上)は室内を勧める一言を添える。
    """
    prob = (weather or {}).get("precip_prob")
    temp = (weather or {}).get("temp_c")
    hot = isinstance(temp, (int, float)) and temp >= HOT_TEMP_C

    # 1. 雨の再配分(最優先)。
    if isinstance(prob, (int, float)) and prob >= RAIN_URGENT_PROB:
        return MealAdvice(
            mode="rain",
            headline="☔️ 今のうちに室内で食事へ",
            detail=f"まもなく雨(降水{int(prob)}%)。屋外で立ち往生する前に、"
            "室内レストランへ先に入って座るのが得。",
            indoor_urgent=True,
        )

    # 2/3. 人圧による判定(履歴が十分あるときだけ)。
    if current_pressure is not None and len(pressure_series) >= _MIN_PRESSURE_SAMPLES:
        rank = _percentile_rank(current_pressure, pressure_series)
        if rank >= HIGH_PCTL:
            return MealAdvice(
                mode="eat",
                headline="🍽️ 今は食べ時(園内が混雑ピーク)",
                detail="いま園全体が混んでいる=どこも並ぶのが最も損な時間帯。"
                "並ばず座って食事・休憩に充てるのが得。"
                + ("暑いので室内がおすすめ。" if hot else ""),
                indoor_urgent=hot,
            )
        if rank <= LOW_PCTL:
            return MealAdvice(
                mode="ride",
                headline="🎢 今は乗り時(園内が空いている)",
                detail="いま園全体が空いている。乗るなら今。"
                "食事は混雑ピークの時間帯に回すと、待ち時間の損を食事で相殺できる。",
                indoor_urgent=False,
            )
        return MealAdvice(
            mode="neutral",
            headline="🍽️ 食事は好きなタイミングで",
            detail="いまの混み具合はふつう。"
            + ("暑いので休むなら室内が快適。" if hot else "空く時間を待つほどの差は無い。"),
            indoor_urgent=hot,
        )

    # 4. 履歴が薄い。
    return MealAdvice(
        mode="neutral",
        headline="🍽️ 食事どきナビ(準備中)",
        detail="人圧の履歴がまだ薄いので、混雑ピーク判定はもう少しデータが要ります"
        "(数時間〜数日で効きます)。"
        + ("いまは暑いので室内で座るのがおすすめ。" if hot else ""),
        indoor_urgent=hot,
    )
