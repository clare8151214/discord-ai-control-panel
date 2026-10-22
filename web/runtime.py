from __future__ import annotations

import asyncio
import codecs
from collections import deque
import contextlib
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
from typing import Any

from .store import ProfileStore
from .image_setup import check_image_setup
from .models import DEFAULT_IPADAPTER, DEFAULT_CLIP_VISION


ROOT = Path(__file__).resolve().parent.parent


def fingerprint(config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


class RuntimeManager:
    def __init__(self, store: ProfileStore, bot_script: Path | None = None) -> None:
        self.store = store
        self.bot_script = bot_script or ROOT / "bot.py"
        self.frozen_runner = bool(getattr(sys, "frozen", False)) and bot_script is None
        self.bots: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()

    def status(self, profile_id: str) -> dict[str, Any]:
        entry = self.bots.get(profile_id)
        if not entry:
            return {"id": profile_id, "status": "stopped", "pid": None, "exit_code": None, "config_changed": False, "logs": self.read_logs(profile_id)}
        process = entry["process"]
        code = process.returncode
        return {
            "id": profile_id,
            "status": entry["status"] if code is None else ("stopped" if code == 0 or entry.get("stopped") else "failed"),
            "pid": process.pid if code is None else None,
            "exit_code": code,
            "config_changed": code is None and entry["fingerprint"] != fingerprint(self.store.get(profile_id)),
            "logs": list(entry["logs"]),
        }

    def read_logs(self, profile_id: str) -> list[str]:
        path = self.store.path(profile_id) / "bot.log"
        if not path.exists():
            return []
        with path.open(encoding="utf-8", errors="replace") as stream:
            return list(deque((line.rstrip() for line in stream), maxlen=200))

    @staticmethod
    def environment(config: dict[str, Any], directory: Path) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("CONTROL_PANEL_")}
        mapping = {
            "BOT_CONFIG_ISOLATED": "1",
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "DISCORD_TOKEN": config["discord_token"],
            "DISCORD_GUILD_ID": config["guild_id"],
            "MESSAGE_CONTENT_INTENT": str(config["message_content_intent"]).lower(),
            "ADMIN_ONLY_SETTINGS": str(config["admin_only_settings"]).lower(),
            "AI_PROVIDERS_JSON": json.dumps(config["providers"]),
            "DEFAULT_PROVIDER": config["default_provider"],
            "DEFAULT_MODEL": config["default_model"],
            "DEFAULT_SYSTEM_PROMPT": config["system_prompt"],
            "OLLAMA_CONTEXT_LENGTH": str(config["context_length"]),
            "CHAT_MAX_OUTPUT_TOKENS": str(config["output_tokens"]),
            "MEMORY_OUTPUT_RESERVE": str(min(config["context_length"] // 2, max(1536, config["output_tokens"]))),
            "MEMORY_COMPACT_TRIGGER": str(config["compact_trigger"]),
            "ARCHIVE_RETENTION_DAYS": str(config["retention_days"]),
            "STATE_FILE": str(directory / "state.json"),
            "DATABASE_FILE": str(directory / "history.sqlite3"),
            "CHARACTER_ASSET_DIR": str(directory / "characters"),
            "IMAGE_SERVICE_ENABLED": str(config.get("image_service_enabled", bool(config["comfyui_url"]))).lower(),
            "COMFYUI_URL": config["comfyui_url"] if config.get("image_service_enabled", bool(config["comfyui_url"])) else "",
            "COMFYUI_CHECKPOINT": config["comfyui_checkpoint"],
            "IPADAPTER_MODEL": config.get("ipadapter_model", DEFAULT_IPADAPTER),
            "CLIP_VISION_MODEL": config.get("clip_vision_model", DEFAULT_CLIP_VISION),
            "TTS_DEFAULT_VOICE": config["tts_voice"],
            "LOG_LEVEL": "INFO",
        }
        env.update(mapping)
        env.pop("BOT_LOG_FILE", None)
        return env

    def child_command(self) -> list[str]:
        if self.frozen_runner:
            return [sys.executable, "--run-bot"]
        return [sys.executable, "-u", str(self.bot_script)]

    async def validate_configuration(self, profile_id: str) -> dict[str, Any]:
        config = self.store.get(profile_id)
        if not config["discord_token"] or not config["default_model"]:
            raise ValueError("請先設定 Discord Bot Token 與預設模型")
        if config["image_service_enabled"]:
            report = await check_image_setup(
                config["comfyui_url"], config["comfyui_checkpoint"],
                config["ipadapter_model"], config["clip_vision_model"],
            )
            if not report["ok"]:
                reasons = "；".join(report.get("errors", [])[:3])
                raise ValueError("生圖依賴檢查未通過：" + (reasons or "必要模型或節點未就緒") + "。請前往「圖片與語音 → 檢查生圖依賴」，或關閉生圖後啟動純文字 Bot。")
        return config

    async def start(self, profile_id: str) -> dict[str, Any]:
        # Network preflight must not hold the process lock and block Stop or
        # shutdown for unrelated Bots while a ComfyUI endpoint is offline.
        checked_config = await self.validate_configuration(profile_id)
        async with self.lock:
            config = self.store.get(profile_id)
            if fingerprint(config) != fingerprint(checked_config):
                raise ValueError("檢查期間設定已變更，請重新啟動以檢查最新設定")
            previous = self.bots.get(profile_id)
            if previous and (previous.get("stopping") or not previous["reader"].done() and previous["process"].returncode is not None):
                raise ValueError("Bot 正在停止或整理日誌，請稍後再啟動")
            for other_id, entry in self.bots.items():
                if entry["process"].returncode is not None:
                    continue
                if other_id == profile_id:
                    raise ValueError("此 Bot 已在執行")
                if entry["token_digest"] == hashlib.sha256(config["discord_token"].encode()).hexdigest():
                    raise ValueError("同一個 Discord Token 已由其他 Bot 設定執行")
            directory = self.store.path(profile_id)
            log_handler = RotatingFileHandler(directory / "bot.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
            env = self.environment(config, directory)
            output_file = directory / "process-output.log" if self.frozen_runner else None
            if output_file:
                output_file.touch(mode=0o600, exist_ok=True)
                output_file.write_text("", encoding="utf-8")
                env["BOT_LOG_FILE"] = str(output_file)
                bundled_bin = Path(getattr(sys, "_MEIPASS", ROOT)) / "bin"
                env["PATH"] = str(bundled_bin) + os.pathsep + env.get("PATH", "")
            try:
                process = await asyncio.create_subprocess_exec(
                    *self.child_command(), cwd=str(ROOT), env=env,
                    stdout=asyncio.subprocess.DEVNULL if self.frozen_runner else asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT, limit=262144,
                    **({"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NO_WINDOW}),
                )
            except BaseException:
                log_handler.close()
                raise
            entry = {
                "process": process, "status": "starting", "logs": deque(maxlen=200),
                "fingerprint": fingerprint(config), "stopped": False,
                "token_digest": hashlib.sha256(config["discord_token"].encode()).hexdigest(),
            }
            self.bots[profile_id] = entry
            secrets = [config["discord_token"], *(item.get("api_key", "") for item in config["providers"])]
            entry["reader"] = asyncio.create_task(self._read(entry, log_handler, secrets, output_file))
            (directory / "desired-running").touch(mode=0o600)
            return self.status(profile_id)

    @staticmethod
    async def _output_chunks(process, output_file: Path | None):
        if output_file is None:
            while raw := await process.stdout.read(4096):
                yield raw
            return
        offset = 0
        identity = None
        while True:
            raw = b""
            try:
                stat = output_file.stat()
                current_identity = (stat.st_dev, stat.st_ino)
                if current_identity != identity or stat.st_size < offset:
                    offset = 0
                    identity = current_identity
                with output_file.open("rb") as stream:
                    stream.seek(offset)
                    raw = stream.read(65536)
                offset += len(raw)
            except FileNotFoundError:
                pass
            if raw:
                yield raw
            elif process.returncode is not None:
                return
            else:
                await asyncio.sleep(0.15)

    async def _read(self, entry: dict[str, Any], handler: RotatingFileHandler, secrets: list[str], output_file: Path | None = None) -> None:
        process = entry["process"]
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buffer = ""

        def redact(text: str) -> str:
            for secret in secrets:
                if secret:
                    text = text.replace(secret, "[REDACTED]")
            return text

        def record(text: str) -> None:
            text = redact(text).rstrip()[:16000]
            entry["logs"].append(text)
            handler.emit(logging.LogRecord("bot", logging.INFO, "", 0, text, (), None))
            if "Discord 已登入" in text:
                entry["status"] = "online"

        try:
            async for raw in self._output_chunks(process, output_file):
                buffer += decoder.decode(raw)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    record(line)
                if len(buffer) > 65536:
                    # Redact before splitting; keep enough trailing text to catch
                    # a credential spanning the next chunk (keys are <=8192 chars).
                    buffer = redact(buffer)
                    record(buffer[:32768])
                    buffer = buffer[32768:]
            buffer += decoder.decode(b"", final=True)
            if buffer:
                record(buffer)
            await process.wait()
        finally:
            handler.close()

    async def _terminate(self, entry: dict[str, Any]) -> None:
        process = entry["process"]
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                if os.name == "nt":
                    # A one-file executable has its own bootloader process;
                    # terminate the tree so its Python worker/FFmpeg cannot linger.
                    taskkill = shutil.which("taskkill")
                    if taskkill:
                        killer = await asyncio.create_subprocess_exec(
                            taskkill, "/PID", str(process.pid), "/T", "/F",
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        await killer.wait()
                    else:
                        process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=12)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        await entry["reader"]

    async def stop(self, profile_id: str, *, preserve_desired: bool = False) -> dict[str, Any]:
        async with self.lock:
            self.store.get(profile_id)
            entry = self.bots.get(profile_id)
            if not preserve_desired:
                (self.store.path(profile_id) / "desired-running").unlink(missing_ok=True)
            if entry:
                entry["stopped"] = True
                entry["stopping"] = True
                if not entry.get("stop_task"):
                    entry["stop_task"] = asyncio.create_task(self._terminate(entry))
        # Do not hold the manager lock while waiting. All bots must receive
        # SIGTERM together during a 30-second Kubernetes termination window.
        if entry:
            await asyncio.shield(entry["stop_task"])
        async with self.lock:
            if entry:
                entry["stopping"] = False
            return self.status(profile_id)

    async def delete(self, profile_id: str) -> None:
        async with self.lock:
            self.store.get(profile_id)
            entry = self.bots.get(profile_id)
            if entry and (entry["process"].returncode is None or entry.get("stopping") or not entry["reader"].done()):
                raise ValueError("請先停止 Bot，再刪除設定與資料")
            shutil.rmtree(self.store.path(profile_id))
            self.bots.pop(profile_id, None)

    async def restore(self) -> None:
        for profile in self.store.all():
            if (self.store.path(profile["id"]) / "desired-running").exists():
                try:
                    await self.start(profile["id"])
                except (ValueError, OSError):
                    logging.getLogger(__name__).exception("Could not restore bot %s", profile["id"])

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.stop(profile_id, preserve_desired=True) for profile_id in list(self.bots)))
