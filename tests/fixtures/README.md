# テストフィクスチャの出所(provenance)

これらは **ThemeParks.wiki v1 API の実応答**。ネットワーク非依存でテストを回すための固定入力。

- **採取方法**: GitHub Actions の `fetch-fixtures` ワークフロー(`.github/workflows/fetch-fixtures.yml`)を
  手動実行し、ランナーから実APIを叩いて採取(**2026-07-18 採取**、全エンドポイント HTTP 200)。
  開発サンドボックスは egress ポリシーで実APIに出られないため、Actions 経由で採取している。
- **実の東京パークID / デスティネーション**(実応答のまま):
  - Tokyo Disney Resort destination: `faff60df-c766-4470-8adb-dee78e813f42`(**slug `tdr`**)
  - Tokyo Disneyland: `3cc919f1-d16d-43e0-8c3f-1dd269bd1a42`(実応答で 37 アトラクション)
  - Tokyo DisneySea: `67b290d5-3478-4f23-b601-2f8fb71ba803`(実応答で 34 アトラクション)

## 実応答から分かった要点(正規化の根拠)

- 東京の `/live` は(採取時点で)全エンティティが `entityType="ATTRACTION"`。
  status は `OPERATING` / `CLOSED` / `DOWN`。
- **停止・休止中は `queue.STANDBY` が空 `{}`**(`waitTime` キー自体が無い)。
  `null` ではない。`normalize._extract_standby_wait` はキー欠落を None として扱うので問題なし。
- 一部に `RETURN_TIME` / `PAID_RETURN_TIME`(`price.amount=0, formatted="Unknown"` あり)。
  Phase 0 では待ち時間は `STANDBY` のみを採用し、他の待ち行列は生JSON(snapshots)に温存。
- `destination.slug` は実応答では **`tdr`**(公式ライブラリの過去キャプチャは `tokyodisneyresort`
  だった)。発見は slug と名前("Tokyo Disney" 部分一致)の両方で照合する(`config.py`)。

## ファイル

- `destinations.json` — `/destinations`(実応答から東京+1件に間引き)。東京の slug=`tdr`。
- `tdl_live.json` — Tokyo Disneyland の `/entity/{id}/live` 実応答(37エンティティ)。
- `tds_live.json` — Tokyo DisneySea の `/entity/{id}/live` 実応答(34エンティティ)。
- `live_malformed.json` — 2xx だが `liveData` を欠く仕様変更疑いレスポンス(**合成**・欠測検知テスト用)。
- `open_meteo_forecast.json` — Open-Meteo `/forecast`(舞浜)の実形状。**Open-Meteo の公開仕様に
  基づく実形状**で、`current`(気温・降水・weather_code)+ `hourly`(降水確率など24時間分)を持つ。
  `fetch-fixtures` に Open-Meteo 採取ステップを追加済み(実物での再確認・差し替え用)。
  降水確率は「今以降2時間の最大」を採る(`data/weather.py`)ので、12:00 時点で 13:00 の 60% を拾う。

## 再採取

内容が古くなったら `fetch-fixtures` を再実行して差し替える。採取物は `captured-fixtures`
ブランチ(git 経由)と Actions artifacts の両方に出る。参考として Queue-Times の
Tokyo パークID(TDL=274 / TDS=275、company "Walt Disney Attractions")も同時採取される
(Phase 0 コードでは未使用)。
