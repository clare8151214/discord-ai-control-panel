import asyncio

import pytest

from web.models import BotProfile
from web.runtime import RuntimeManager
from web.store import ProfileStore


def prepare(tmp_path):
    store = ProfileStore(tmp_path / "panel")
    profile = store.save(BotProfile(discord_token="fake-token", default_model="test"))
    script = tmp_path / "fake_bot.py"
    script.write_text("import time\ntime.sleep(60)\n")
    return RuntimeManager(store, script), profile["id"]


def test_delete_waits_for_an_in_flight_start(tmp_path, monkeypatch):
    async def scenario():
        manager, profile_id = prepare(tmp_path)
        real_spawn = asyncio.create_subprocess_exec
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_spawn(*args, **kwargs):
            entered.set()
            await release.wait()
            return await real_spawn(*args, **kwargs)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
        try:
            starting = asyncio.create_task(manager.start(profile_id))
            await entered.wait()
            deleting = asyncio.create_task(manager.delete(profile_id))
            await asyncio.sleep(0)
            assert not deleting.done()
            assert (manager.store.path(profile_id) / "config.json").exists()
            release.set()
            assert (await starting)["pid"]
            with pytest.raises(ValueError, match="停止"):
                await deleting
            assert manager.status(profile_id)["pid"]
        finally:
            release.set()
            await manager.shutdown()
    asyncio.run(scenario())


def test_restart_waits_for_previous_reader_to_finish(tmp_path):
    async def scenario():
        manager, profile_id = prepare(tmp_path)
        await manager.start(profile_id)
        entry = manager.bots[profile_id]
        real_reader = entry["reader"]
        finishing, release = asyncio.Event(), asyncio.Event()

        async def gated_reader():
            await real_reader
            finishing.set()
            await release.wait()

        entry["reader"] = asyncio.create_task(gated_reader())
        try:
            stopping = asyncio.create_task(manager.stop(profile_id))
            await finishing.wait()
            with pytest.raises(ValueError, match="停止或整理日誌"):
                await manager.start(profile_id)
            with pytest.raises(ValueError, match="停止"):
                await manager.delete(profile_id)
            release.set()
            assert (await stopping)["status"] == "stopped"
            assert (await manager.start(profile_id))["pid"]
        finally:
            release.set()
            await manager.shutdown()
    asyncio.run(scenario())
