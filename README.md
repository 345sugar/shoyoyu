# sabotage(サボタージュ)

東京ディズニーリゾートを「効率よくだらだら」過ごすためのAI執事。
思想と原則は [CLAUDE.md](CLAUDE.md)、全体計画は [docs/roadmap.md](docs/roadmap.md) を参照。

現在の実装状況: **Phase 0(データフライホイール)** + **Phase 1(可視化)**。

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

## 暫定フライホイール(git scraping)

本命の5分間隔ポーラーを常時稼働マシンで回すまでの繋ぎ。GitHub Actions の cron で
**1時間ごと**に両パークの `/live` を1回取得し、生JSONを NDJSON として **`data` ブランチ**へ
コミットする([`.github/workflows/scrape.yml`](.github/workflows/scrape.yml))。

- 間隔は**1時間固定**(非公式APIへの礼儀 + Actions 無料枠)。User-Agent 明示。
- 出力: `data/themeparks/live/<park_id>/<UTC日付>.ndjson`。1行 = 1スナップショット、
  列は Phase 0 の `snapshots` と同型 `{ts, source, park_id, http_status, raw}`。
- 取得失敗も `http_status:0` の行として残す(欠測は観測)。
- **後日の移行**: 本命ポーラーへ切り替える際、この git 履歴を SQLite へバックフィルできる。

```bash
sabotage-scrape --out data                          # 1回取得して NDJSON へ追記(cron が毎時実行)
sabotage-backfill --data data --db data/sabotage.db # NDJSON → SQLite(冪等。再実行で重複しない)
```

`data` ブランチはデータ専用の orphan ブランチ(コードは含まない)。`main` の履歴は汚さない。

## Phase 1: 可視化

蓄積データを Streamlit で「雑に見る」。DoD は「ブラウザで**昨日の園内**が見える」。

- **待ち時間波形** — アトラクション別・1日分の折れ線
- **曜日 × 時間帯ヒートマップ** — 全期間の平均待ち
- **人圧マップ** — 待ち時間総和(=園内需要の相対指標)を全体+エリア別に。
  停止・改修中のアトラクション一覧も併記(木鶏の材料)
- 数字づくりは `analysis` 層(テスト済み純関数)、描画は `viz` 層(Streamlit)に分離

```bash
pip install -e '.[viz]'                       # 可視化の依存(streamlit/pandas/altair)
streamlit run src/sabotage/viz/app.py -- --db data/sabotage.db
```

### 実データが無い環境向け:合成デモデータ

実APIが遮断されている等でまだ実データが無い場合、**合成デモデータ**で可視化を試せる
(⚠️ 本物の待ち時間ではない。`snapshots.source='demo-synthetic'` で区別・削除可能)。

```bash
sabotage-seed-demo --db data/sabotage.db --days 3   # 直近3日分の合成データを投入
sabotage-seed-demo --db data/sabotage.db --purge    # 合成データだけ削除(実データは残る)
```

アプリ上部には合成データ表示中の警告バナーが出る。

### 当日スマホ: 現況ボード

その場で「今どうなってる?」を見るスマホ向け1画面(`viz/board.py`)。最新スナップショットを
**待ち短い順(=穴場優先。立ち待ちは損失)**で並べ、停止・休止は下部にまとめる。トレンド矢印
(直近比)と、履歴が溜まれば割安/割高バッジ、データ鮮度も表示する。

```bash
streamlit run src/sabotage/viz/board.py -- --db data/sabotage.db
```

**当日リアルタイムに使うには**、同じ DB に実データが5分ごとに入り続けている必要がある。
本命の5分間隔ポーラー([実装済み](#phase-0-データフライホイール))を、スマホや常時稼働機で
回して board を同じ DB に向ける:

```bash
sabotage-poll --loop --db data/sabotage.db          # 端末側で常時稼働(5分+ジッター)
streamlit run src/sabotage/viz/board.py -- --db data/sabotage.db
```

> 間隔は CLAUDE.md 準拠の **5分以上**(1分間隔は非公式APIへの礼儀・規約に反するため不可)。
> 公開ホスティングは「取得データは私的利用の範囲」に留めるため避け、自分の端末で見る。

### テスト

ネットワーク非依存。ThemeParks.wiki の**実レスポンス形状**に基づくフィクスチャで駆動する
(出所は [tests/fixtures/README.md](tests/fixtures/README.md))。

```bash
pytest
```

### 実APIからのフィクスチャ採取(GitHub Actions)

開発環境から実APIに出られない場合に備え、Actions のランナーから実レスポンスを採取する
手動ワークフロー [`.github/workflows/fetch-fixtures.yml`](.github/workflows/fetch-fixtures.yml)
を用意している。`workflow_dispatch` で実行すると、TDL/TDS の `/live`・`/destinations`・
Queue-Times の `parks.json` を取得して artifacts に保存する。採取した実JSONで
フィクスチャと正規化を実物に合わせて更新する(手順は fixtures の README 参照)。

### データ利用について

本プロジェクトは ThemeParks.wiki の非公式APIを私的利用の範囲で参照する。
公式アプリ/APIのリバースエンジニアリングは行わない(CLAUDE.md データ倫理参照)。
