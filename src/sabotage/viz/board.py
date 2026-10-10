"""現況ボード(スマホ向け)。当日その場で「今どうなってる?」を見る1画面。

起動:
    streamlit run src/sabotage/viz/board.py -- --db data/sabotage.db

- 最新スナップショットの各アトラクションを、待ち短い順(=穴場優先。立ち待ちは損失)で表示。
- 停止・休止中は下部にまとめる(木鶏: 騒がず、素直に別へ)。
- トレンド矢印(直近比)と、履歴が溜まれば割安/割高バッジ。
- データ鮮度を明示(古ければ警告)。表示ロジックは analysis 層(テスト済み)に委譲。
"""

from __future__ import annotations

import argparse
import math
import os
import threading
from contextlib import closing
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from sabotage.analysis import board, crowd, dining, nowcast, outing, queries
from sabotage.analysis.outing_insights import outing_insights
from sabotage.config import DEFAULT_DB_PATH, DEFAULT_INTERVAL_SECONDS, DEFAULT_JITTER_SECONDS
from sabotage.tools.seed_demo import DEMO_SOURCE, META_DEMO_FLAG
from sabotage.viz.theme import APP_NAME, apply_theme, hero, section_intro
from sabotage.viz.insight_cards import render_insights

FRESH_LIMIT_MIN = 15  # これを超えて更新が無ければ「古い」警告。
DEMO_NOW = pd.Timestamp("2026-07-18T14:00:00", tz="Asia/Tokyo")


def _setting(key: str, default: str = "") -> str:
    """設定を読む。Streamlit Cloud の Secrets(st.secrets)と OS 環境変数の両対応。

    Streamlit Community Cloud は環境変数ではなく Secrets(st.secrets)で値を渡すため、
    両方を見る(secrets.toml が無いローカルでは st.secrets アクセスが例外になるので握る)。
    """
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:  # noqa: BLE001 — secrets 未設定など
        pass
    return os.environ.get(key, default)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=_setting("SABOTAGE_DB", DEFAULT_DB_PATH))
    parser.add_argument("--demo", action="store_true", help="通信・DBファイル作成なしの合成デモ")
    # --self-poll: このページ自身が裏で5分ごとに取得する(常時稼働の箱が無くても
    #   Streamlit Community Cloud 等の無料URLで5分ライブにできる)。
    parser.add_argument(
        "--self-poll",
        action="store_true",
        default=_setting("SABOTAGE_SELF_POLL", "").lower() in ("1", "true", "yes"),
    )
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--jitter", type=int, default=DEFAULT_JITTER_SECONDS)
    args, _ = parser.parse_known_args()
    return args


@st.cache_resource
def _ensure_background_poller(db_path: str, interval: int, jitter: int) -> bool:
    """サーバープロセスで一度だけ、ポーラーを常駐スレッドとして起動する。

    Streamlit の cache_resource で「1プロセス1回」を担保。スレッドは自前の Storage 接続
    (WAL)を持つので、描画側の読み取りと並行できる。間隔は5分以上(config 準拠)。
    """
    def _run() -> None:
        try:
            from sabotage.data.client import ThemeParksClient
            from sabotage.data.poller import resolve_parks, run_forever
            from sabotage.data.storage import Storage
            from sabotage.data.weather import WeatherClient

            with (
                Storage(db_path) as store,
                ThemeParksClient() as client,
                WeatherClient() as weather_client,
            ):
                parks = resolve_parks(store, client)
                run_forever(
                    store,
                    client,
                    parks,
                    interval=interval,
                    jitter=jitter,
                    weather_client=weather_client,
                )
        except Exception:  # noqa: BLE001 — 自前ポーリングが死んでも描画は続ける。
            pass

    threading.Thread(target=_run, name="sabotage-selfpoll", daemon=True).start()
    return True


@st.cache_resource(show_spinner="📚 蓄積履歴を読み込み中…(初回のみ・数十秒)")
def _bootstrap_history_once(db_path: str) -> dict:
    """data ブランチの1ヶ月分をDBへ取り込む。1プロセス1回(cache_resource)。失敗しても描画は続行。"""
    try:
        from sabotage.data.history import bootstrap_history

        return bootstrap_history(db_path)
    except Exception as exc:  # noqa: BLE001
        return {"skipped": True, "reason": f"error:{type(exc).__name__}"}


def _fmt_delta(delta) -> str:
    if delta is None or pd.isna(delta):
        return ""
    d = int(delta)
    if d > 0:
        return f"🔺+{d}"
    if d < 0:
        return f"🔻{d}"
    return "➖"


def _value_badge(label) -> str:
    if not label:
        return ""
    color = {"割安": "#1a7f37", "割高": "#cf222e", "適正": "#6e7781"}.get(label, "#6e7781")
    return f'<span style="color:{color};font-weight:600">({label})</span>'


def _wait_text(status, wait) -> str:
    if status in board.STOP_STATUSES:
        jp = {"DOWN": "停止", "CLOSED": "休止", "REFURBISHMENT": "改修"}.get(status, status)
        return f'<span style="color:#cf222e;font-weight:700">{jp}</span>'
    if wait is None or pd.isna(wait):
        return '<span style="color:#6e7781">運営中</span>'
    return f'<b style="font-size:1.5rem">{int(wait)}</b><span style="font-size:.8rem">分</span>'


def _pred_text(r) -> str:
    """到着時予測「着N分↗」。群衆補正込み。停止/予測不可なら空。"""
    pw = r.get("pred_wait")
    if pw is None or pd.isna(pw):
        return ""
    sig = r.get("signal")
    color = {"混む": "#cf222e", "空く": "#1a7f37"}.get(sig, "#6e7781")
    arrow = {"混む": "↗", "空く": "↘"}.get(sig, "→")
    return f'<span style="color:{color};font-weight:600">着{int(pw)}分{arrow}</span>'


# WMO weather code → 絵文字(ざっくり)。Open-Meteo の weather_code に対応。
_WMO_EMOJI = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌦️", 56: "🌧️", 57: "🌧️",
    61: "🌧️", 63: "🌧️", 65: "🌧️", 66: "🌧️", 67: "🌧️",
    71: "🌨️", 73: "🌨️", 75: "❄️", 77: "🌨️",
    80: "🌦️", 81: "🌧️", 82: "⛈️",
    85: "🌨️", 86: "❄️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}
# この降水確率(%)以上なら「まもなく雨」警告を出す(=屋内退避で室内系が混む予兆)。
RAIN_ALERT_PROB = 50


def _weather_line(w: dict) -> None:
    """天気バッジ+雨警告を描画する。w は queries.latest_weather の返り値。

    Phase 2 心理設計:雨予報は「室内系がこれから混む」先行シグナル。網を張る材料。
    """
    code = w.get("weather_code")
    emoji = _WMO_EMOJI.get(int(code), "🌡️") if code is not None else "🌡️"
    parts = [emoji]
    temp = w.get("temp_c")
    if temp is not None:
        parts.append(f"{temp:.0f}℃")
    prob = w.get("precip_prob")
    if prob is not None:
        parts.append(f"降水{int(prob)}%")
    st.caption("舞浜 " + " · ".join(parts))

    if prob is not None and int(prob) >= RAIN_ALERT_PROB:
        st.warning(
            f"☔️ まもなく雨(降水{int(prob)}%)— 屋内系がこれから混みます。"
            "先に室内・飲食へ張るのが得(立ち待ちは損失)。",
            icon="☔️",
        )


def _dining_section(park_df, park_id: str, latest_ts, weather: dict | None) -> None:
    """🍽️ 食事どきナビ。人圧(実データ)+天気で「食べ時/室内へ」を助言し、店(参考)を並べる。

    店の時刻・空席・メニューは非公式APIに存在しないため出さない(無いものは出さない)。
    """
    today = pd.Timestamp(latest_ts).date()
    press = crowd.crowd_pressure(park_df, today)
    series = press["pressure"].tolist() if not press.empty else []
    current = series[-1] if series else None
    advice = dining.meal_timing(current, series, weather)

    st.markdown("#### ひと息つく時間")
    box = {"eat": st.info, "ride": st.success, "rain": st.warning}.get(advice.mode, st.info)
    box(f"**{advice.headline}**\n\n{advice.detail}")

    rests = dining.restaurants(park_id, indoor_only=advice.indoor_urgent)
    # 室内→エリア順に。室内フラグを分かりやすく。
    rests = sorted(rests, key=lambda r: (not r.indoor, r.area, r.name))
    label = "室内で座れる店（参考）" if advice.indoor_urgent else "レストランのご案内（参考）"
    with st.expander(f"{label}({len(rests)})"):
        html = []
        for r in rests:
            mark = "室内" if r.indoor else "屋外"
            html.append(
                '<div style="padding:.35rem 0;border-bottom:1px solid rgba(128,128,128,.2)">'
                f'<div style="font-weight:600">{escape(r.name)}</div>'
                f'<div style="font-size:.78rem;color:#6e7781">'
                f'{escape(r.area)} · {escape(r.service)} · {mark}{" · " + escape(r.note) if r.note else ""}</div>'
                "</div>"
            )
        st.markdown("".join(html), unsafe_allow_html=True)
        st.caption(
            "※ 店は参考データ(改装・営業変更あり得る)。待ち時間・空席・メニューは"
            "非公式APIに無いため非提供。"
        )


def _row(r, *, favorite: bool = False) -> str:
    """現在待ちを前面に出さず、到着予測と運営状態を HTML へ安全に整形する。"""
    status = r.get("status")
    prediction = r.get("pred_wait")
    operating = isinstance(status, str) and status == "OPERATING"
    if operating and prediction is not None and pd.notna(prediction) and math.isfinite(prediction) and prediction >= 0:
        left = f'<b>{int(prediction)}</b><br>分・到着時予測'
    else:
        label = {"DOWN": "停止", "CLOSED": "休止", "REFURBISHMENT": "改修"}.get(
            status if isinstance(status, str) else "", "予測なし" if operating else "状態不明"
        )
        left = escape(label)
    method = {"reversion": "履歴からの予測", "momentum": "直近の変化から予測", "flat": "履歴が少ない参考値"}.get(r.get("pred_method"), "")
    meta = " · ".join(escape(str(value)) for value in [r.get("area"), method] if value)
    star = '<span class="mpm-tag">★ お気に入り</span>' if favorite else ""
    return (
        '<div class="mpm-ride">'
        f'<div class="mpm-wait">{left}</div>'
        f'<div><div class="mpm-name">{escape(str(r["name"]))}{star}</div>'
        f'<div class="mpm-meta">{meta}</div></div>'
        "</div>"
    )


@st.cache_data(show_spinner=False)
def _demo_data() -> tuple[pd.DataFrame, dict, list[str]]:
    """固定した合成観測をメモリ内だけで作る。ネットワーク・DBファイルを使わない。"""
    from sabotage.data.storage import Storage
    from sabotage.tools import seed_demo

    with Storage(":memory:") as storage:
        seed_demo.seed(storage, days=3, today=DEMO_NOW.to_pydatetime())
        df = queries.load_observations(storage.connection)
        names = queries.park_names(storage.connection)
        parks = queries.available_parks(storage.connection)
    return df[df["ts_local"] <= DEMO_NOW].copy(), names, parks


def _query_choices(key: str, choices: list[str]) -> list[str]:
    return [value for value in st.query_params.get_all(key) if value in choices]


def _save_query(key: str, value) -> None:
    values = value if isinstance(value, list) else [str(value)]
    if st.query_params.get_all(key) != values:
        if values:
            st.query_params[key] = values
        elif key in st.query_params:
            del st.query_params[key]


def render(
    db_path: str,
    *,
    self_poll: bool = False,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    jitter: int = DEFAULT_JITTER_SECONDS,
    demo: bool = False,
) -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="✨", layout="centered")
    apply_theme()
    hero("A DAY AT THE PARK", "急がず、楽しむ。\n大人のパーク時間。")

    boot = {}
    weather = None
    weather_history = pd.DataFrame()
    sources = {DEMO_SOURCE} if demo else set()
    if demo:
        df, names, parks = _demo_data()
        clock = DEMO_NOW
        st.warning("合成デモ｜2026年7月18日 14:00（日本時間）の架空データです。現在の園内状況ではありません。")
    else:
        clock = pd.Timestamp.now(tz="Asia/Tokyo")
    if self_poll and not demo:
        # このページ自身が裏で5分ごとに取得(常時稼働の箱が不要)。
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _ensure_background_poller(db_path, max(interval, 300), jitter)
        # 60秒ごとにページを自動リロードして最新を反映(親フレームごと)。
        components.html(
            "<script>setTimeout(function(){window.parent.location.reload();},60000);</script>",
            height=0,
        )
        st.caption(f"🔴 5分ライブ(このページ自身が取得中・{max(interval, 300)//60}分間隔)")

    # data ブランチの蓄積履歴(毎時フライホイール)を揮発DBへ取り込む。これで到着時予測が
    # 初回から平常回帰で効く(揮発DB+self-pollだけだと flat に戻ってしまう)。初回のみ実行。
    if not demo:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        boot = _bootstrap_history_once(db_path)
        if not Path(db_path).exists():
            st.info("初回取得を待っています。データの準備ができるとここに表示されます。")
            return
        # self-poll 初回のスキーマ作成との競合を避ける。
        from sabotage.data.storage import Storage
        with Storage(db_path):
            pass
        with closing(queries.connect(db_path)) as conn:
            df = queries.load_observations(conn)
            names = queries.park_names(conn)
            parks = queries.available_parks(conn)
            weather = queries.latest_weather(conn)
            weather_history = queries.load_weather(conn)
            sources = queries.data_sources(conn)
            demo_flag = conn.execute("SELECT value FROM meta WHERE key=?", (META_DEMO_FLAG,)).fetchone()
        if demo_flag and not any(s and s != DEMO_SOURCE for s in sources):
            st.warning("合成データを表示しています。実際の待ち時間ではありません。", icon="🎠")
    if df.empty:
        st.info("まだ観測データがありません。")
        return
    if not parks:
        st.info("表示できるパークがありません。")
        return

    # パーク選択と到着分を URL クエリに保持する。self_poll の60秒リロードは
    # ページ全体を再読込みするため、保持しないとスライダー等が毎分既定値へ戻ってしまう
    #(「動かしても効かない」ように見える原因)。クエリに載せておけば再読込み後も復元される。
    qp = st.query_params

    park_from_qp = qp.get("park")
    park_index = parks.index(park_from_qp) if park_from_qp in parks else 0
    park_id = st.radio(
        "パーク", parks, index=park_index,
        format_func=lambda p: names.get(p, p), horizontal=True, key="park_choice",
    )
    if qp.get("park") != park_id:
        qp["park"] = park_id

    views = ["board", "correlations"]
    view_default = qp.get("view", "board")
    view = st.radio(
        "表示", views, index=views.index(view_default) if view_default in views else 0,
        format_func=lambda value: {"board": "今日のパーク", "correlations": "相関をみる"}[value],
        horizontal=True, key="view_choice",
    )
    _save_query("view", view)
    park_df = df[df["park_id"] == park_id]
    if view == "correlations":
        from sabotage.viz.app import _correlation_section

        _correlation_section(park_df, weather_history)
        st.caption("合成デモデータ使用" if demo else "データ元: ThemeParks.wiki（非公式・私的利用）")
        return

    daily_hint_slot = st.container()
    try:
        arr_default = int(qp.get("arr", nowcast.DEFAULT_ARRIVAL_MIN))
    except (TypeError, ValueError):
        arr_default = nowcast.DEFAULT_ARRIVAL_MIN
    arr_default = min(90, max(5, (arr_default // 5) * 5))
    arrival_min = st.slider(
        "到着まで(分)", min_value=5, max_value=90, value=arr_default, step=5,
        help="すべての候補に同じ到着時間を仮定します。実際の徒歩経路や距離は計算しません。",
        key="arrival_minutes",
    )
    if qp.get("arr") != str(arrival_min):
        qp["arr"] = str(arrival_min)

    current = board.current_board(park_df, park_id)
    if current.empty:
        st.info("このパークの現況データがありません。")
        return

    latest = pd.Timestamp(current["ts_local"].iloc[0])
    data_freshness = outing.freshness(latest, now=clock, max_age_minutes=FRESH_LIMIT_MIN)
    fresh = data_freshness["fresh"]
    b = nowcast.predict_board(
        park_df, park_id, arrival_min=arrival_min, now=clock if fresh else None
    )
    st.caption(f"最終観測 {latest.strftime('%Y/%m/%d %H:%M')} JST · {'更新は良好' if fresh else '参考表示'}")
    if not fresh:
        st.warning("観測が古いか時刻を確認できないため、移動候補と食事どきの提案を止めています。下の一覧は観測時点の参考情報です。")
    if boot and not boot.get("skipped") and boot.get("observations"):
        st.caption(
            f"📚 蓄積履歴 {boot['observations']:,} 観測をロード済み — 到着時予測(平常回帰)が有効"
        )

    weather_fresh = bool(weather) and outing.freshness(weather.get("ts"), now=clock)["fresh"]
    if weather_fresh and fresh:
        _weather_line(weather)
    elif weather and not weather_fresh:
        st.caption("天気の更新が古いため、天気に基づく助言は休止しています。")

    section_intro("YOUR DAY, YOUR PACE", "今日の過ごし方", "好きな場所と、心地よく待てる時間から。")
    area_options = sorted(b["area"].dropna().unique().tolist())
    name_options = sorted(b["name"].dropna().unique().tolist())
    area_key, favorite_key = f"area_{park_id}", f"fav_{park_id}"
    areas = st.multiselect("エリア", area_options, default=_query_choices(area_key, area_options), key=area_key)
    favorites = st.multiselect("お気に入り", name_options, default=_query_choices(favorite_key, name_options), key=favorite_key)
    _save_query(area_key, areas)
    _save_query(favorite_key, favorites)
    favorites_only = st.toggle("お気に入りだけ", value=qp.get("fav_only") == "1", key="favorites_only")
    _save_query("fav_only", "1" if favorites_only else "0")
    limits = [15, 30, 45, 60, 90, 120, "全て"]
    saved_limit = qp.get("max_wait", "全て")
    default_limit = next((value for value in limits if str(value) == saved_limit), "全て")
    max_wait = st.selectbox("到着時に待てる上限", limits, index=limits.index(default_limit),
                            format_func=lambda value: value if isinstance(value, str) else f"{value}分", key="max_wait")
    _save_query("max_wait", max_wait)
    chosen = outing.select_attractions(
        b, areas=areas, favorites=favorites, favorites_only=favorites_only,
        max_wait=None if max_wait == "全て" else max_wait,
    )
    st.caption("絞り込みはURLに保存されます。ブックマークすると次回も同じ条件で開けます。")
    with daily_hint_slot:
        render_insights(outing_insights(chosen, fresh=fresh, arrival_min=arrival_min), daily=True)
    columns = st.columns(3)
    columns[0].metric("運営中", f"{int((b['status'] == 'OPERATING').sum())}件")
    columns[1].metric("条件に合う候補", f"{len(chosen)}件")
    columns[2].metric("到着までの想定", f"{arrival_min}分")

    if fresh and not chosen.empty:
        first = chosen.iloc[0]
        st.markdown(
            '<div class="mpm-pick"><div class="mpm-label">条件に合う、待ち時間の短い候補</div>'
            f'<h3>{escape(str(first["name"]))}</h3>'
            f'<p>{escape(str(first["area"]))} · 到着時の予測待ち <strong>{int(first["pred_wait"])}分</strong></p>'
            '<p>入口の最新案内を確認して、無理なく楽しみましょう。</p></div>',
            unsafe_allow_html=True,
        )
    st.markdown("#### 到着時の待ち時間" if fresh else "#### 過去の観測を使った参考一覧")
    st.caption(
        (f"今から{arrival_min}分後" if fresh else f"観測時刻から{arrival_min}分後")
        + "の予測待ちが短い順。全候補に同じ到着時間を仮定しています。経路・距離は未計算です。"
    )
    # 予測の“段階”を正直に出す。履歴が薄いと全部 flat(=現在値そのまま)になり、
    # スライダーを動かしても数字が変わらない。それを黙って放置すると「壊れてる?」に見える。
    methods = [m for m in chosen.get("pred_method", pd.Series(dtype=object)).tolist() if m]
    if methods and all(m == "flat" for m in methods):
        st.info(
            "⏳ いま履歴が薄いので、予測は現在値と同じです"
            "(スライダーを動かしても数字は変わりません)。"
            "データが数日貯まると『この時間帯はいつもこう』で動き始めます。",
            icon="⏳",
        )
    elif methods and all(m in ("flat", "momentum") for m in methods):
        st.caption("※ いまは直近の傾きベースの暫定予測。数日貯まると平常回帰(本命)に切り替わります。")
    if chosen.empty:
        st.info("条件に合う候補がありません。エリアや待ち時間の上限を広げてみてください。")
    else:
        html = "".join(_row(r, favorite=r["name"] in favorites) for _, r in chosen.iterrows())
        st.markdown(html, unsafe_allow_html=True)

    valid_indices = outing.select_attractions(b).index
    unavailable = b.loc[~b.index.isin(valid_indices)]
    if not unavailable.empty:
        with st.expander(f"停止・状態不明・予測なし（{len(unavailable)}件）"):
            html = "".join(_row(r) for _, r in unavailable.iterrows())
            st.markdown(html, unsafe_allow_html=True)

    st.divider()
    # 🍽️ 食事どき(人圧+天気で「食べ時/室内へ」。店は参考データ)。
    if fresh:
        _dining_section(park_df, park_id, latest, weather if weather_fresh else None)
    else:
        st.caption("食事どきナビは新しい観測が届くまでお休みです。")

    st.divider()
    st.caption("個人制作の非公式ツールです。予測は目安で、待ち時間や運営状況を保証するものではありません。")
    st.caption("合成デモデータ使用" if demo else "データ元: ThemeParks.wiki（非公式・私的利用）")
    if any("queue-times" in str(source).lower() for source in sources):
        st.markdown("[Powered by Queue-Times.com](https://queue-times.com)")


def main() -> None:
    args = _parse_args()
    render(
        args.db, self_poll=args.self_poll, interval=args.interval, jitter=args.jitter, demo=args.demo
    )


if __name__ == "__main__":
    main()
