"""設定定数。フェーズをまたぐ先回り実装はしない(Phase 0 に必要なものだけ)。

データソースとパークIDは docs/roadmap.md 準拠。パークIDは実行時に /destinations
から発見してキャッシュする(discover 参照)が、発見に失敗しても 24時間回り続けられる
よう、ここに既知の既定値を持たせておく(欠測は観測、しかし停止は敗北)。
"""

from __future__ import annotations

from dataclasses import dataclass

# ThemeParks.wiki API(メイン・キー不要)
API_BASE = "https://api.themeparks.wiki/v1"

# データソース識別子(snapshots.source に入る)
SOURCE_THEMEPARKS = "themeparks.wiki"

# 非公式アグリゲータへの礼儀(CLAUDE.md データ倫理):User-Agent を明示する。
USER_AGENT = "sabotage-poller/0.1 (+https://github.com/345sugar/shoyoyu; personal-use)"

# ポーリング間隔。礼儀として5分(300秒)を下限とし、これ未満は許さない。
MIN_INTERVAL_SECONDS = 300
DEFAULT_INTERVAL_SECONDS = 300
# ジッター:同期した一斉アクセスを避けるため 0〜この秒数を毎サイクル上乗せする。
DEFAULT_JITTER_SECONDS = 45

# HTTP タイムアウト(秒)。遅延は前提。固まらせない。
HTTP_TIMEOUT_SECONDS = 20.0

# 既定のSQLite保存先(リポジトリ直下のdata/)。
DEFAULT_DB_PATH = "data/sabotage.db"


@dataclass(frozen=True)
class Park:
    """ポーリング対象パーク。"""

    park_id: str
    name: str


# 東京ディズニーリゾートの2パーク(実APIの /destinations 実形状から確認済み)。
# 発見に失敗した場合のフォールバック。roadmap の TDL ID と一致する。
TOKYO_DESTINATION_SLUG = "tokyodisneyresort"
DEFAULT_PARKS: tuple[Park, ...] = (
    Park(park_id="3cc919f1-d16d-43e0-8c3f-1dd269bd1a42", name="Tokyo Disneyland"),
    Park(park_id="67b290d5-3478-4f23-b601-2f8fb71ba803", name="Tokyo DisneySea"),
)

# meta テーブルで発見済みパークをキャッシュするキー。
META_KEY_PARKS = "tokyo_parks"
