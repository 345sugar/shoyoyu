"""スマホ用ランチャ(tools/mobile)のテスト。コマンド組み立てを検証(プロセスは起動しない)。"""

from __future__ import annotations

from pathlib import Path

from sabotage.tools import mobile


def test_board_app_path_points_to_board_py():
    assert mobile.BOARD_APP.name == "board.py"
    assert mobile.BOARD_APP.parent.name == "viz"
    assert mobile.BOARD_APP.exists()


def test_build_poller_cmd_loops_with_5min_floor_args():
    cmd = mobile.build_poller_cmd("data/x.db", interval=300, jitter=45, python="py")
    assert cmd[:4] == ["py", "-m", "sabotage.data.poller", "--loop"]
    assert "--db" in cmd and "data/x.db" in cmd
    assert cmd[cmd.index("--interval") + 1] == "300"
    assert cmd[cmd.index("--jitter") + 1] == "45"


def test_build_board_cmd_binds_localhost_and_passes_db():
    cmd = mobile.build_board_cmd("data/x.db", port=8600, address="localhost", python="py")
    assert cmd[:4] == ["py", "-m", "streamlit", "run"]
    assert str(mobile.BOARD_APP) in cmd
    assert cmd[cmd.index("--server.address") + 1] == "localhost"
    assert cmd[cmd.index("--server.port") + 1] == "8600"
    # `--` の後ろに board への --db が渡る。
    dashdash = cmd.index("--")
    assert "--db" in cmd[dashdash:]
    assert "data/x.db" in cmd[dashdash:]


def test_build_board_cmd_headless_no_telemetry():
    cmd = mobile.build_board_cmd("d.db")
    assert cmd[cmd.index("--server.headless") + 1] == "true"
    assert cmd[cmd.index("--browser.gatherUsageStats") + 1] == "false"
