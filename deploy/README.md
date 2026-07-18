# 5分ライブを常時稼働させる(デプロイ)

現況ボードを **5分間隔のライブ**で使うには、24時間動き続ける箱が1つ必要。
このディレクトリはそれを「1コマンドで立てる」ための道具一式。

> **なぜ箱が要るのか**: ポーラー(5分ごとに実データ取得)は誰かが常時回す必要がある。
> GitHub Actions は最短5分+遅延あり、Claude は常駐しない。よって
> **VPS / 自宅Pi / 常時起動PC / Android(Termux)** のどれかで回すのが唯一の5分ライブ解。
>
> **iPhone 単体は不可**: iOS はバックグラウンド常駐を許さず、iSH/a-Shell はロック/切替で
> 凍結する(5分ループが止まる)。iPhone は「Tailscale + Safari で開くだけの閲覧役」に徹し、
> ポーラーは VPS/Pi で回すのが正解。

## 方法A: Docker(推奨・どの箱でも同じ)

```bash
git clone https://github.com/345sugar/shoyoyu && cd shoyoyu/deploy
docker compose up -d
# → http://127.0.0.1:8501/ で現況ボード。5分ごとに更新。
docker compose logs -f      # 動作確認
docker compose down         # 停止
```

- SQLite は名前付きボリューム `sabotage-data` に永続化(再作成でもデータが残る)。
- 既定は **`127.0.0.1` 束縛**(=そのホスト内 or トンネル経由のみ)。公開しない。

### 最小VPS クイックスタート(Ubuntu + iPhone 閲覧)

VPS(Ubuntu 22.04+ を想定)に SSH で入って、以下をコピペ:

```bash
# 1) Docker と git
sudo apt update && sudo apt install -y docker.io git
sudo systemctl enable --now docker

# 2) 起動(5分ポーラー + ボード。127.0.0.1:8501 束縛のまま=非公開)
git clone https://github.com/345sugar/shoyoyu && cd shoyoyu/deploy
sudo docker compose up -d
sudo docker compose logs -f     # 5分ごとに取得しているのを確認

# 3) Tailscale で「自分だけ」到達(公開せずに iPhone から見る)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale serve --bg 8501  # localhost:8501 を tailnet 内だけに HTTPS 公開
sudo tailscale serve status     # 表示された https://<マシン>.<tailnet>.ts.net を控える
```

iPhone 側:
1. App Store で **Tailscale** を入れ、VPS と**同じアカウント**でログイン。
2. Safari で `tailscale serve status` に出た **`https://<マシン>.<tailnet>.ts.net/`** を開く。
3. ホーム画面に追加すればアプリ感覚。**開くだけ**で5分ライブの現況ボード。

> `tailscale serve` を使うのは、compose が `127.0.0.1` 束縛のため。素の `http://…:8501` は
> tailnet からは届かない(だから serve でトンネルする)。公開インターネットには出ない。

## 方法B: systemd(Docker 無しの Pi / Linux)

`deploy/sabotage.service` の先頭コメントの手順どおり。`sabotage-mobile` をユーザーサービス
として常駐させる。

## スマホから見る(公開せずに)

取得データは私的利用の範囲(CLAUDE.md)。**公開URLにはしない**。自分の端末からだけ届く
プライベート経路にする:

- **Tailscale(推奨)**: 箱で `tailscale serve --bg 8501`(上記クイックスタート参照)。
  スマホは同じ tailnet に入り、`https://<マシン>.<tailnet>.ts.net/` を開くだけ。公開されない。
- **SSH ポートフォワード**: `ssh -L 8501:127.0.0.1:8501 user@箱` してPC側 `localhost:8501`。
- どうしても公開する場合のみ compose の `ports` を `0.0.0.0:8501:8501` に変え、
  認証(リバースプロキシ + Basic認証等)を必ず前段に置く。ただし非公式APIデータの公開は
  規約的にグレーなので非推奨。

## 費用の目安

- 自宅Pi / 余ってるPC: 実質0円(電気代のみ)。
- 最小VPS: 月数百円〜。常時稼働で安定。

## 注意

- 間隔は **5分固定**(CLAUDE.md「5分間隔以上」。1分は入れない)。
- 初回起動直後は履歴が無いので、波形やフェアバリューは数十分〜数時間で育つ。
  現況(現在待ち・停止)は最初の1サイクル(数十秒)で出る。
