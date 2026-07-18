"""アトラクション → エリア(ランド)対応。人圧マップのエリア分割に使う。

ThemeParks.wiki の /live 応答にはランド情報が含まれないため、名前ベースで best-effort に
割り当てる。網羅は目的ではなく「雑にエリア別で見える」ことが目的(Phase 1 DoD)。
未知の名前は AREA_UNKNOWN に落とす。実データで精度を上げたくなったら、将来
/entity/{id}/children を同期してここを置き換える(Phase 0 では取得していない)。
"""

from __future__ import annotations

AREA_UNKNOWN = "その他"

# 代表的な東京ディズニーランド/シーのアトラクション → ランド(illustrative)。
_NAME_TO_AREA: dict[str, str] = {
    # --- Tokyo Disneyland ---
    "Pooh's Hunny Hunt": "ファンタジーランド",
    "Enchanted Tale of Beauty and the Beast": "ファンタジーランド",
    "Haunted Mansion": "ファンタジーランド",
    "Western River Railroad": "アドベンチャーランド",
    "Pirates of the Caribbean": "アドベンチャーランド",
    "Splash Mountain": "クリッターカントリー",
    "Big Thunder Mountain": "ウエスタンランド",
    "Space Mountain": "トゥモローランド",
    "Star Tours: The Adventures Continue": "トゥモローランド",
    "Monsters, Inc. Ride & Go Seek!": "トゥモローランド",
    # --- Tokyo DisneySea ---
    "Soaring: Fantastic Flight": "メディテレーニアンハーバー",
    "Tower of Terror": "アメリカンウォーターフロント",
    "Toy Story Mania!": "アメリカンウォーターフロント",
    "Journey to the Center of the Earth": "ミステリアスアイランド",
    "Indiana Jones Adventure: Temple of the Crystal Skull": "ロストリバーデルタ",
    "Nemo & Friends SeaRider": "ポートディスカバリー",
}


def area_for(name: str | None) -> str:
    """アトラクション名からランドを返す。未知は AREA_UNKNOWN。"""
    if not name:
        return AREA_UNKNOWN
    return _NAME_TO_AREA.get(name, AREA_UNKNOWN)


def known_names() -> list[str]:
    """ランド割り当て済みの名前一覧(デモ生成器と共有する)。"""
    return list(_NAME_TO_AREA.keys())
