# テストフィクスチャの出所(provenance)

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

egress が開通したら `sabotage-poll --once --discover` で本物を1サイクル取得し、
`snapshots.raw_json` を新しいフィクスチャとして採取して差し替えること。

## ファイル

- `destinations.json` — `/destinations`。東京デスティネーションと2パークを含む。
- `tdl_live.json` — Tokyo Disneyland の `/entity/{id}/live`。全 status と待ち行列種別を網羅。
- `tds_live.json` — Tokyo DisneySea の `/entity/{id}/live`(小さめ)。
- `live_malformed.json` — 2xx だが `liveData` を欠く仕様変更疑いレスポンス(欠測検知テスト用)。
