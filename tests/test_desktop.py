import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest

from desktop.launcher import AlreadyRunningError, InstanceLock, LocalServer, default_data_dir, open_desktop, self_test
from desktop_entry import _BotLogStream


ROOT = Path(__file__).resolve().parent.parent


def test_default_data_outside_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert default_data_dir() == tmp_path / "Local" / "DiscordAIControlPanel"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert default_data_dir() == tmp_path / "xdg" / "DiscordAIControlPanel"


def test_instance_lock_is_released_and_stale_file_safe(tmp_path):
    with InstanceLock(tmp_path):
        with pytest.raises(AlreadyRunningError):
            with InstanceLock(tmp_path):
                pytest.fail("Two desktop runtimes acquired one data directory")
    with InstanceLock(tmp_path):
        assert (tmp_path / "desktop.lock").exists()


def test_real_http_self_test_does_not_need_webview_or_real_services(tmp_path):
    result_file = tmp_path / "result.json"
    assert self_test(result_file) == 0
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert result["ok"]
    assert len(result["checks"]) == 4
    assert "supervised_child_dispatch_environment_and_log_reader" in result["checks"]
    assert "secret" not in result_file.read_text(encoding="utf-8")


def test_server_owns_ephemeral_loopback_and_shuts_down(tmp_path):
    with LocalServer(tmp_path) as server:
        assert server.socket.getsockname()[0] == "127.0.0.1"
        assert server.socket.getsockname()[1] > 0
        assert server.thread.is_alive()
        thread = server.thread
    assert not thread.is_alive()


def test_reopening_browser_gets_a_fresh_one_time_bootstrap(tmp_path):
    with LocalServer(tmp_path) as server:
        opener = build_opener(ProxyHandler({}))
        first = server.bootstrap_url()
        with opener.open(first, timeout=5) as response:
            assert response.status == 200
        with pytest.raises(HTTPError) as invalid:
            opener.open(first, timeout=5)
        assert invalid.value.code == 403
        second = server.bootstrap_url()
        assert second != first
        with opener.open(second, timeout=5) as response:
            assert response.status == 200


def test_webview_failure_leaves_a_visible_browser_controller(monkeypatch):
    calls = []

    def unavailable(*args, **kwargs):
        raise RuntimeError("No WebView2 runtime")

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=unavailable))
    monkeypatch.setattr("desktop.launcher.browser_window", lambda server, **kwargs: calls.append(kwargs))
    open_desktop(SimpleNamespace(bootstrap_url=lambda: "http://127.0.0.1:1/desktop/bootstrap?token=test"))
    assert len(calls) == 1 and "WebView2" in calls[0]["reason"]


def test_windowed_bot_log_redacts_and_rotates(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_TOKEN", "discord-sensitive-value")
    monkeypatch.setenv("AI_PROVIDERS_JSON", '[{"api_key":"api-sensitive-value"}]')
    stream = _BotLogStream(tmp_path / "bot.log")
    stream.handler.maxBytes = 200
    try:
        stream.write("discord-sensitive-value\n")
        stream.write("api-sensitive-value\n")
        stream.flush()
        early_contents = (tmp_path / "bot.log").read_text(encoding="utf-8")
        assert "sensitive-value" not in early_contents
        assert early_contents.count("[REDACTED]") == 2
        for index in range(15):
            stream.write(f"message {index}: " + "x" * 60 + "\n")
        stream.flush()
        logs = list(tmp_path.glob("bot.log*"))
        assert 1 < len(logs) <= 3
        contents = "\n".join(path.read_text(encoding="utf-8") for path in logs)
        assert "sensitive-value" not in contents
        assert "message 14" in contents
    finally:
        stream.handler.close()


def test_bot_child_branch_loads_without_opening_gui_or_logging_into_discord(tmp_path):
    paths = {
        "STATE_FILE": str(tmp_path / "profile" / "state.json"),
        "DATABASE_FILE": str(tmp_path / "profile" / "history.sqlite3"),
        "CHARACTER_ASSET_DIR": str(tmp_path / "profile" / "characters"),
    }
    result_file = tmp_path / "bot-result.json"
    env = {**os.environ, **paths, "BOT_CONFIG_ISOLATED": "1", "DISCORD_TOKEN": "never-connect-this-token"}
    process = subprocess.run([sys.executable, str(ROOT / "desktop_entry.py"), "--run-bot", "--bot-self-test", "--result-file", str(result_file)], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert result["module_loaded"] == "bot"
    assert result["state_file"] == paths["STATE_FILE"]
    assert result["database_file"] == paths["DATABASE_FILE"]
    assert result["character_asset_dir"] == paths["CHARACTER_ASSET_DIR"]
    assert "never-connect-this-token" not in process.stdout + process.stderr


def test_bot_child_file_output_for_windowed_exe(tmp_path):
    log_file = tmp_path / "bot-child.log"
    process = subprocess.run([sys.executable, str(ROOT / "desktop_entry.py"), "--bot-self-test"], cwd=tmp_path, env={**os.environ, "BOT_CONFIG_ISOLATED": "1", "BOT_LOG_FILE": str(log_file)}, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0
    assert '"module_loaded": "bot"' in log_file.read_text(encoding="utf-8")
    assert process.stdout == ""


def test_desktop_self_test_works_without_stdout_stderr(tmp_path):
    result_file = tmp_path / "result.json"
    command = "import runpy,sys; sys.stdout=sys.stderr=None; runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    process = subprocess.run([sys.executable, "-c", command, str(ROOT / "desktop_entry.py"), "--self-test", "--result-file", str(result_file)], env={**os.environ, "PYTHONPATH": str(ROOT)}, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert json.loads(result_file.read_text(encoding="utf-8"))["ok"]
    assert (tmp_path / "desktop.log").exists()


def test_bot_self_test_works_without_stdout_stderr(tmp_path):
    result_file = tmp_path / "result.json"
    command = "import runpy,sys; sys.stdout=sys.stderr=None; runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    process = subprocess.run([sys.executable, "-c", command, str(ROOT / "desktop_entry.py"), "--bot-self-test", "--result-file", str(result_file)], env={**os.environ, "PYTHONPATH": str(ROOT), "BOT_LOG_FILE": ""}, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert json.loads(result_file.read_text(encoding="utf-8"))["ok"]
    assert list(tmp_path.glob("bot-child-*.log"))
