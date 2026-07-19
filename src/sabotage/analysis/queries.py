"""DB(observations)を pandas DataFrame として読み出す。

タイムスタンプは保存時 UTC。表示は東京時間なので、ここで Asia/Tokyo に変換し、
date / hour / weekday の派生列を付ける。以降の集計はすべてこの DataFrame 上で行う。
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pandas as pd

from ..config import DEFAULT_PARKS, META_KEY_PARKS
from ..data.storage import STATUS_FETCH_FAILED

DEFAULT_TZ = "Asia/Tokyo"

_OBS_COLUMNS = ["ts", "park_id", "entity_id", "name", "entity_type", "status", "wait_minutes"]
# 集計対象外の「観測できなかった」ステータス。
_NON_OBSERVED = {STATUS_FETCH_FAILED}

# 曜日を月→日で並べるための順序(ヒートマップの行順)。
WEEKDAY_ORDER = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def connect(db_path: str | Path) -> sqlite3.Connection:
    """読み取り用に接続する。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def load_observations(
    conn: sqlite3.Connection,
    *,
    park_id: str | None = None,
    tz: str = DEFAULT_TZ,
) -> pd.DataFrame:
    """observations を DataFrame で返す(ts_local / date / hour / weekday 付き)。

    欠測(FETCH_FAILED)行は除外する。空でも列は揃えて返す。
    """
    df = pd.read_sql_query(
        f"SELECT {', '.join(_OBS_COLUMNS)} FROM observations", conn
    )
    if df.empty:
        for extra in ("ts_local", "date", "hour", "weekday"):
            df[extra] = pd.Series(dtype="object")
        return df

    df = df[~df["status"].isin(_NON_OBSERVED)].copy()
    ts_utc = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df["ts_local"] = ts_utc.dt.tz_convert(tz)
    df["date"] = df["ts_local"].dt.date
    df["hour"] = df["ts_local"].dt.hour
    df["weekday"] = df["ts_local"].dt.day_name()
    df["wait_minutes"] = pd.to_numeric(df["wait_minutes"], errors="coerce")

    if park_id is not None:
        df = df[df["park_id"] == park_id].copy()
    return df.reset_index(drop=True)


def available_dates(df: pd.DataFrame) -> list[dt.date]:
    """データが存在するローカル日付を新しい順に返す。"""
    if df.empty:
        return []
    return sorted(df["date"].dropna().unique(), reverse=True)


def park_names(conn: sqlite3.Connection) -> dict[str, str]:
    """park_id → 表示名。meta キャッシュ→PARKエンティティ→既定値の順で解決。"""
    names: dict[str, str] = {p.park_id: p.name for p in DEFAULT_PARKS}

    # meta の発見済みキャッシュがあれば上書き。
    row = conn.execute("SELECT value FROM meta WHERE key=?", (META_KEY_PARKS,)).fetchone()
    if row:
        import json

        try:
            for item in json.loads(row["value"]):
                names[item["park_id"]] = item["name"]
        except (ValueError, KeyError, TypeError):
            pass

    # observations 内の PARK 自己エントリからも補完。
    for r in conn.execute(
        "SELECT DISTINCT park_id, name FROM observations WHERE entity_type='PARK' AND name IS NOT NULL"
    ):
        names.setdefault(r["park_id"], r["name"])
    return names


def available_parks(conn: sqlite3.Connection) -> list[str]:
    """観測が存在する park_id 一覧。"""
    rows = conn.execute(
        "SELECT DISTINCT park_id FROM observations WHERE status IS NOT ? OR status IS NULL",
        (STATUS_FETCH_FAILED,),
    ).fetchall()
    return [r["park_id"] for r in rows]


def data_sources(conn: sqlite3.Connection) -> set[str]:
    """snapshots に含まれる source の集合(データの出所判定に使う)。"""
    return {r["source"] for r in conn.execute("SELECT DISTINCT source FROM snapshots")}


def latest_weather(conn: sqlite3.Connection) -> dict | None:
    """最新の有効な天気観測を1件返す(欠測行=http_status!=200 は飛ばす)。

    返り値: {ts, temp_c, precip_mm, precip_prob, weather_code} または None。
    weather テーブルがまだ無い(天気を取り始める前の古い DB)場合も None。
    """
    try:
        row = conn.execute(
            "SELECT ts, temp_c, precip_mm, precip_prob, weather_code "
            "FROM weather WHERE http_status=200 ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None  # weather テーブル未作成の旧 DB。
    return dict(row) if row else None
