import asyncio
import json

import pytest

from web.discord_setup import verify_discord_token


def fake_discord(monkeypatch, *, status=200, chunks=None, error=None):
    calls = []

    class Stream:
        async def iter_chunked(self, size):
            for chunk in chunks or []:
                yield chunk

    class Context:
        async def __aenter__(self):
            if error:
                raise error
            return self

        async def __aexit__(self, *args):
            pass

    class Response(Context):
        content = Stream()

    Response.status = status

    class Client(Context):
        def __init__(self, **kwargs):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr("web.discord_setup.aiohttp.ClientSession", Client)
    return calls


def test_fragmented_discord_application_response(monkeypatch):
    raw = json.dumps({"id": "123456789012345678", "bot": {"username": "機器人"}}).encode()
    calls = fake_discord(monkeypatch, chunks=[raw[:7], raw[7:]])
    result = asyncio.run(verify_discord_token("sensitive-token"))
    assert result == {"ok": True, "application_id": "123456789012345678", "username": "機器人"}
    assert calls[0][1]["headers"] == {"Authorization": "Bot sensitive-token"}
    assert calls[0][1]["allow_redirects"] is False
    assert "sensitive-token" not in json.dumps(result)


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_discord_http_errors_never_echo_token_or_response(monkeypatch, status):
    fake_discord(monkeypatch, status=status, chunks=[b"sensitive-token response"])
    result = asyncio.run(verify_discord_token("sensitive-token"))
    assert not result["ok"]
    assert "sensitive-token" not in json.dumps(result)


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"id":"invalid"}', b'{"id":"123456789012345678","bot":[]}'])
def test_bad_discord_responses_are_safe(monkeypatch, raw):
    # Empty bot data falls back to the application name; invalid top-level data
    # or an invalid snowflake is an explicit failed check.
    fake_discord(monkeypatch, chunks=[raw])
    result = asyncio.run(verify_discord_token("token"))
    assert isinstance(result["ok"], bool)
    if raw != b'{"id":"123456789012345678","bot":[]}':
        assert not result["ok"]


def test_discord_response_size_limit_and_invalid_header(monkeypatch):
    fake_discord(monkeypatch, chunks=[b" " * 600_000, b" " * 600_000])
    assert "回應過大" in asyncio.run(verify_discord_token("token"))["error"]
    calls = fake_discord(monkeypatch)
    assert not asyncio.run(verify_discord_token("token\r\nheader"))["ok"]
    assert calls == []


def test_discord_timeout_is_reported_without_details(monkeypatch):
    fake_discord(monkeypatch, error=asyncio.TimeoutError("sensitive-token"))
    result = asyncio.run(verify_discord_token("sensitive-token"))
    assert not result["ok"]
    assert "sensitive-token" not in json.dumps(result)
