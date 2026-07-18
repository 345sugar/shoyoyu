# テストフィクスチャの出所(provenance)

> **⚠️ 実物(東京パークのライブ応答)未確認**
> 形状は実データで確認済みだが、東京パークの `/live` 実応答そのものは未取得。
> 個々のライドの `id`(UUID)・待ち時間の値は形状再現のための **合成値**。
> 実物での確定は `.github/workflows/fetch-fixtures.yml` を手動実行して採取した
> 実JSONで差し替えること(下記「実物への差し替え手順」)。

これらは **ThemeParks.wiki v1 API の実レスポンス形状**に基づく。ネットワーク非依存で
テストを回すための固定入力。フィールド名・ネスト構造・値の型は、以下の実データで確認した:

- **実形状の確認元**: ThemeParks 公式ライブラリが同梱する実キャプチャ
  `ThemeParks/ThemeParks_JavaScript` の
  `test/fixtures/destinations.json` と `test/fixtures/mk_live.json`
  (commit `6b8fad0`)、および OpenAPI 生成型 `src/_generated/schema.ts`。
- **実の東京パークID / デスティネーション**(そのまま使用):
  - Tokyo Disney Resort destination: `faff60df-c766-4470-8adb-dee78e813f42` (slug `tokyodisneyresort`)
  - Tokyo Disneyland: `3cc919f1-d16d-43e0-8c3f-1dd269bd1a42`
  - Tokyo DisneySea: `67b290d5-3478-4f23-b601-2f8fb71ba803`

`api.themeparks.wiki` への直接アクセスは本実行環境の egress ポリシーで遮断されていた
ため(403 CONNECT)、ライブ叩きの代わりに公式ライブラリの実キャプチャで形状を確定した。
個々のアトラクションの UUID は形状再現のための合成値だが、トップレベルのパーク/
デスティネーションID・全フィールド名・値の型・待ち行列の入れ子は実データ準拠。

## ファイル

- `destinations.json` — `/destinations`。東京デスティネーションと2パークを含む。
- `tdl_live.json` — Tokyo Disneyland の `/entity/{id}/live`。全 status と待ち行列種別を網羅。
- `tds_live.json` — Tokyo DisneySea の `/entity/{id}/live`(小さめ)。
- `live_malformed.json` — 2xx だが `liveData` を欠く仕様変更疑いレスポンス(欠測検知テスト用)。

## 実物への差し替え手順

1. GitHub の Actions から **fetch-fixtures** ワークフローを手動実行(`workflow_dispatch`)。
   ランナーが実APIを叩き、`captured/` を artifacts として保存する。
   - 採取物: `themeparks_destinations.json` / `themeparks_tdl_live.json` /
     `themeparks_tds_live.json` / `queue_times_parks.json`(+各 `status_*.txt`)
   - 注: 手動実行ボタンはワークフローがデフォルトブランチ(main)に載って初めて出る。
2. artifacts をダウンロードし、実JSONを本ディレクトリのフィクスチャに反映
   (`themeparks_tdl_live.json` → `tdl_live.json` など。冗長な要素は間引いてよい)。
3. 実物とフィールドがずれていたら `src/sabotage/data/normalize.py` を実形状へ更新し、
   `tests/` の期待値を合わせる。`pytest` が通ることを確認。
4. 上の「⚠️ 実物未確認」節を削除し、確認元を実採取(run ID 等)に更新する。

`sabotage-poll` が実APIに到達できる環境なら、`--once --discover` で1サイクル取得して
`snapshots.raw_json` を採取してもよい(同じ生JSONが得られる)。
