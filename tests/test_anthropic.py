"""Native Claude protocol checks using local fake HTTP servers, never paid APIs."""

import asyncio
from contextlib import asynccontextmanager

from aiohttp import web
import pytest

from providers.anthropic import AnthropicProvider
from providers.base import ProviderError


@asynccontextmanager
async def fake_api(handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        await runner.cleanup()


def provider(url="https://api.anthropic.com/v1", **changes):
    return AnthropicProvider(provider_id="claude", display_name="Claude", base_url=url, api_key="private-test-key", **changes)


def test_native_headers_models_pagination_and_multiplayer_messages():
    async def scenario():
        captured = []

        async def handler(request):
            assert request.headers["x-api-key"] == "private-test-key"
            assert request.headers["anthropic-version"] == "2023-06-01"
            assert "Authorization" not in request.headers
            assert "private-test-key" not in str(request.rel_url)
            if request.method == "GET":
                assert request.path == "/v1/models"
                assert request.query["limit"] == "1000"
                if request.query.get("after_id") == "claude-one":
                    return web.json_response({"data": [{"id": "claude-two"}], "has_more": False})
                return web.json_response({"data": [{"id": "claude-one", "display_name": "Claude One", "max_input_tokens": 200000}], "has_more": True, "last_id": "claude-one"})
            assert request.path == "/v1/messages"
            captured.append(await request.json())
            return web.json_response({"content": [{"type": "thinking", "thinking": "private reasoning"}, {"type": "text", "text": "  你好"}, {"type": "tool_use", "name": "ignore"}, {"type": "text", "text": "！  "}]})

        async with fake_api(handler) as url:
            client = provider(url)
            await client.start()
            try:
                models = await client.list_models()
                assert [(item.model_id, item.display_name) for item in models] == [("claude-one", "Claude One"), ("claude-two", "claude-two")]
                assert models[0].metadata == {"max_input_tokens": 200000}
                messages = [{"role": "system", "content": "使用繁體中文"}, {"role": "developer", "content": "保留角色"}, {"role": "user", "content": "甲：嗨"}, {"role": "user", "content": "乙：你好"}, {"role": "assistant", "content": "大家好"}, {"role": "user", "content": "繼續"}]
                assert await client.chat("claude-one", messages, max_output_tokens=500) == "你好！"
                assert captured == [{
                    "model": "claude-one", "max_tokens": 500, "stream": False,
                    "system": [{"type": "text", "text": "使用繁體中文"}, {"type": "text", "text": "保留角色"}],
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "甲：嗨"}, {"type": "text", "text": "乙：你好"}]},
                        {"role": "assistant", "content": [{"type": "text", "text": "大家好"}]},
                        {"role": "user", "content": [{"type": "text", "text": "繼續"}]},
                    ],
                }]
            finally:
                await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("send", [True, False])
def test_default_required_output_limit_and_optional_temperature(send):
    async def scenario():
        async def handler(request):
            payload = await request.json()
            assert payload["max_tokens"] == 1024
            assert "system" not in payload
            assert ("temperature" in payload) is send
            if send:
                assert payload["temperature"] == 0.3
            return web.json_response({"content": [{"type": "text", "text": "ok"}]})

        async with fake_api(handler) as url:
            client = provider(url, send_temperature=send, temperature=0.3)
            await client.start()
            try:
                assert await client.chat("claude", [{"role": "user", "content": "hello"}]) == "ok"
            finally:
                await client.close()

    asyncio.run(scenario())


def test_static_models_skip_discovery_but_probe_makes_real_read_only_request():
    async def scenario():
        requests = []

        async def handler(request):
            requests.append((request.method, request.path))
            return web.json_response({"data": [], "has_more": False})

        async with fake_api(handler) as url:
            client = provider(url, models=["manually-configured"])
            assert [item.model_id for item in await client.list_models()] == ["manually-configured"]
            assert requests == []
            await client.start()
            try:
                await client.probe()
                assert requests == [("GET", "/v1/models")]
            finally:
                await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [302, 400, 401, 403, 429, 500])
def test_errors_do_not_echo_response_or_follow_redirect(status):
    async def scenario():
        paths = []

        async def handler(request):
            paths.append(request.path)
            return web.Response(status=status, text="private-test-key private-user-prompt", headers={"Location": "/credential-leak"})

        async with fake_api(handler) as url:
            client = provider(url)
            await client.start()
            try:
                with pytest.raises(ProviderError) as error:
                    await client.chat("claude", [{"role": "user", "content": "private-user-prompt"}])
                assert str(status) in str(error.value)
                assert "private-test-key" not in str(error.value)
                assert "private-user-prompt" not in str(error.value)
                assert paths == ["/v1/messages"]
            finally:
                await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [{}, {"content": None}, {"content": [{"type": "text", "text": " "}]}, {"content": [{"type": "thinking", "thinking": "private-reasoning"}]}])
def test_missing_visible_text_is_safe_error(payload):
    async def scenario():
        async def handler(request):
            return web.json_response(payload)

        async with fake_api(handler) as url:
            client = provider(url)
            await client.start()
            try:
                with pytest.raises(ProviderError) as error:
                    await client.chat("claude", [{"role": "user", "content": "hi"}])
                assert "private-reasoning" not in str(error.value)
            finally:
                await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [{"data": None}, {"data": [], "has_more": "yes"}, {"data": [], "has_more": True}, {"data": [], "has_more": True, "last_id": "same-cursor"}])
def test_invalid_or_repeated_pagination_rejected(payload):
    async def scenario():
        async def handler(request):
            return web.json_response(payload)

        async with fake_api(handler) as url:
            client = provider(url)
            await client.start()
            try:
                with pytest.raises(ProviderError):
                    await client.list_models()
            finally:
                await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("url", ["https://user:password@api.example/v1", "https://api.example/v1?key=private-key", "file:///tmp/models"])
def test_url_credentials_are_rejected_without_echo(url):
    with pytest.raises(ProviderError) as error:
        provider(url)
    assert url not in str(error.value) and "password" not in str(error.value) and "private-key" not in str(error.value)


@pytest.mark.parametrize("kwargs", [{"send_temperature": "yes"}, {"send_temperature": True, "temperature": 1.1}])
def test_invalid_temperature_options_rejected(kwargs):
    with pytest.raises(ProviderError):
        provider(**kwargs)


@pytest.mark.parametrize("messages,limit", [([], 10), ([{"role": "system", "content": "only-system"}], 10), ([{"role": "tool", "content": "unsupported"}], 10), ([{"role": "user", "content": "hi"}], False), ([{"role": "user", "content": "hi"}], 0)])
def test_invalid_inputs_rejected_before_client_required(messages, limit):
    with pytest.raises(ProviderError):
        asyncio.run(provider().chat("claude", messages, max_output_tokens=limit))
