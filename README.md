# sabotage(サボタージュ)

東京ディズニーリゾートを「効率よくだらだら」過ごすためのAI執事。
思想と原則は [CLAUDE.md](CLAUDE.md)、全体計画は [docs/roadmap.md](docs/roadmap.md) を参照。

現在の実装状況: **Phase 0(データフライホイール)** — ポーラー + SQLite蓄積。

## Phase 0: データフライホイール

ThemeParks.wiki API(非公式・キー不要)から東京2パークの待ち時間ライブデータを
定期取得し、生JSONと正規化観測をSQLiteへ蓄積する。すべての後続フェーズの燃料。

- 生レスポンスを丸ごと `snapshots` に保存(正規化スキーマの設計ミスから常に復旧できる)
- 正規化済み観測を `observations` に保存
- 発見したパークIDを `meta` にキャッシュ(発見失敗でも既定値で回り続ける)
- 取得失敗・欠測は `status='FETCH_FAILED'` の1行として記録(**欠測は観測である**)
- SQLiteは **WALモード**。1サイクル内のどの例外もループを殺さない
- ポーリングは5分間隔以上+ジッター、User-Agent明示(非公式アグリゲータへの礼儀)

### インストール

```bash
pip install -e .          # 実行のみ
pip install -e '.[dev]'   # テストも回す場合(pytest)
```

### 使い方

```bash
# 1サイクルだけ取得して終了(cron 向け)
sabotage-poll --once --db data/sabotage.db

# 常駐して回し続ける(5分間隔+ジッター)
sabotage-poll --loop

# 起動時に /destinations でパークIDを再発見してキャッシュ更新
sabotage-poll --once --discover
```

cron 例(5分間隔):

```cron
*/5 * * * * cd /path/to/shoyoyu && sabotage-poll --once >> poll.log 2>&1
```

主なオプション: `--db PATH`(保存先)/ `--interval SECS`(下限300)/ `--jitter SECS`
/ `--discover` / `--log-level`。

### テーブル

| テーブル | 内容 |
| --- | --- |
| `snapshots(ts, source, park_id, http_status, raw_json)` | 生レスポンスを丸ごと |
| `observations(ts, park_id, entity_id, name, entity_type, status, wait_minutes)` | 正規化済み |
| `meta(key, value)` | 発見したパークIDのキャッシュ |

### テスト

ネットワーク非依存。ThemeParks.wiki の**実レスポンス形状**に基づくフィクスチャで駆動する
(出所は [tests/fixtures/README.md](tests/fixtures/README.md))。

```bash
pytest
```

### データ利用について

本プロジェクトは ThemeParks.wiki の非公式APIを私的利用の範囲で参照する。
公式アプリ/APIのリバースエンジニアリングは行わない(CLAUDE.md データ倫理参照)。
