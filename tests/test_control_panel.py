import asyncio
import json
import os
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from web.app import create_app
from web.models import BotProfile
from web.runtime import RuntimeManager
from web.store import ProfileStore
from providers import ModelInfo, ProviderError


PASSWORD = "test-control-panel-password"
HEADERS = {"X-Control-Panel": "1"}


@pytest.fixture
def app(tmp_path):
    script = tmp_path / "fake_bot.py"
    script.write_text("import time\nprint('Discord 已登入', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    return create_app(tmp_path / "panel", password=PASSWORD, bot_script=script)


@pytest.fixture
def client(app):
    with TestClient(app) as session:
        assert session.post("/api/login", json={"password": PASSWORD}).status_code == 200
        session.headers.update(HEADERS)
        yield session


def payload(**changes):
    return {
        "name": "測試機器人",
        "discord_token": "discord-private-secret",
        "default_provider": "ollama", "default_model": "test-model",
        "providers": [{"id": "ollama", "name": "Ollama", "type": "ollama", "base_url": "http://127.0.0.1:11434", "api_key": "provider-private-secret"}],
        **changes,
    }


def test_auth_health_and_csrf(app):
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.get("/api/profiles").status_code == 401
        assert client.post("/api/login", json={"password": "bad"}).status_code == 401
        response = client.post("/api/login", json={"password": PASSWORD})
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        assert client.get("/api/session").json()["authenticated"]
        assert client.post("/api/profiles", json=payload()).status_code == 403
        assert client.post("/api/profiles", json=payload(), headers={**HEADERS, "Origin": "https://evil.test"}).status_code == 403
        assert client.get("/").headers["x-frame-options"] == "DENY"
        assert client.post("/api/logout", headers=HEADERS).status_code == 200
        assert client.get("/api/profiles").status_code == 401


def test_login_rate_limit(app):
    with TestClient(app) as client:
        for _ in range(5):
            assert client.post("/api/login", json={"password": "bad"}).status_code == 401
        assert client.post("/api/login", json={"password": PASSWORD}).status_code == 429


def test_secrets_preserved_masked_and_changed_endpoint_drops_credential(client, app):
    response = client.post("/api/profiles", json=payload())
    assert response.status_code == 201
    profile = response.json()
    profile_id = profile["id"]
    assert profile["discord_token"] == "" and profile["has_discord_token"]
    assert profile["providers"][0]["api_key"] == "" and profile["providers"][0]["has_api_key"]
    assert "private-secret" not in client.get("/api/profiles").text
    assert client.put(f"/api/profiles/{profile_id}", json=profile).status_code == 200
    stored = app.state.store.get(profile_id)
    assert stored["discord_token"] == "discord-private-secret"
    assert stored["providers"][0]["api_key"] == "provider-private-secret"
    profile["providers"][0]["base_url"] = "https://another-api.example/v1"
    assert client.put(f"/api/profiles/{profile_id}", json=profile).status_code == 200
    assert app.state.store.get(profile_id)["providers"][0]["api_key"] == ""
    profile["clear_discord_token"] = True
    assert not client.put(f"/api/profiles/{profile_id}", json=profile).json()["has_discord_token"]
    if os.name != "nt":
        assert (app.state.store.path(profile_id) / "config.json").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("changes", [
    {"default_provider": "missing"}, {"output_tokens": 8192}, {"retention_days": 0},
    {"providers": [{"id": "ollama", "base_url": "file:///etc/passwd"}]},
    {"providers": [{"id": "ollama", "base_url": "https://name:secret@example.test"}]},
    {"comfyui_url": "http://example.test/?api_key=secret"},
    {"providers": [{"id": "ollama"}, {"id": "ollama"}]},
])
def test_validation_never_echoes_submitted_secrets(client, changes):
    response = client.post("/api/profiles", json=payload(**changes))
    assert response.status_code == 422
    assert "private-secret" not in response.text


def test_bad_ids_and_deletion(client):
    assert client.get("/api/profiles/not-a-uuid/logs").status_code == 404
    profile = client.post("/api/profiles", json=payload()).json()
    assert client.post(f"/api/profiles/{profile['id']}/start").status_code == 200
    # This fixture runs an isolated fake process, never a real Discord session.
    client.post(f"/api/profiles/{profile['id']}/stop")
    assert client.delete(f"/api/profiles/{profile['id']}").status_code == 204
    assert client.get("/api/profiles").json() == {"profiles": []}


def test_import_is_explicit_and_masks_token(tmp_path):
    env = tmp_path / "legacy.env"
    env.write_text("DISCORD_TOKEN=import-private-secret\nDEFAULT_MODEL=existing\nOLLAMA_REMOTE_URL=http://192.168.0.2:11434\n", encoding="utf-8")
    denied = create_app(tmp_path / "denied", password=PASSWORD, legacy_env=env, allow_legacy_import=False)
    with TestClient(denied) as client:
        client.post("/api/login", json={"password": PASSWORD})
        assert client.post("/api/import-legacy", headers=HEADERS).status_code == 403
    app = create_app(tmp_path / "allowed", password=PASSWORD, legacy_env=env, allow_legacy_import=True)
    with TestClient(app) as client:
        client.post("/api/login", json={"password": PASSWORD})
        result = client.post("/api/import-legacy", headers=HEADERS)
        assert result.status_code == 201
        assert result.json()["has_discord_token"]
        assert "import-private-secret" not in result.text
        assert len(result.json()["providers"]) == 2
        assert client.get("/api/runtime").json()["bots"][0]["status"] == "stopped"


def test_runtime_isolation_restart_redaction_and_restore(tmp_path):
    async def scenario():
        store = ProfileStore(tmp_path / "panel")
        first = store.save(BotProfile.model_validate(payload()))
        second = store.save(BotProfile.model_validate(payload()))
        script = tmp_path / "fake_bot.py"
        script.write_text("import os,time\nprint(os.environ['DISCORD_TOKEN'], flush=True)\nprint('Discord 已登入', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
        manager = RuntimeManager(store, script)
        try:
            first_path = store.path(first["id"])
            env = manager.environment(first, first_path)
            assert env["BOT_CONFIG_ISOLATED"] == "1"
            assert Path(env["DATABASE_FILE"]).parent == first_path
            assert Path(env["CHARACTER_ASSET_DIR"]).parent == first_path
            assert json.loads(env["AI_PROVIDERS_JSON"])[0]["id"] == "ollama"
            assert not any(key.startswith("CONTROL_PANEL_") for key in env)
            await manager.start(first["id"])
            for _ in range(100):
                if manager.status(first["id"])["status"] == "online":
                    break
                await asyncio.sleep(0.02)
            status = manager.status(first["id"])
            assert status["status"] == "online"
            assert "discord-private-secret" not in "\n".join(status["logs"])
            assert "[REDACTED]" in "\n".join(status["logs"])
            with pytest.raises(ValueError, match="同一個 Discord Token"):
                await manager.start(second["id"])
            changed = BotProfile.model_validate({**first, "name": "已修改"})
            store.save(changed, first["id"])
            assert manager.status(first["id"])["config_changed"]
            await manager.shutdown()
            assert (first_path / "desired-running").exists()
            other_manager = RuntimeManager(store, script)
            try:
                await other_manager.restore()
                assert other_manager.status(first["id"])["pid"]
                await other_manager.stop(first["id"])
                assert not (first_path / "desired-running").exists()
            finally:
                await other_manager.shutdown()
        finally:
            await manager.shutdown()
    asyncio.run(scenario())


def test_password_required(tmp_path):
    with pytest.raises(RuntimeError, match="CONTROL_PANEL_PASSWORD"):
        create_app(tmp_path, password="short")


def test_anthropic_profile_round_trip_and_worker_configuration(client, app):
    response = client.post("/api/profiles", json=payload(
        default_provider="claude", default_model="claude-test-model",
        providers=[{"id": "claude", "name": "Claude", "type": "anthropic", "base_url": "https://api.anthropic.com/v1", "api_key": "anthropic-private-key"}],
    ))
    assert response.status_code == 201
    public = response.json()
    provider = public["providers"][0]
    assert provider["type"] == "anthropic" and provider["send_temperature"] is False
    assert provider["has_api_key"] and provider["api_key"] == ""
    assert "anthropic-private-key" not in response.text
    stored = app.state.store.get(public["id"])
    environment = RuntimeManager.environment(stored, app.state.store.path(public["id"]))
    configured = json.loads(environment["AI_PROVIDERS_JSON"])[0]
    assert environment["DEFAULT_PROVIDER"] == "claude"
    assert configured["type"] == "anthropic" and configured["api_key"] == "anthropic-private-key"
    assert client.put(f"/api/profiles/{public['id']}", json=public).status_code == 200
    assert app.state.store.get(public["id"])["providers"][0]["api_key"] == "anthropic-private-key"


def test_provider_probe_and_chat_resolve_saved_keys_without_leaking(client, monkeypatch):
    calls = []

    class FakeProvider:
        async def start(self):
            pass

        async def close(self):
            calls.append("closed")

        async def probe(self):
            calls.append("probe")

        async def list_models(self):
            return [ModelInfo("fake-model", "Fake Model")]

        async def chat(self, model, messages, **kwargs):
            assert model == "fake-model" and messages[-1]["content"] == "hello"
            assert kwargs["max_output_tokens"] == 2048
            return "模型連線成功"

    def factory(config, **kwargs):
        calls.append(config["api_key"])
        if "keep_alive" in kwargs:
            assert kwargs["context_length"] == 16384
        return FakeProvider()

    monkeypatch.setattr("web.app.create_provider", factory)
    profile = client.post("/api/profiles", json=payload()).json()
    probe_body = {"profile_id": profile["id"], "provider": profile["providers"][0]}
    response = client.post("/api/probe", json=probe_body)
    assert response.json()["models"][0]["id"] == "fake-model"
    assert "probe" in calls and "provider-private-secret" in calls
    assert "provider-private-secret" not in response.text
    response = client.post("/api/chat-test", json={**probe_body, "model": "fake-model", "message": "hello", "context_length": 16384, "output_tokens": 2048})
    assert response.json() == {"ok": True, "reply": "模型連線成功"}
    assert calls.count("closed") == 2
    def broken_factory(config, **kwargs):
        raise ProviderError("endpoint failed")
    monkeypatch.setattr("web.app.create_provider", broken_factory)
    assert not client.post("/api/probe", json=probe_body).json()["ok"]
    assert not client.post("/api/chat-test", json={**probe_body, "model": "fake-model"}).json()["ok"]


def test_restart_applies_persisted_profile_changes(client):
    profile = client.post("/api/profiles", json=payload()).json()
    url = f"/api/profiles/{profile['id']}"
    first = client.post(f"{url}/start").json()
    assert client.delete(url).status_code == 409
    profile["name"] = "新名稱"
    assert client.put(url, json=profile).status_code == 200
    assert client.get("/api/runtime").json()["bots"][0]["config_changed"]
    second = client.post(f"{url}/restart").json()
    assert second["pid"] != first["pid"] and not second["config_changed"]
    assert client.post(f"{url}/stop").json()["status"] == "stopped"
