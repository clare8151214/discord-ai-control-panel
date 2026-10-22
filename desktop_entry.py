"""PyInstaller entry point: route Bot children before importing the GUI."""
from __future__ import annotations

import json
import io
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import tempfile


class _BotLogStream(io.TextIOBase):
    """Line-buffered, credential-filtered print/log output with bounded storage."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.pending = ""
        self.secrets = [os.environ.get("DISCORD_TOKEN", "")]
        try:
            self.secrets.extend(item.get("api_key", "") for item in json.loads(os.environ.get("AI_PROVIDERS_JSON", "[]")))
        except (ValueError, TypeError, AttributeError):
            pass

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def _record(self, text: str) -> None:
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        self.handler.emit(logging.LogRecord("bot-child", logging.INFO, "", 0, text, (), None))

    def write(self, text: str) -> int:
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self._record(line.rstrip("\r"))
        if len(self.pending) > 65536:
            self._record(self.pending)
            self.pending = ""
        return len(text)

    def flush(self) -> None:
        if self.pending:
            self._record(self.pending)
            self.pending = ""
        self.handler.flush()


def _result_path() -> Path | None:
    if "--result-file" not in sys.argv:
        return None
    index = sys.argv.index("--result-file")
    if index + 1 >= len(sys.argv):
        return None
    return Path(sys.argv[index + 1]).resolve()


def _bot_entry() -> int:
    # A --windowed EXE has no stdout/stderr, even when started with PIPE.  The
    # supervisor tails this per-profile file instead of waiting on that pipe.
    stream = None
    log_path = os.environ.get("BOT_LOG_FILE", "")
    if not log_path and (getattr(sys, "frozen", False) or sys.stdout is None):
        from desktop.launcher import default_data_dir

        result_path = _result_path()
        parent = result_path.parent if result_path else default_data_dir() / "logs"
        log_path = str(parent / f"bot-child-{os.getpid()}.log")
    if log_path:
        stream = _BotLogStream(Path(log_path))
        sys.stdout = sys.stderr = stream
    self_test = "--bot-self-test" in sys.argv
    temporary = None
    if self_test:
        os.environ["BOT_CONFIG_ISOLATED"] = "1"
        temporary = tempfile.TemporaryDirectory(prefix="discord-ai-bot-test-")
        directory = Path(temporary.name)
        for key, filename in (("STATE_FILE", "state.json"), ("DATABASE_FILE", "history.sqlite3"), ("CHARACTER_ASSET_DIR", "characters")):
            os.environ.setdefault(key, str(directory / filename))
    try:
        import bot  # Static import is required for PyInstaller module discovery.

        if self_test:
            opus = {"opus_checked": False}
            if os.name == "nt":
                import discord.opus
                import nacl.bindings
                import davey

                encoder = discord.opus.Encoder()
                packet = encoder.encode(bytes(encoder.FRAME_SIZE), encoder.SAMPLES_PER_FRAME)
                opus = {
                    "opus_checked": True,
                    "opus_loaded": discord.opus.is_loaded(),
                    "opus_version": encoder.get_opus_version(),
                    "opus_encoded_bytes": len(packet),
                    "opus_path": str(Path(discord.opus.__file__).parent / "bin" / "libopus-0.x64.dll"),
                }
                if not opus["opus_loaded"] or not packet or not Path(opus["opus_path"]).is_file():
                    raise RuntimeError("Bundled Opus self-test failed")
            result = {
                "ok": True,
                "module_loaded": "bot",
                "state_file": str(bot.STATE_FILE),
                "database_file": str(bot.DATABASE_FILE),
                "character_asset_dir": str(bot.CHARACTER_ASSET_DIR),
                "config_isolated": os.environ.get("BOT_CONFIG_ISOLATED") == "1",
                "ffmpeg_path": bot.find_ffmpeg(),
                "image_service_enabled": bot.IMAGE_SERVICE_ENABLED,
                "image_client_configured": bot.bot.image_client is not None,
                **opus,
            }
            result_path = _result_path()
            if result_path:
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if sys.stdout is not None:
                print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0
        bot.main()
        return 0
    except Exception as exc:
        if not self_test:
            raise
        result = {"ok": False, "error": type(exc).__name__}
        result_path = _result_path()
        if result_path:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
        if sys.stdout is not None:
            print(json.dumps(result), flush=True)
        return 1
    finally:
        if stream is not None:
            stream.flush()
        if temporary is not None:
            temporary.cleanup()


def main() -> int:
    if "--run-bot" in sys.argv or "--bot-self-test" in sys.argv:
        return _bot_entry()
    from desktop.launcher import main as desktop_main

    return desktop_main()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
