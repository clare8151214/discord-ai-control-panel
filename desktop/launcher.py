"""Run the web control panel inside a native Windows window.

The HTTP listener is private to this desktop process, uses an ephemeral loopback
port, and is protected by a fresh random credential on each launch.  pywebview is
imported only when opening a window, so CI can exercise the real server headlessly.
"""
from __future__ import annotations

import argparse
import contextlib
from http.cookiejar import CookieJar
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import webbrowser


APP_NAME = "Discord AI Control Panel"
WEBVIEW2_URL = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/"
LOGGER = logging.getLogger("discord-ai-desktop")


def default_data_dir() -> Path:
    """Keep user data outside the EXE, its extraction directory, and the repo."""
    if sys.platform == "win32":
        parent = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        parent = Path.home() / "Library" / "Application Support"
    else:
        parent = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return parent / "DiscordAIControlPanel"


class AlreadyRunningError(RuntimeError):
    pass


class InstanceLock:
    """An OS-held lock survives stale files and is released if the EXE crashes."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "desktop.lock"
        self.stream: Any = None

    def __enter__(self) -> InstanceLock:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise AlreadyRunningError("管理介面已經開啟，請使用現有視窗。") from exc
        return self

    def __exit__(self, *args: Any) -> None:
        if self.stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None
        # Never unlink this path: another process may already be holding its lock.


class _RedactFilter(logging.Filter):
    def __init__(self, values: tuple[str, ...]) -> None:
        super().__init__()
        self.values = values

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for value in self.values:
            message = message.replace(value, "[REDACTED]")
        record.msg = message
        record.args = ()
        return True


def configure_logging(directory: Path) -> RotatingFileHandler:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = RotatingFileHandler(directory / "desktop.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return handler


class LocalServer:
    """Own the socket, ASGI lifespan, and graceful Bot shutdown in one place."""

    def __init__(self, data_dir: Path, *, ready_timeout: float = 30) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.ready_timeout = ready_timeout
        self.password = secrets.token_urlsafe(48)
        self.bootstrap_token = secrets.token_urlsafe(48)
        self.base_url = ""
        self.app: Any = None
        self.server: Any = None
        self.thread: threading.Thread | None = None
        self.socket: socket.socket | None = None
        self.failure: BaseException | None = None
        self._redactor = _RedactFilter((self.password, self.bootstrap_token))
        self._handlers: list[logging.Handler] = []

    def __enter__(self) -> LocalServer:
        from web.app import create_app
        import uvicorn

        self.app = create_app(self.data_dir, password=self.password, desktop_bootstrap_token=self.bootstrap_token, allow_legacy_import=False)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Bind before creating the server, rather than finding then releasing
            # a port; no other process can claim it between these operations.
            self.socket.bind(("127.0.0.1", 0))
            self.socket.listen(128)
            self.socket.setblocking(False)
            self.base_url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
            for handler in logging.getLogger().handlers:
                handler.addFilter(self._redactor)
                self._handlers.append(handler)
            config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_config=None, access_log=False, log_level="warning", timeout_graceful_shutdown=25)
            self.server = uvicorn.Server(config)
            self.thread = threading.Thread(target=self._serve, name="desktop-control-panel", daemon=True)
            self.thread.start()
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + self.ready_timeout
            while time.monotonic() < deadline:
                if not self.thread.is_alive():
                    raise RuntimeError("本機管理服務無法啟動，請查看 desktop.log。") from self.failure
                try:
                    with opener.open(self.base_url + "/readyz", timeout=0.5) as response:
                        if response.status == 200:
                            return self
                except (OSError, URLError):
                    pass
                time.sleep(0.05)
            raise RuntimeError("本機管理服務啟動逾時，請查看 desktop.log。")
        except BaseException:
            self.close()
            raise

    def _serve(self) -> None:
        try:
            self.server.run(sockets=[self.socket])
        except BaseException as exc:
            self.failure = exc
            LOGGER.error("Local control-panel server terminated unexpectedly (%s).", type(exc).__name__)

    def bootstrap_url(self) -> str:
        issue = getattr(self.app.state, "issue_desktop_bootstrap", None)
        token = issue() if issue else self.bootstrap_token
        self._redactor.values = (*self._redactor.values, token)
        return self.base_url + "/desktop/bootstrap?token=" + quote(token, safe="")

    def close(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=28)
            if self.thread.is_alive():
                self.server.force_exit = True
                self.thread.join(timeout=3)
            if self.thread.is_alive():
                LOGGER.error("Server did not finish shutdown in time.")
        if self.socket is not None:
            with contextlib.suppress(OSError):
                self.socket.close()
            self.socket = None
        for handler in self._handlers:
            handler.removeFilter(self._redactor)
        self._handlers.clear()

    def __exit__(self, *args: Any) -> None:
        self.close()


def show_message(title: str, message: str) -> None:
    """A windowed EXE must report errors even when stderr does not exist."""
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
        return
    try:
        import tkinter as tk
        from tkinter import messagebox

        window = tk.Tk()
        window.withdraw()
        messagebox.showinfo(title, message, parent=window)
        window.destroy()
    except Exception:
        if sys.stderr:
            print(f"{title}: {message}", file=sys.stderr)


def browser_window(server: LocalServer, *, reason: str = "") -> None:
    """Visible lifetime owner when Edge WebView2 is unavailable or not wanted."""
    import tkinter as tk
    from tkinter import ttk

    window = tk.Tk()
    window.title(APP_NAME)
    window.geometry("560x290")
    window.minsize(480, 260)
    frame = ttk.Frame(window, padding=22)
    frame.pack(expand=True, fill="both")
    ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 15, "bold")).pack(anchor="w")
    detail = reason or "管理介面會在瀏覽器中開啟。保留此視窗即可持續執行 Bot；關閉此視窗會停止本次服務。"
    ttk.Label(frame, text=detail, wraplength=500, justify="left").pack(anchor="w", pady=(14, 10))

    def open_panel() -> None:
        if not webbrowser.open(server.bootstrap_url()):
            show_message(APP_NAME, "無法開啟預設瀏覽器。請確認系統已設定瀏覽器，然後重試。")

    ttk.Button(frame, text="開啟管理介面", command=open_panel).pack(fill="x", pady=4)
    if reason:
        ttk.Button(frame, text="下載 Microsoft Edge WebView2 Runtime", command=lambda: webbrowser.open(WEBVIEW2_URL)).pack(fill="x", pady=4)
    ttk.Button(frame, text="停止 Bot 並結束", command=window.destroy).pack(fill="x", pady=4)
    if not reason:
        window.after(150, open_panel)
    window.mainloop()


def open_desktop(server: LocalServer, *, use_browser: bool = False) -> None:
    if use_browser:
        browser_window(server)
        return
    try:
        import webview

        webview.create_window(APP_NAME, server.bootstrap_url(), width=1180, height=820, min_size=(720, 540))
        webview.start(gui="edgechromium" if sys.platform == "win32" else None, debug=False, private_mode=True)
    except Exception as exc:
        LOGGER.warning("Native window unavailable (%s); displaying browser launcher.", type(exc).__name__)
        browser_window(server, reason="無法開啟內嵌視窗。Windows 請安裝 Microsoft Edge WebView2 Runtime，或直接使用下方的瀏覽器模式。保留此視窗即可持續執行 Bot。")


def _write_result(result: dict[str, Any], path: Path | None) -> None:
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if path is not None:
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded + "\n", encoding="utf-8")
    if sys.stdout is not None:
        print(encoded, flush=True)


def self_test(result_file: Path | None = None) -> int:
    """Exercise packaged assets, real HTTP auth, and CRUD; never log in to Discord."""
    checks: list[str] = []
    result: dict[str, Any] = {"ok": False, "checks": checks}
    try:
        with tempfile.TemporaryDirectory(prefix="discord-ai-desktop-test-") as temporary:
            with LocalServer(Path(temporary) / "panel") as server:
                cookies = CookieJar()
                opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(cookies))

                def request(path: str, method: str = "GET", body: Any = None) -> tuple[int, Any]:
                    data = json.dumps(body).encode() if body is not None else None
                    headers = {"Content-Type": "application/json", "X-Control-Panel": "1"}
                    req = Request(server.base_url + path, data=data, method=method, headers=headers)
                    try:
                        response = opener.open(req, timeout=10)
                    except HTTPError as exc:
                        response = exc
                    with response:
                        raw = response.read()
                        content_type = response.headers.get("Content-Type", "")
                        return response.status, json.loads(raw) if raw and "application/json" in content_type else raw.decode("utf-8")

                assert request("/api/profiles")[0] == 401
                assert request("/desktop/bootstrap?token=invalid")[0] == 403
                bootstrap_path = server.bootstrap_url().removeprefix(server.base_url)
                assert request(bootstrap_path)[0] == 200
                assert request(bootstrap_path)[0] == 403
                status, session = request("/api/session")
                assert status == 200 and session["authenticated"] and session["desktop"]
                checks.append("loopback_one_time_bootstrap_session")
                status, html = request("/")
                assert status == 200 and "<html" in html.lower()
                assert request("/static/app.js")[0] == 200
                checks.append("bundled_web_assets")
                payload = {"name": "Desktop self-test", "discord_token": "desktop-test-secret", "default_model": "self-test", "providers": [{"id": "ollama", "base_url": "http://127.0.0.1:9"}]}
                status, profile = request("/api/profiles", "POST", payload)
                assert status == 201 and profile["has_discord_token"] and profile["discord_token"] == ""
                profile_id = profile["id"]
                status, listing = request("/api/profiles")
                assert status == 200 and len(listing["profiles"]) == 1
                profile["name"] = "Updated self-test"
                assert request(f"/api/profiles/{profile_id}", "PUT", profile)[0] == 200
                status, runtime = request("/api/runtime")
                assert status == 200 and runtime["bots"][0]["status"] == "stopped"
                manager = server.app.state.manager
                original_command = manager.child_command
                child_result_file = Path(temporary) / "child-result.json"
                child_args = ["--bot-self-test", "--result-file", str(child_result_file)]
                if manager.frozen_runner:
                    manager.child_command = lambda: [*original_command(), *child_args]
                else:
                    # Source previews exercise the same desktop dispatch branch;
                    # frozen builds preserve the actual EXE --run-bot command.
                    entry = Path(__file__).resolve().parent.parent / "desktop_entry.py"
                    manager.child_command = lambda: [sys.executable, "-u", str(entry), "--run-bot", *child_args]
                try:
                    assert request(f"/api/profiles/{profile_id}/start", "POST")[0] == 200
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        status, runtime = request("/api/runtime")
                        child_status = runtime["bots"][0]
                        if child_status["exit_code"] is not None and child_result_file.exists() and any('"module_loaded": "bot"' in line for line in child_status["logs"]):
                            break
                        time.sleep(0.1)
                    else:
                        raise TimeoutError("Bot child dispatch self-test did not finish")
                    child_result = json.loads(child_result_file.read_text(encoding="utf-8"))
                    assert child_status["exit_code"] == 0 and child_result["ok"] and child_result["config_isolated"]
                    directory = server.app.state.store.path(profile_id)
                    assert Path(child_result["state_file"]) == directory / "state.json"
                    assert Path(child_result["database_file"]) == directory / "history.sqlite3"
                    assert Path(child_result["character_asset_dir"]) == directory / "characters"
                    assert "desktop-test-secret" not in "\n".join(child_status["logs"])
                    if manager.frozen_runner:
                        assert (directory / "process-output.log").exists()
                        assert child_result["ffmpeg_path"] and Path(child_result["ffmpeg_path"]).is_file()
                        if os.name == "nt":
                            assert child_result["opus_loaded"] and child_result["opus_encoded_bytes"] > 0
                    checks.append("supervised_child_dispatch_environment_and_log_reader")
                finally:
                    request(f"/api/profiles/{profile_id}/stop", "POST")
                    manager.child_command = original_command
                assert request(f"/api/profiles/{profile_id}", "DELETE")[0] == 204
                assert request("/api/profiles")[1]["profiles"] == []
                checks.append("isolated_profile_crud_without_bot_or_provider_calls")
        result["ok"] = True
    except Exception as exc:
        # Never include assertion bodies, HTTP request URLs, or submitted secrets.
        result["error"] = type(exc).__name__
        LOGGER.error("Desktop self-test failed (%s).", type(exc).__name__)
    _write_result(result, result_file)
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--data-dir", type=Path, default=None, help="Override per-user application data (advanced use)")
    parser.add_argument("--browser", action="store_true", help="Use a browser with a visible stop/open control window")
    parser.add_argument("--self-test", "--smoke-test", dest="self_test", action="store_true", help="Headless HTTP/auth/CRUD verification without Discord or model calls")
    parser.add_argument("--result-file", type=Path, help="Write self-test JSON for windowed EXE/CI")
    args = parser.parse_args(argv)
    if args.self_test:
        log_directory = args.result_file.resolve().parent if args.result_file else default_data_dir()
        handler = configure_logging(log_directory)
        try:
            return self_test(args.result_file)
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()
    data_dir = (args.data_dir or default_data_dir()).expanduser().resolve()
    handler: logging.Handler | None = None
    try:
        with InstanceLock(data_dir):
            handler = configure_logging(data_dir)
            LOGGER.info("Starting desktop control panel.")
            with LocalServer(data_dir) as server:
                open_desktop(server, use_browser=args.browser)
            LOGGER.info("Desktop control panel stopped.")
        return 0
    except AlreadyRunningError as exc:
        show_message(APP_NAME, str(exc))
        return 0
    except Exception as exc:
        LOGGER.error("Desktop startup failed (%s).", type(exc).__name__)
        show_message(APP_NAME, f"無法開啟管理介面。請查看下列日誌：\n{data_dir / 'desktop.log'}\n\n錯誤類型：{type(exc).__name__}")
        return 1
    finally:
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()
