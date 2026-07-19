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

#### スマホ一台で動かす(Android / Termux)

`sabotage-mobile` が **ポーラー(5分+ジッター)と現況ボードを同時に起動**し、同じ SQLite を
共有する(WAL なので書き込み中でも読める)。停止時はプロセスグループごと畳むので孤児が残らない。

```bash
# Termux(Android)での初回セットアップ
pkg install python git
git clone https://github.com/345sugar/shoyoyu && cd shoyoyu
pip install -e '.[viz]'

# 起動(これ1つ)
sabotage-mobile --db data/sabotage.db
# → 端末のブラウザで http://localhost:8501/ を開く。Ctrl-C(音量下+C)で両方停止。
```

> - 間隔は CLAUDE.md 準拠の **5分以上**(1分間隔は非公式APIへの礼儀・規約に反するため不可)。
> - `localhost` 束縛・非公開(「取得データは私的利用の範囲」)。自分の端末でのみ見る。
> - iOS はバックグラウンド常駐が難しく非推奨。常時稼働は Android/Termux か、自宅PC/VPSで
>   `sabotage-poll --loop` を回して端末からボードだけ見る構成が安定。
> - 補足: Termux で pandas/streamlit の導入が重い場合は `pkg install python-numpy` 等の
>   プリビルドを併用する。

手動で別々に起動することもできる:

```bash
sabotage-poll --loop --db data/sabotage.db
streamlit run src/sabotage/viz/board.py -- --db data/sabotage.db
```

#### 常時稼働で5分ライブ(VPS / 自宅Pi)

24時間動かして「5分ライブのURL(自分だけ)」にするなら [`deploy/`](deploy/README.md) の
Docker / systemd 一式を使う。常時稼働の箱で `docker compose up -d` するだけ:

```bash
cd deploy && docker compose up -d      # ポーラー(5分)+ボードが常駐、127.0.0.1:8501
```

スマホからは Tailscale 等で自分だけ到達する(公開はしない)。詳細は
[deploy/README.md](deploy/README.md)。

## Phase 2: 人流ナウキャスティング(骨組み)

待ち時間は遅行指標。**「今の表示値」ではなく「到着した頃の待ち」**を出す(群衆補正込みの
到着時予測)。CLAUDE.md 心理設計原則1「自壊する信号」の実装 — 魅力的な低い数字ほど速く埋まる。

- **到着時予測 `herd_adjusted_wait`**(`analysis/nowcast.py`): 平均回帰モデル。
  乖離(現在 − 平常値)を τ(群衆弾性)で減衰させ、到着時刻の平常値に足す。履歴が薄いうちは
  直近トレンド(モメンタム)にフォールバック。
- **現況ボードに統合**: 各アトラクションに **「着N分 ↗/↘」** を表示。
  今15分でも「着30分↗(今の低さは罠)」/ 今55分でも「着40分↘(待てば空く)」を出せる。
  スライダーで到着まで分を変えられる。
- **バックテスト**(`sabotage-backtest`): 到着時MAE を素朴予測(現状維持)と比較し、
  スパイク検知率を出す(Phase 2 DoD)。

```bash
sabotage-backtest --db data/sabotage.db --arrival 20   # 数字はデータが数日貯まってから意味を持つ
```

> **正直な但し書き**: 予測ロジックは実装済み・単体テスト済みだが、**当たるかは実データを
> 数日〜数週間貯めてから**(毎時フライホイール/5分ポーラーがその燃料)。平常値は現状
> 全履歴から算出しており厳密には楽観値。まずは素朴予測に勝てるかが判断材料。

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
