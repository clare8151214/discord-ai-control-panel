import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from web.app import create_app
from web.models import BotProfile
from web.runtime import RuntimeManager
from web.store import ProfileStore


PASSWORD = "image-setup-test-password"
HEADERS = {"X-Control-Panel": "1"}
IMAGE = {"comfyui_url": "http://127.0.0.1:8188", "comfyui_checkpoint": "NoobAI-test.safetensors", "ipadapter_model": "adapter-test.bin", "clip_vision_model": "vision-test.safetensors"}
PASSED = {"ok": True, "checks": [{"key": "service", "label": "ComfyUI", "ok": True, "error": ""}], "errors": []}
FAILED = {"ok": False, "checks": [{"key": "checkpoint", "label": "NoobAI", "ok": False, "error": "找不到指定模型"}], "errors": ["NoobAI：找不到指定模型"]}


@pytest.fixture
def setup(tmp_path):
    script = tmp_path / "fake.py"
    script.write_text("import time\nprint('Discord 已登入',flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    app = create_app(tmp_path / "panel", password=PASSWORD, bot_script=script)
    with TestClient(app) as client:
        client.post("/api/login", json={"password": PASSWORD})
        client.headers.update(HEADERS)
        yield app, client


def profile(client, **changes):
    response = client.post("/api/profiles", json={"discord_token": "never-connect", "default_model": "test", **changes})
    assert response.status_code == 201, response.text
    return response.json()


def test_image_check_auth_csrf_and_read_only_route(setup, monkeypatch):
    app, client = setup
    calls = []

    async def check(*args):
        calls.append(args)
        return PASSED

    monkeypatch.setattr("web.app.check_image_setup", check)
    response = client.post("/api/image/check", json=IMAGE)
    assert response.status_code == 200 and response.json() == PASSED
    assert calls == [tuple(IMAGE.values())]
    assert app.state.store.all() == [] and not app.state.manager.bots
    assert client.post("/api/image/check", json=IMAGE, headers={"Origin": "https://evil.test"}).status_code == 403
    client.headers.pop("X-Control-Panel")
    assert client.post("/api/image/check", json=IMAGE).status_code == 403
    client.cookies.clear()
    assert client.post("/api/image/check", json=IMAGE, headers=HEADERS).status_code == 401


def test_disabled_images_keep_settings_but_skip_checks_and_client(setup, monkeypatch):
    app, client = setup

    async def should_not_check(*args):
        pytest.fail("Text-only Bot must not contact ComfyUI")

    monkeypatch.setattr("web.runtime.check_image_setup", should_not_check)
    saved = profile(client, image_service_enabled=False, **IMAGE)
    stored = app.state.store.get(saved["id"])
    assert stored["comfyui_url"] == IMAGE["comfyui_url"]
    env = RuntimeManager.environment(stored, app.state.store.path(saved["id"]))
    assert env["COMFYUI_URL"] == ""
    assert env["IMAGE_SERVICE_ENABLED"] == "false"
    assert client.post(f"/api/profiles/{saved['id']}/start").status_code == 200
    assert client.post(f"/api/profiles/{saved['id']}/stop").status_code == 200


def test_enabled_images_must_have_url_and_model_names(setup):
    _, client = setup
    for changes in ({"comfyui_url": ""}, {"ipadapter_model": "\n"}, {"clip_vision_model": ""}):
        response = client.post("/api/profiles", json={"image_service_enabled": True, **IMAGE, **changes})
        assert response.status_code == 422


def test_failed_check_does_not_spawn_but_settings_can_be_saved(setup, monkeypatch):
    app, client = setup

    async def check(*args):
        return FAILED

    monkeypatch.setattr("web.runtime.check_image_setup", check)
    saved = profile(client, image_service_enabled=True, **IMAGE)
    response = client.post(f"/api/profiles/{saved['id']}/start")
    assert response.status_code == 409
    assert "找不到指定模型" in response.json()["detail"]
    assert not app.state.manager.bots
    assert not (app.state.store.path(saved["id"]) / "desired-running").exists()


def test_successful_fresh_check_starts_and_forwards_exact_model_names(setup, monkeypatch):
    app, client = setup
    calls = []

    async def check(*args):
        calls.append(args)
        return PASSED

    monkeypatch.setattr("web.runtime.check_image_setup", check)
    saved = profile(client, image_service_enabled=True, **IMAGE)
    url = f"/api/profiles/{saved['id']}"
    assert client.post(url + "/start").status_code == 200
    assert calls == [tuple(IMAGE.values())]
    stored = app.state.store.get(saved["id"])
    env = RuntimeManager.environment(stored, app.state.store.path(saved["id"]))
    assert env["COMFYUI_URL"] == IMAGE["comfyui_url"]
    assert env["IMAGE_SERVICE_ENABLED"] == "true"
    assert env["IPADAPTER_MODEL"] == IMAGE["ipadapter_model"]
    assert env["CLIP_VISION_MODEL"] == IMAGE["clip_vision_model"]
    assert client.post(url + "/stop").status_code == 200
    assert client.post(url + "/start").status_code == 200
    assert len(calls) == 2  # A previous successful check is never an authorization cache.


def test_failed_restart_preflight_does_not_stop_running_bot(setup, monkeypatch):
    app, client = setup
    report = PASSED

    async def check(*args):
        return report

    monkeypatch.setattr("web.runtime.check_image_setup", check)
    saved = profile(client, image_service_enabled=True, **IMAGE)
    url = f"/api/profiles/{saved['id']}"
    first = client.post(url + "/start").json()
    report = FAILED
    assert client.post(url + "/restart").status_code == 409
    assert app.state.manager.status(saved["id"])["pid"] == first["pid"]


def test_preflight_cannot_apply_stale_configuration(tmp_path, monkeypatch):
    async def scenario():
        store = ProfileStore(tmp_path)
        config = store.save(BotProfile(discord_token="fake", default_model="test", image_service_enabled=True, **IMAGE))
        manager = RuntimeManager(store)

        async def changed_during_check(*args):
            store.save(BotProfile.model_validate({**config, "image_service_enabled": False}), config["id"])
            return PASSED

        monkeypatch.setattr("web.runtime.check_image_setup", changed_during_check)
        with pytest.raises(ValueError, match="設定已變更"):
            await manager.start(config["id"])
        assert not manager.bots

    asyncio.run(scenario())


def test_legacy_image_profiles_are_normalized_without_rewriting(tmp_path):
    store = ProfileStore(tmp_path)
    saved = store.save(BotProfile(comfyui_url="http://old-comfy.test:8188"))
    path = store.path(saved["id"]) / "config.json"
    for key in ("image_service_enabled", "ipadapter_model", "clip_vision_model"):
        saved.pop(key)
    content = json.dumps(saved)
    path.write_text(content)
    normalized = store.get(saved["id"])
    assert normalized["image_service_enabled"] and normalized["ipadapter_model"]
    assert path.read_text() == content
    assert BotProfile().image_service_enabled is False


def test_disabled_global_images_ignore_previously_enabled_rooms(tmp_path):
    # Import and exercise the actual Bot coroutine, without a Discord session,
    # model request, ComfyUI request or filesystem migration.
    root = Path(__file__).resolve().parents[1]
    command = (
        "import asyncio,bot; "
        "assert bot.bot.image_client is None; "
        "asyncio.run(bot.deliver_scene_image({'image_enabled':True},'test',None,'scene')); "
        "print('disabled-image-room-skipped')"
    )
    env = {**os.environ, "PYTHONPATH": str(root), "BOT_CONFIG_ISOLATED": "1", "IMAGE_SERVICE_ENABLED": "false", "COMFYUI_URL": "http://127.0.0.1:9", "STATE_FILE": str(tmp_path / "state.json"), "DATABASE_FILE": str(tmp_path / "history.sqlite3"), "CHARACTER_ASSET_DIR": str(tmp_path / "characters")}
    result = subprocess.run([sys.executable, "-c", command], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "disabled-image-room-skipped" in result.stdout
