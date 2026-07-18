# data ブランチ(git scraping 出力・自動生成)

このブランチは **`scrape` ワークフローが自動でコミットするデータ専用**の orphan ブランチ。
コードは含まない。`main` の履歴を汚さないために分けている。

- 中身: `data/themeparks/live/<park_id>/<UTC日付>.ndjson`
- 1行 = 1スナップショット(Phase 0 の snapshots 同型):
  `{"ts","source","park_id","http_status","raw", ...}`
- 生成元: `.github/workflows/scrape.yml`(毎時)/ `sabotage-scrape`
- SQLite への取り込み: `sabotage-backfill --data data --db sabotage.db`

手で編集しない(毎時の自動コミットが積み上がる)。
