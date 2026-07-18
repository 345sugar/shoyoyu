"""スマホ一台で完結させるランチャ(Android/Termux 想定)。

`sabotage-mobile` 一発で、同じ端末上に
  1. ポーラー(sabotage-poll --loop, 5分+ジッター)= 実データを SQLite に貯め続ける
  2. 現況ボード(Streamlit, viz/board.py)= その SQLite をスマホのブラウザで見る
を同時に立ち上げ、同じ DB を共有させる(SQLite は WAL なので書き込み中でも読める)。

Ctrl-C(Termux では音量下+C)で両方まとめて止める。

間隔は CLAUDE.md 準拠の 5分以上(1分間隔は非公式APIへの礼儀・規約に反するため不可)。
公開はせず localhost 束縛。取得データは私的利用の範囲(自分の端末で見る)。
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from ..config import DEFAULT_DB_PATH, DEFAULT_INTERVAL_SECONDS, DEFAULT_JITTER_SECONDS

BOARD_APP = Path(__file__).resolve().parent.parent / "viz" / "board.py"


def build_poller_cmd(
    db: str,
    *,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    jitter: int = DEFAULT_JITTER_SECONDS,
    python: str = sys.executable,
) -> list[str]:
    """ポーラー常駐プロセスの起動コマンド。"""
    return [
        python,
        "-m",
        "sabotage.data.poller",
        "--loop",
        "--db",
        db,
        "--interval",
        str(interval),
        "--jitter",
        str(jitter),
    ]


def build_board_cmd(
    db: str,
    *,
    port: int = 8501,
    address: str = "localhost",
    python: str = sys.executable,
    app: Path = BOARD_APP,
) -> list[str]:
    """現況ボード(Streamlit)の起動コマンド。localhost 束縛・ヘッドレス。"""
    return [
        python,
        "-m",
        "streamlit",
        "run",
        str(app),
        "--server.address",
        address,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--",
        "--db",
        db,
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sabotage-mobile",
        description="スマホ一台でポーラー(5分)+現況ボードを同時起動する(Android/Termux 想定)。",
    )
    p.add_argument("--db", default=DEFAULT_DB_PATH, help=f"共有 SQLite(既定: {DEFAULT_DB_PATH})")
    p.add_argument("--port", type=int, default=8501, help="ボードのポート(既定: 8501)")
    p.add_argument("--address", default="localhost", help="束縛アドレス(既定: localhost)")
    p.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="ポーリング間隔秒(下限300)"
    )
    p.add_argument("--jitter", type=int, default=DEFAULT_JITTER_SECONDS, help="ジッター秒")
    args = p.parse_args(argv)

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)

    poller_cmd = build_poller_cmd(
        args.db, interval=args.interval, jitter=args.jitter
    )
    board_cmd = build_board_cmd(args.db, port=args.port, address=args.address)

    # 各プロセスを独立セッションで起動 → 停止時にプロセスグループごと畳める。
    # Streamlit は子プロセスを産むため、グループごと止めないと孤児が残る(スマホの電池食い)。
    print("▶ ポーラー起動(5分+ジッターで実データ蓄積)")
    poller = subprocess.Popen(poller_cmd, start_new_session=True)
    print(f"▶ 現況ボード起動 → http://{args.address}:{args.port}/ をスマホのブラウザで開く")
    board = subprocess.Popen(board_cmd, start_new_session=True)

    procs = [("poller", poller), ("board", board)]

    def _signal_group(proc: subprocess.Popen, sig: int) -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, AttributeError, OSError):
            # killpg 不可(非POSIX等)は直接シグナル。
            (proc.terminate if sig != signal.SIGKILL else proc.kill)()

    def _shutdown(*_a) -> None:
        for name, proc in procs:
            if proc.poll() is None:
                print(f"■ {name} 停止")
                _signal_group(proc, signal.SIGTERM)

    signal.signal(signal.SIGINT, lambda *a: _shutdown())
    signal.signal(signal.SIGTERM, lambda *a: _shutdown())

    try:
        # どちらかが落ちたら、もう片方も畳んで終了。
        while True:
            for name, proc in procs:
                code = proc.poll()
                if code is not None:
                    print(f"■ {name} が終了(code={code})。もう片方も停止する。")
                    _shutdown()
                    for _, other in procs:
                        try:
                            other.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            _signal_group(other, signal.SIGKILL)
                    return code or 0
            try:
                poller.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        _shutdown()
    finally:
        for _, proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _signal_group(proc, signal.SIGKILL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
