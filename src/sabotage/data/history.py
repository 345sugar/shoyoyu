"""起動時ヒストリ・ブートストラップ。

問題: Streamlit Community Cloud の DB は揮発(再デプロイのたびに空)。裏の5分ポーラーは
その場から積み直すので、到着時予測(平常回帰)が「履歴が薄い→flat(現在値のまま)」に
戻ってしまう。一方で `data` ブランチには git-scraping の毎時フライホイールが1ヶ月分
(数百スナップショット)を貯めている。この宝を使わない手はない。

解決: 起動時に `data` ブランチの NDJSON を GitHub raw(public リポジトリなので認証不要)から
取り込み、既存の backfill で揮発 DB に流し込む。これで初回描画から平常回帰予測が効く。

設計方針(CLAUDE.md):過剰な抽象化はしない。ネットワーク失敗は前提(落ちても self-poll で
動く)。DB に十分な観測が既にあるならスキップ(冪等・毎回フェッチしない)。
"""

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import httpx

from ..tools.backfill import backfill, backfill_weather
from .storage import Storage

# data ブランチの生ファイル基点(public。private だと raw は 404 になる点に注意)。
RAW_BASE = "https://raw.githubusercontent.com/345sugar/shoyoyu/data/data"
PARK_IDS = (
    "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42",  # TDL
    "67b290d5-3478-4f23-b601-2f8fb71ba803",  # TDS
)
# フライホイール初回コミット日(この日より前のファイルは存在しない)。
HISTORY_START = dt.date(2026, 7, 18)
_UA = "sabotage-bootstrap/0.1 (+https://github.com/345sugar/shoyoyu; personal-use)"
# これ以上の観測が既に DB にあればブートストラップ不要(自前ポーラーで育っている)。
DEFAULT_MIN_OBS = 1500


def _date_range(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def _relpaths_for(day: dt.date) -> list[str]:
    ds = day.isoformat()
    rels = [f"themeparks/live/{pid}/{ds}.ndjson" for pid in PARK_IDS]
    rels.append(f"weather/open-meteo/{ds}.ndjson")
    return rels


def fetch_history(
    dest_dir: str | Path,
    *,
    start: dt.date = HISTORY_START,
    end: dt.date | None = None,
    client: httpx.Client | None = None,
    timeout: float = 8.0,
) -> int:
    """data ブランチの NDJSON を GitHub raw から dest_dir にミラーする。取得ファイル数を返す。

    欠けている日(未来・未取得)は 404 として黙って飛ばす。ネットワーク例外も1ファイル単位で
    握りつぶす(部分的にでも取れたぶんは活かす)。client 差し込みでテスト可能。
    """
    end = end or dt.date.today()
    dest = Path(dest_dir)
    owns = client is None
    if client is None:
        client = httpx.Client(timeout=timeout, headers={"User-Agent": _UA})
    got = 0
    try:
        for day in _date_range(start, end):
            for rel in _relpaths_for(day):
                url = f"{RAW_BASE}/{rel}"
                try:
                    r = client.get(url)
                except Exception:  # noqa: BLE001 — 1ファイルの失敗で全体を止めない
                    continue
                if r.status_code == 200 and r.text.strip():
                    p = dest / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(r.text, encoding="utf-8")
                    got += 1
    finally:
        if owns:
            client.close()
    return got


def _observation_count(db_path: str) -> int:
    try:
        from ..analysis import queries

        return len(queries.load_observations(queries.connect(db_path)))
    except Exception:  # noqa: BLE001 — DB 未作成など
        return 0


def bootstrap_history(
    db_path: str,
    *,
    min_observations: int = DEFAULT_MIN_OBS,
    start: dt.date = HISTORY_START,
    end: dt.date | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """必要なら data ブランチ履歴を取り込む。結果サマリ(dict)を返す。

    既に min_observations 以上あるならフェッチせずスキップ(冪等)。
    """
    have = _observation_count(db_path)
    if have >= min_observations:
        return {"skipped": True, "reason": "enough-data", "observations": have}

    with tempfile.TemporaryDirectory(prefix="sabotage-hist-") as tmp:
        files = fetch_history(tmp, start=start, end=end, client=client)
        if files == 0:
            return {"skipped": True, "reason": "no-files-fetched", "observations": have}
        with Storage(db_path) as s:
            r_live = backfill(s, tmp)
            r_wx = backfill_weather(s, tmp)

    # backfill 側の統計は `skipped` 等のキーが衝突するので、明示的に取り出す(spread しない)。
    return {
        "skipped": False,
        "files": files,
        "observations": r_live.get("observations", 0),
        "snapshots": r_live.get("snapshots", 0),
        "weather": r_wx.get("weather", 0),
        "observations_before": have,
    }
