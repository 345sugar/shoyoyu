# sabotage 5分ライブ(ポーラー + 現況ボード)を常時稼働させるイメージ。
# 常時稼働の箱(VPS / 自宅Pi / サーバー)で `docker compose up -d` する想定。
FROM python:3.11-slim

WORKDIR /app

# 依存を先に入れてレイヤキャッシュを効かせる。
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[viz]'

# SQLite はボリュームに置いて永続化(コンテナ再作成でもデータを残す)。
ENV SABOTAGE_DB=/data/sabotage.db
VOLUME ["/data"]
EXPOSE 8501

# 現況ボードのヘルスチェック(落ちたら unhealthy)。
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health',timeout=3).status==200 else 1)" || exit 1

# ポーラー(--loop, 5分+ジッター)と現況ボードを同時起動。0.0.0.0 束縛で
# コンテナ外へポート公開可能に(compose 側で 127.0.0.1 に絞る)。
CMD ["sabotage-mobile", "--db", "/data/sabotage.db", "--address", "0.0.0.0", "--port", "8501", "--interval", "300", "--jitter", "45"]
