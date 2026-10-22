"""Exercise real HTTP requests against local fake vendor APIs (no keys/network needed)."""

import asyncio
from contextlib import asynccontextmanager
import json

from aiohttp import web
import pytest

from providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
    build_provider_registry,
    create_provider,
)


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
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


def run(coro):
    return asyncio.run(coro)


def test_openai_discovery_and_chat_protocol():
    async def scenario():
        captured = []

        async def handler(request):
            assert request.headers["Authorization"] == "Bearer secret-test-key"
            assert "secret-test-key" not in str(request.rel_url)
            if request.method == "GET":
                assert request.path == "/v1/models"
                return web.json_response({"data": [{"id": "coder", "name": "Coder", "owned_by": "test"}]})
            assert request.path == "/v1/chat/completions"
            captured.append(await request.json())
            return web.json_response({"choices": [{"message": {"role": "assistant", "content": "  你好  "}}]})

        async with fake_api(handler) as url:
            provider = create_provider({"id": "cloud", "type": "openai", "base_url": url + "/v1/", "api_key": "secret-test-key"}, temperature=0.3)
            await provider.start()
            try:
                models = await provider.list_models()
                assert [(m.model_id, m.display_name) for m in models] == [("coder", "Coder")]
                assert models[0].metadata == {"owned_by": "test"}
                messages = [{"role": "system", "content": "使用繁體中文"}, {"role": "user", "content": "嗨"}]
                assert await provider.chat("coder", messages, max_output_tokens=123) == "你好"
                assert captured == [{"model": "coder", "messages": messages, "temperature": 0.3, "stream": False, "max_tokens": 123}]
            finally:
                await provider.close()

    run(scenario())


def test_gemini_native_protocol_history_thought_filter_and_pagination():
    async def scenario():
        captured = []

        async def handler(request):
            assert request.headers["x-goog-api-key"] == "gemini-secret"
            assert "Authorization" not in request.headers
            assert "key" not in request.query
            if request.method == "GET":
                assert request.path == "/v1beta/models"
                assert request.query["pageSize"] == "1000"
                if request.query.get("pageToken") == "page2":
                    return web.json_response({"models": [{"name": "models/fast", "supportedGenerationMethods": ["generateContent"]}]})
                return web.json_response({
                    "models": [
                        {"name": "models/gemini-chat", "displayName": "Gemini Chat", "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 1000},
                        {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]},
                    ],
                    "nextPageToken": "page2",
                })
            assert request.path == "/v1beta/models/gemini-chat:generateContent"
            captured.append(await request.json())
            return web.json_response({"candidates": [{"content": {"role": "model", "parts": [{"text": "private thought", "thought": True}, {"text": "你好"}, {"text": "！"}]}}]})

        async with fake_api(handler) as url:
            provider = create_provider({"type": "gemini", "base_url": url + "/v1beta", "api_key": "gemini-secret"})
            await provider.start()
            try:
                models = await provider.list_models()
                assert [m.model_id for m in models] == ["gemini-chat", "fast"]
                assert models[0].metadata == {"inputTokenLimit": 1000}
                messages = [
                    {"role": "system", "content": "角色卡"},
                    {"role": "user", "content": "甲：嗨"},
                    {"role": "user", "content": "乙：你好"},
                    {"role": "assistant", "content": "嗨，兩位"},
                    {"role": "user", "content": "繼續"},
                ]
                assert await provider.chat("models/gemini-chat", messages, max_output_tokens=500) == "你好！"
                assert captured == [{
                    "systemInstruction": {"parts": [{"text": "角色卡"}]},
                    "contents": [
                        {"role": "user", "parts": [{"text": "甲：嗨"}, {"text": "乙：你好"}]},
                        {"role": "model", "parts": [{"text": "嗨，兩位"}]},
                        {"role": "user", "parts": [{"text": "繼續"}]},
                    ],
                    "generationConfig": {"temperature": 0.7, "maxOutputTokens": 500},
                }]
            finally:
                await provider.close()

    run(scenario())


def test_ollama_legacy_constructor_and_payload_are_preserved():
    async def scenario():
        captured = []

        async def handler(request):
            assert "Authorization" not in request.headers
            if request.method == "GET":
                assert request.path == "/api/tags"
                return web.json_response({"models": [{"name": "gpt-oss:20b", "size": 14000000000, "details": {"parameter_size": "20B"}}]})
            assert request.path == "/api/chat"
            captured.append(await request.json())
            return web.json_response({"message": {"content": "完成"}})

        async with fake_api(handler) as url:
            provider = OllamaProvider(url, 4096, 0.5, "-1")
            assert provider.provider_id == "ollama"
            await provider.start()
            try:
                models = await provider.list_models()
                assert models[0].size_bytes == 14000000000
                messages = [{"role": "user", "content": "寫程式"}]
                assert await provider.chat("gpt-oss:20b", messages, max_output_tokens=90) == "完成"
                assert captured == [{"model": "gpt-oss:20b", "messages": messages, "stream": False, "think": False, "keep_alive": -1, "options": {"num_ctx": 4096, "temperature": 0.5, "num_predict": 90}}]
            finally:
                await provider.close()

    run(scenario())


@pytest.mark.parametrize("kind, cls", [("ollama", OllamaProvider), ("openai", OpenAICompatibleProvider), ("gemini", GeminiProvider), ("anthropic", AnthropicProvider)])
def test_manual_models_do_not_require_discovery_or_client(kind, cls):
    provider = create_provider({"id": "custom", "name": "Custom API", "type": kind, "models": ["first", "second", "first"]})
    assert isinstance(provider, cls)
    assert provider.display_name == "Custom API"
    assert [m.model_id for m in run(provider.list_models())] == ["first", "second"]


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://host", "http://", "https://user:password@api.test/v1", "http://user@api.test",
    "https://api.test/v1?key=secret", "http://api.test/#secret", "http://api.test/?", "http://api.test/#",
    "http://api.test:invalid", "http://api.test\n/", "http://api.test\\@another-host", "http://api.test /v1",
])
def test_factory_rejects_unsafe_urls_without_echoing_credentials(url):
    with pytest.raises(ProviderError) as exc:
        create_provider({"id": "cloud", "type": "openai", "base_url": url, "api_key": "super-secret"})
    assert url not in str(exc.value)
    assert "super-secret" not in str(exc.value)


@pytest.mark.parametrize("config", [
    None, [], {"type": "unsupported"}, {"type": {}}, {"id": "UPPER"}, {"id": "../escape"},
    {"models": "model"}, {"models": [1]}, {"models": [""]}, {"name": []}, {"name": "x\ny"}, {"api_key": "secret\nheader"},
])
def test_factory_rejects_invalid_config(config):
    with pytest.raises(ProviderError):
        create_provider(config)


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 429, 500])
def test_remote_errors_never_reflect_body_or_keys(status):
    async def scenario():
        async def handler(request):
            return web.Response(status=status, text='<script>alert("secret-api-key")</script> @everyone private prompt', headers={"Location": "/leak"})

        async with fake_api(handler) as url:
            provider = create_provider({"type": "openai", "base_url": url, "api_key": "secret-api-key"})
            await provider.start()
            try:
                with pytest.raises(ProviderError) as exc:
                    await provider.chat("model", [{"role": "user", "content": "private prompt"}])
                text = str(exc.value)
                assert str(status) in text
                assert not any(s in text for s in ["secret-api-key", "script", "@everyone", "private prompt"])
            finally:
                await provider.close()

    run(scenario())


@pytest.mark.parametrize("body", ["not json secret-api-key", "[]", "null", '{"error": {"message": "secret-api-key"}}', '{"choices": []}', '{"choices": [{"message": {"content": null}}]}', '{"choices": [{"message": {"content": 42}}]}'])
def test_invalid_json_or_empty_outputs_raise_uniform_error(body):
    async def scenario():
        async def handler(request):
            return web.Response(text=body)

        async with fake_api(handler) as url:
            provider = create_provider({"type": "openai", "base_url": url, "api_key": "secret-api-key"})
            await provider.start()
            try:
                with pytest.raises(ProviderError) as exc:
                    await provider.chat("model", [{"role": "user", "content": "hi"}])
                assert "secret-api-key" not in str(exc.value)
            finally:
                await provider.close()

    run(scenario())


def test_openai_text_content_blocks_supported():
    async def scenario():
        async def handler(request):
            payload = await request.json()
            assert "max_tokens" not in payload
            return web.json_response({"choices": [{"message": {"content": [{"type": "text", "text": "一"}, {"type": "image_url"}, {"type": "text", "text": "二"}]}}]})

        async with fake_api(handler) as url:
            provider = create_provider({"type": "openai", "base_url": url})
            await provider.start()
            try:
                assert await provider.chat("model", [{"role": "user", "content": "hi"}]) == "一二"
            finally:
                await provider.close()

    run(scenario())


def test_registry_legacy_defaults_and_json_override(monkeypatch):
    monkeypatch.delenv("AI_PROVIDERS_JSON", raising=False)
    legacy = build_provider_registry("http://127.0.0.1:11434", 8192, 0.7, "http://192.0.2.1:11434")
    assert [s.provider_id for s in legacy.specs()] == ["ollama", "ollama-remote", "gemini", "grok"]
    assert not legacy.spec("grok").enabled
    monkeypatch.setenv("AI_PROVIDERS_JSON", json.dumps([{"id": "deepseek", "type": "openai", "base_url": "https://api.deepseek.com", "models": ["coder"]}]))
    configured = build_provider_registry("http://127.0.0.1:11434", 8192, 0.7)
    assert [s.provider_id for s in configured.specs()] == ["deepseek"]
    assert isinstance(configured.get("deepseek"), OpenAICompatibleProvider)


@pytest.mark.parametrize("json_value", ["", "not-json-secret", "{}", "[]", '[{"id":"same"},{"id":"same"}]'])
def test_registry_rejects_invalid_override_and_duplicate_ids(monkeypatch, json_value):
    monkeypatch.setenv("AI_PROVIDERS_JSON", json_value)
    with pytest.raises(ProviderError) as exc:
        build_provider_registry("http://localhost:11434", 8192, 0.7)
    assert "not-json-secret" not in str(exc.value)


def test_start_and_close_are_idempotent_and_require_started_client():
    async def scenario():
        provider = create_provider({"type": "openai"})
        with pytest.raises(ProviderError, match="尚未啟動"):
            await provider.list_models()
        await provider.start()
        session = provider.session
        await provider.start()
        assert provider.session is session
        await provider.close()
        await provider.close()
        assert session.closed
        await provider.start()
        assert provider.session is not session
        await provider.close()

    run(scenario())


@pytest.mark.parametrize("kind, path", [("ollama", "/api/tags"), ("openai", "/models"), ("gemini", "/models"), ("anthropic", "/models")])
def test_probe_makes_real_request_with_manually_configured_models(kind, path):
    async def scenario():
        calls = []

        async def handler(request):
            calls.append(request.path)
            return web.Response(status=403, text="upstream-secret")

        async with fake_api(handler) as url:
            provider = create_provider({"type": kind, "base_url": url, "models": ["manual-model"]})
            await provider.start()
            try:
                assert [m.model_id for m in await provider.list_models()] == ["manual-model"]
                assert calls == []
                with pytest.raises(ProviderError, match="HTTP 403"):
                    await provider.probe()
                assert calls == [path]
            finally:
                await provider.close()

    run(scenario())


@pytest.mark.parametrize("kind, body", [
    ("ollama", {"models": None}),
    ("openai", {"data": None}),
    ("gemini", {"models": None}),
    ("anthropic", {"data": None}),
])
def test_invalid_model_discovery_response(kind, body):
    async def scenario():
        async def handler(request):
            return web.json_response(body)

        async with fake_api(handler) as url:
            provider = create_provider({"type": kind, "base_url": url})
            await provider.start()
            try:
                with pytest.raises(ProviderError):
                    await provider.list_models()
            finally:
                await provider.close()

    run(scenario())


def test_gemini_duplicate_pagination_token_is_rejected():
    async def scenario():
        async def handler(request):
            return web.json_response({"models": [], "nextPageToken": "same-token"})

        async with fake_api(handler) as url:
            provider = create_provider({"type": "gemini", "base_url": url})
            await provider.start()
            try:
                with pytest.raises(ProviderError, match="分頁"):
                    await provider.list_models()
            finally:
                await provider.close()

    run(scenario())


@pytest.mark.parametrize("kind", ["ollama", "openai", "gemini", "anthropic"])
def test_transport_connection_failure_does_not_leak_key(kind):
    async def scenario():
        provider = create_provider({"type": kind, "base_url": "http://127.0.0.1:1", "api_key": "private-key"})
        await provider.start()
        try:
            with pytest.raises(ProviderError) as exc:
                await provider.probe()
            assert "private-key" not in str(exc.value)
            assert "連線" in str(exc.value)
        finally:
            await provider.close()

    run(scenario())


@pytest.mark.parametrize("token_parameter", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize("send_temperature", [True, False])
def test_compatible_payload_uses_configured_token_parameter_and_temperature(token_parameter, send_temperature):
    async def scenario():
        captured = []

        async def handler(request):
            captured.append(await request.json())
            return web.json_response({"choices": [{"message": {"content": "ok"}}]})

        async with fake_api(handler) as url:
            provider = create_provider({
                "type": "openai", "base_url": url,
                "token_parameter": token_parameter, "send_temperature": send_temperature,
            }, temperature=0.4)
            await provider.start()
            try:
                messages = [{"role": "user", "content": "hello"}]
                assert await provider.chat("reasoner", messages, max_output_tokens=300) == "ok"
                expected = {"model": "reasoner", "messages": messages, "stream": False, token_parameter: 300}
                if send_temperature:
                    expected["temperature"] = 0.4
                assert captured == [expected]
                assert provider.token_parameter == token_parameter
                assert provider.send_temperature is send_temperature
            finally:
                await provider.close()

    run(scenario())


@pytest.mark.parametrize("options", [
    {"token_parameter": "invalid"}, {"token_parameter": None}, {"token_parameter": []},
    {"send_temperature": "false"}, {"send_temperature": 0}, {"send_temperature": None},
])
def test_compatible_options_reject_invalid_values(options):
    with pytest.raises(ProviderError):
        create_provider({"type": "openai", **options})


def test_compatible_modern_limit_is_omitted_when_unspecified():
    async def scenario():
        async def handler(request):
            payload = await request.json()
            assert "max_completion_tokens" not in payload
            assert "max_tokens" not in payload
            assert "temperature" not in payload
            return web.json_response({"choices": [{"message": {"content": "ok"}}]})

        async with fake_api(handler) as url:
            provider = create_provider({"type": "openai", "base_url": url, "token_parameter": "max_completion_tokens", "send_temperature": False})
            await provider.start()
            try:
                assert await provider.chat("reasoner", [{"role": "user", "content": "hello"}]) == "ok"
            finally:
                await provider.close()

    run(scenario())
