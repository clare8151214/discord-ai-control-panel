"""Desktop-only authentication and Discord wizard API regression coverage.

All Discord calls are replaced locally; these tests never contact Discord or a
model provider and never start a real Bot process.
"""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from web.app import create_app
from web.discord_setup import verify_discord_token
from web.models import BotProfile


PASSWORD = "desktop-api-test-password"
BOOTSTRAP = "initial-private-desktop-bootstrap"
HEADERS = {"X-Control-Panel": "1"}


@pytest.fixture
def desktop_app(tmp_path):
    return create_app(tmp_path / "desktop", password=PASSWORD, desktop_bootstrap_token=BOOTSTRAP)


@pytest.fixture
def desktop_client(desktop_app):
    with TestClient(desktop_app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.get("/desktop/bootstrap", params={"token": BOOTSTRAP}, follow_redirects=False)
        assert response.status_code == 303
        client.headers.update(HEADERS)
        yield client


def test_desktop_bootstrap_is_one_time_and_does_not_echo_credentials(desktop_app):
    with TestClient(desktop_app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/profiles").status_code == 401
        invalid = client.get("/desktop/bootstrap", params={"token": "wrong-private-token"})
        assert invalid.status_code == 403
        assert "wrong-private-token" not in invalid.text
        response = client.get("/desktop/bootstrap", params={"token": BOOTSTRAP}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"
        assert BOOTSTRAP not in response.text + str(response.headers)
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert "max-age" not in cookie and "expires=" not in cookie
        assert client.get("/api/session").json()["authenticated"]
        assert client.get("/api/session").json()["desktop"]
        replay = client.get("/desktop/bootstrap", params={"token": BOOTSTRAP})
        assert replay.status_code == 403 and BOOTSTRAP not in replay.text


@pytest.mark.parametrize("remote_address", ["192.168.1.100", "10.0.0.2", "203.0.113.42", "localhost"])
def test_remote_desktop_bootstrap_is_hidden_and_does_not_consume_token(desktop_app, remote_address):
    with TestClient(desktop_app, client=(remote_address, 50000)) as remote:
        response = remote.get("/desktop/bootstrap", params={"token": BOOTSTRAP})
        assert response.status_code == 404
        assert "set-cookie" not in response.headers
        assert BOOTSTRAP not in response.text
    with TestClient(desktop_app, client=("127.0.0.1", 50000)) as local:
        assert local.get("/desktop/bootstrap", params={"token": BOOTSTRAP}, follow_redirects=False).status_code == 303


def test_ipv6_loopback_bootstrap_is_allowed(desktop_app):
    with TestClient(desktop_app, base_url="http://[::1]", client=("::1", 50000)) as client:
        assert client.get("/desktop/bootstrap", params={"token": BOOTSTRAP}, follow_redirects=False).status_code == 303


def test_new_bootstrap_revokes_previous_unused_link(desktop_app):
    with TestClient(desktop_app, client=("127.0.0.1", 50000)) as client:
        fresh_token = desktop_app.state.issue_desktop_bootstrap()
        assert fresh_token != BOOTSTRAP
        assert client.get("/desktop/bootstrap", params={"token": BOOTSTRAP}).status_code == 403
        assert client.get("/desktop/bootstrap", params={"token": fresh_token}, follow_redirects=False).status_code == 303
        assert client.get("/desktop/bootstrap", params={"token": fresh_token}).status_code == 403


def test_web_mode_never_exposes_desktop_bootstrap(tmp_path):
    app = create_app(tmp_path / "web", password=PASSWORD)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/desktop/bootstrap", params={"token": BOOTSTRAP}).status_code == 404
        assert client.get("/api/desktop-info").status_code == 401
        assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
        assert client.get("/api/desktop-info").json() == {"desktop": False}
        assert not client.get("/api/session").json()["desktop"]


def test_desktop_info_requires_login_and_only_exposes_local_app_data(desktop_app):
    with TestClient(desktop_app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/desktop-info").status_code == 401
        client.get("/desktop/bootstrap", params={"token": BOOTSTRAP})
        response = client.get("/api/desktop-info")
        assert response.status_code == 200
        assert response.json()["desktop"]
        assert response.json()["data_dir"] == str(desktop_app.state.store.directory)
        assert response.json()["version"]
        assert PASSWORD not in response.text and BOOTSTRAP not in response.text


def test_desktop_login_remains_valid_beyond_web_session_expiration(desktop_client, monkeypatch):
    import web.app as app_module

    later = app_module.time.monotonic() + 43201
    monkeypatch.setattr(app_module, "time", SimpleNamespace(monotonic=lambda: later))
    assert desktop_client.get("/api/session").json()["authenticated"]
    assert desktop_client.get("/api/profiles").status_code == 200


def test_desktop_cookies_are_distinct_for_multiple_local_instances(tmp_path):
    first_app = create_app(tmp_path / "first", password=PASSWORD, desktop_bootstrap_token="first-bootstrap-token")
    second_app = create_app(tmp_path / "second", password=PASSWORD, desktop_bootstrap_token="second-bootstrap-token")
    with TestClient(first_app, base_url="http://127.0.0.1:10001", client=("127.0.0.1", 50000)) as first, TestClient(second_app, base_url="http://127.0.0.1:10002", client=("127.0.0.1", 50000)) as second:
        first.get("/desktop/bootstrap", params={"token": "first-bootstrap-token"})
        second.get("/desktop/bootstrap", params={"token": "second-bootstrap-token"})
        first_cookie = next(iter(first.cookies))
        second_cookie = next(iter(second.cookies))
        assert first_cookie != second_cookie
        # Real browsers share cookies across localhost ports.  Simulate both
        # cookies being present, then close only the second instance's session.
        first.cookies.update(second.cookies)
        second.cookies.update(first.cookies)
        assert first.get("/api/session").json()["authenticated"]
        assert second.get("/api/session").json()["authenticated"]
        assert second.post("/api/logout", headers=HEADERS).status_code == 200
        first.cookies.delete(second_cookie)
        assert first.get("/api/profiles").status_code == 200
        assert not second.get("/api/session").json()["authenticated"]


def test_desktop_ignores_secure_cookie_env_for_http_loopback(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTROL_PANEL_SECURE_COOKIE", "true")
    app = create_app(tmp_path / "desktop", password=PASSWORD, desktop_bootstrap_token=BOOTSTRAP)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.get("/desktop/bootstrap", params={"token": BOOTSTRAP}, follow_redirects=False)
        assert response.status_code == 303
        assert "; secure" not in response.headers["set-cookie"].lower()
        assert client.get("/api/profiles").status_code == 200


def test_discord_verify_requires_login_and_csrf(desktop_app, monkeypatch):
    calls = []

    async def verify(token):
        calls.append(token)
        return {"ok": True}

    monkeypatch.setattr("web.app.verify_discord_token", verify)
    with TestClient(desktop_app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        payload = {"discord_token": "private-token"}
        assert client.post("/api/discord/verify", headers=HEADERS, json=payload).status_code == 401
        client.get("/desktop/bootstrap", params={"token": BOOTSTRAP})
        assert client.post("/api/discord/verify", json=payload).status_code == 403
        assert client.post("/api/discord/verify", headers={**HEADERS, "Origin": "https://evil.example"}, json=payload).status_code == 403
        assert calls == []


def test_discord_verify_missing_token_never_calls_discord(desktop_client, monkeypatch):
    async def should_not_call(token):
        pytest.fail("Missing token must be rejected before any Discord call")

    monkeypatch.setattr("web.app.verify_discord_token", should_not_call)
    response = desktop_client.post("/api/discord/verify", json={})
    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "請先填入 Discord Bot Token"}
    assert desktop_client.post("/api/discord/verify", json={"discord_token": "   "}).json()["ok"] is False


def test_discord_verify_resolves_saved_token_and_can_use_new_token(desktop_client, desktop_app, monkeypatch):
    seen = []

    async def verify(token):
        seen.append(token)
        return {"ok": True, "application_id": "123456789012345678", "username": "Example Bot"}

    monkeypatch.setattr("web.app.verify_discord_token", verify)
    saved = desktop_app.state.store.save(BotProfile(discord_token="stored-private-token"))
    response = desktop_client.post("/api/discord/verify", json={"profile_id": saved["id"]})
    assert response.status_code == 200 and response.json()["application_id"] == "123456789012345678"
    assert seen == ["stored-private-token"]
    assert "stored-private-token" not in response.text
    response = desktop_client.post("/api/discord/verify", json={"profile_id": saved["id"], "discord_token": "  new-private-token  "})
    assert response.status_code == 200 and response.json()["ok"]
    assert seen[-1] == "new-private-token"
    assert "new-private-token" not in response.text
    assert desktop_app.state.store.get(saved["id"])["discord_token"] == "stored-private-token"
    assert desktop_client.post("/api/discord/verify", json={"profile_id": "missing-profile"}).status_code == 404


@pytest.mark.parametrize("submitted", ["sensitive-value" * 1000, {"sensitive-value": "invalid-type"}])
def test_discord_verify_validation_does_not_echo_secrets(desktop_client, submitted):
    response = desktop_client.post("/api/discord/verify", json={"discord_token": submitted})
    assert response.status_code == 422
    assert "sensitive-value" not in response.text


def fake_discord(monkeypatch, *, status=200, content=None):
    """Replace the helper's HTTP transport, including its async context managers."""
    requests = []
    raw = content if isinstance(content, bytes) else json.dumps(content).encode()

    class Response:
        def __init__(self):
            self.status = status
            self.content = self

        async def read(self, limit):
            return raw[:limit]

        async def iter_chunked(self, limit):
            for offset in range(0, len(raw), limit):
                yield raw[offset:offset + limit]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response()

    monkeypatch.setattr("web.discord_setup.aiohttp.ClientSession", Client)
    return requests


def test_discord_helper_uses_fixed_endpoint_and_only_returns_public_fields(monkeypatch):
    import asyncio

    requests = fake_discord(monkeypatch, content={"id": "123456789012345678", "name": "Application", "bot": {"username": "Friendly Bot"}, "token": "private-discord-token"})
    result = asyncio.run(verify_discord_token("private-discord-token"))
    assert result == {"ok": True, "application_id": "123456789012345678", "username": "Friendly Bot"}
    assert "private-discord-token" not in json.dumps(result)
    assert requests[0][0] == "https://discord.com/api/v10/oauth2/applications/@me"
    assert requests[0][1]["allow_redirects"] is False
    assert requests[0][1]["headers"] == {"Authorization": "Bot private-discord-token"}


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_discord_helper_returns_safe_http_errors(monkeypatch, status):
    import asyncio

    fake_discord(monkeypatch, status=status, content={"token": "private-discord-token", "error": "private-discord-token"})
    result = asyncio.run(verify_discord_token("private-discord-token"))
    assert result["ok"] is False and result["error"]
    assert "private-discord-token" not in json.dumps(result)


@pytest.mark.parametrize(
    "content",
    [b"not-json-private-discord-token", {"id": "not-valid"}, b"x" * 1_000_001],
    ids=["invalid-json", "invalid-user-id", "oversized-body"],
)
def test_discord_helper_rejects_malformed_or_oversize_responses(monkeypatch, content):
    import asyncio

    fake_discord(monkeypatch, content=content)
    result = asyncio.run(verify_discord_token("private-discord-token"))
    assert result["ok"] is False
    assert "private-discord-token" not in json.dumps(result)


def test_discord_helper_rejects_header_control_characters_without_network(monkeypatch):
    import asyncio

    requests = fake_discord(monkeypatch, content={})
    assert asyncio.run(verify_discord_token("private\nsecret"))["ok"] is False
    assert requests == []
