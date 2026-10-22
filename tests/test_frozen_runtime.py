import asyncio
import json
import sys
from types import SimpleNamespace

from web.models import BotProfile
from web.runtime import RuntimeManager
from web.store import ProfileStore


def test_frozen_entrypoint_and_source_override(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    store = ProfileStore(tmp_path)
    assert RuntimeManager(store).child_command() == [sys.executable, "--run-bot"]
    script = tmp_path / "custom.py"
    assert RuntimeManager(store, script).child_command() == [sys.executable, "-u", str(script)]


def test_frozen_runtime_tails_child_file_and_stops_it(tmp_path, monkeypatch):
    async def scenario():
        store = ProfileStore(tmp_path / "profiles")
        profile = store.save(BotProfile(discord_token="never-login-token", default_model="test-model"))
        manager = RuntimeManager(store)
        manager.frozen_runner = True
        script = tmp_path / "file_child.py"
        script.write_text(
            "import os,time\n"
            "with open(os.environ['BOT_LOG_FILE'], 'a', encoding='utf-8') as log:\n"
            " log.write(os.environ['DISCORD_TOKEN'] + '\\nDiscord 已登入\\n'); log.flush()\n"
            " time.sleep(60)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(manager, "child_command", lambda: [sys.executable, "-u", str(script)])
        monkeypatch.setenv("BOT_LOG_FILE", "must-not-inherit.log")
        try:
            await manager.start(profile["id"])
            for _ in range(100):
                if manager.status(profile["id"])["status"] == "online":
                    break
                await asyncio.sleep(.03)
            status = manager.status(profile["id"])
            assert status["status"] == "online"
            assert "never-login-token" not in json.dumps(status)
            assert "[REDACTED]" in "\n".join(status["logs"])
            assert (store.path(profile["id"]) / "process-output.log").exists()
            assert (await manager.stop(profile["id"]))["status"] == "stopped"
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


def test_tailing_handles_rotation_and_truncation(tmp_path):
    async def scenario():
        path = tmp_path / "output.log"
        path.write_bytes(b"first\n")
        process = SimpleNamespace(returncode=None)
        iterator = RuntimeManager._output_chunks(process, path)
        assert await anext(iterator) == b"first\n"
        path.rename(tmp_path / "output.log.1")
        path.write_bytes(b"rotated\n")
        assert await anext(iterator) == b"rotated\n"
        path.write_bytes(b"new\n")
        assert await anext(iterator) == b"new\n"
        process.returncode = 0
        assert [chunk async for chunk in iterator] == []

    asyncio.run(scenario())
