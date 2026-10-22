"""The setup checker only talks to local fake ComfyUI HTTP endpoints."""

import asyncio
from contextlib import asynccontextmanager
import json
import socket

from aiohttp import web
import pytest

from web.image_setup import REQUIRED_NODES, check_image_setup


CHECKPOINT = "NoobAI-XL-v1.1.safetensors"
IPADAPTER = "ip_adapter_Noobtest_800000.bin"
CLIP = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
PARAMS = (CHECKPOINT, IPADAPTER, CLIP)


def node_payload(node):
    fields = {
        "CheckpointLoaderSimple": {"ckpt_name": [[CHECKPOINT, "another-model.safetensors"], {"tooltip": "Checkpoints"}]},
        "IPAdapterModelLoader": {"ipadapter_file": [[IPADAPTER]]},
        "CLIPVisionLoader": {"clip_name": [[CLIP]]},
    }
    # An independent fake ComfyUI schema: do not derive required inputs from
    # the checker's own compatibility table or an incorrect table could pass.
    inputs_for_node = {
        "KSampler": "model seed steps cfg sampler_name scheduler denoise positive negative latent_image",
        "CheckpointLoaderSimple": "ckpt_name",
        "EmptyLatentImage": "width height batch_size",
        "CLIPTextEncode": "text clip",
        "VAEDecode": "samples vae",
        "PreviewImage": "images",
        "LoadImage": "image",
        "CLIPVisionLoader": "clip_name",
        "IPAdapterModelLoader": "ipadapter_file",
        "IPAdapterAdvanced": "model ipadapter image weight weight_type combine_embeds start_at end_at embeds_scaling clip_vision",
    }
    required = {key: ["MODEL"] for key in inputs_for_node[node].split()}
    required.update(fields.get(node, {}))
    inputs = {"required": required}
    if node == "IPAdapterAdvanced":
        inputs["optional"] = {"clip_vision": required.pop("clip_vision")}
    return {node: {"input": inputs}}


def payload_for(path):
    if path == "/system_stats":
        return {"system": {"os": "test"}, "devices": []}
    return node_payload(path.rsplit("/", 1)[-1])


@asynccontextmanager
async def fake_comfy(handler):
    app = web.Application(client_max_size=10 * 1024 * 1024)
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


def by_key(result):
    return {check["key"]: check for check in result["checks"]}


def test_complete_installation_checks_exact_models_and_get_only_concurrently():
    async def scenario():
        active = 0
        peak = 0
        requests = []

        async def handler(request):
            nonlocal active, peak
            assert request.method == "GET"
            assert not request.query
            active += 1
            peak = max(peak, active)
            requests.append(request.path)
            await asyncio.sleep(0.01)
            active -= 1
            return web.json_response(payload_for(request.path))

        async with fake_comfy(handler) as url:
            result = await check_image_setup(url + "/", *PARAMS)
        assert result["ok"] and result["errors"] == []
        assert len(result["checks"]) == 14
        assert all(item["ok"] and item["error"] == "" for item in result["checks"])
        assert set(requests) == {"/system_stats", *(f"/object_info/{node}" for node in REQUIRED_NODES)}
        assert peak > 1

    asyncio.run(scenario())


@pytest.mark.parametrize("field,index", [("checkpoint", 0), ("ipadapter_model", 1), ("clip_vision_model", 2)])
def test_missing_or_similarly_named_model_is_not_accepted(field, index):
    async def scenario():
        async def handler(request):
            return web.json_response(payload_for(request.path))

        names = list(PARAMS)
        names[index] = "prefix-" + names[index]
        async with fake_comfy(handler) as url:
            result = await check_image_setup(url, *names)
        assert not result["ok"]
        assert not by_key(result)[field]["ok"]
        assert by_key(result)["service"]["ok"]
        assert sum(not item["ok"] for item in result["checks"]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", ["KSampler", "IPAdapterAdvanced", "IPAdapterModelLoader", "CLIPVisionLoader"])
def test_missing_workflow_or_reference_node_fails(missing):
    async def scenario():
        async def handler(request):
            if request.path.endswith("/" + missing):
                return web.json_response({})
            return web.json_response(payload_for(request.path))

        async with fake_comfy(handler) as url:
            result = await check_image_setup(url, *PARAMS)
        assert not result["ok"]
        assert not by_key(result)[f"node:{missing}"]["ok"]

    asyncio.run(scenario())


@pytest.mark.parametrize("spec", [None, "MODEL", ["STRING"], [[1, 2]], {"choices": [CHECKPOINT]}])
def test_unknown_model_choice_schema_fails_explicitly(spec):
    async def scenario():
        async def handler(request):
            payload = payload_for(request.path)
            if request.path.endswith("/CheckpointLoaderSimple"):
                payload["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = spec
            return web.json_response(payload)

        async with fake_comfy(handler) as url:
            result = await check_image_setup(url, *PARAMS)
        assert not result["ok"]
        assert not by_key(result)["checkpoint"]["ok"]
        assert "模型" in by_key(result)["checkpoint"]["error"]

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 403, 302, 500])
def test_http_errors_are_local_and_redirects_never_followed(status):
    async def scenario():
        requests = []

        async def handler(request):
            requests.append(request.path)
            return web.Response(status=status, text="private-key private-file private-prompt", headers={"Location": "/secret-leak"})

        async with fake_comfy(handler) as url:
            result = await check_image_setup(url, *PARAMS)
        assert not result["ok"] and str(status) in by_key(result)["service"]["error"]
        assert "secret-leak" not in " ".join(requests)
        assert "private-" not in json.dumps(result)
        assert len(requests) == 11

    asyncio.run(scenario())


@pytest.mark.parametrize("url", ["http://user:private-key@example.com", "http://example.com?key=private-key", "file:///private-key", "http://example.com/#private-key"])
def test_unsafe_url_rejected_without_reflection(url):
    result = asyncio.run(check_image_setup(url, *PARAMS))
    assert not result["ok"]
    assert "private-key" not in json.dumps(result)


def test_offline_endpoint_reports_failure_without_throwing():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    result = asyncio.run(check_image_setup(f"http://127.0.0.1:{port}", *PARAMS))
    assert not result["ok"]
    assert not by_key(result)["service"]["ok"]
    assert "連線" in by_key(result)["service"]["error"]


@pytest.mark.parametrize("body", ["not-json-private-value", "[]", "{}"])
def test_invalid_json_or_health_schema_fails_safely(body):
    async def scenario():
        async def handler(request):
            if request.path == "/system_stats":
                return web.Response(text=body)
            return web.json_response(payload_for(request.path))

        async with fake_comfy(handler) as url:
            result = await check_image_setup(url, *PARAMS)
        assert not result["ok"]
        assert not by_key(result)["service"]["ok"]
        assert "private-value" not in json.dumps(result)

    asyncio.run(scenario())


def test_response_size_cap_and_total_timeout(monkeypatch):
    async def scenario():
        async def oversized(request):
            return web.Response(body=b"x" * 500)

        monkeypatch.setattr("web.image_setup.RESPONSE_LIMIT", 100)
        async with fake_comfy(oversized) as url:
            result = await check_image_setup(url, *PARAMS)
        assert not result["ok"] and "上限" in by_key(result)["service"]["error"]

        async def slow(request):
            await asyncio.sleep(0.1)
            return web.json_response(payload_for(request.path))

        monkeypatch.setattr("web.image_setup.SETUP_TIMEOUT", 0.01)
        async with fake_comfy(slow) as url:
            result = await check_image_setup(url, *PARAMS)
        assert not result["ok"] and "25 秒" in by_key(result)["service"]["error"]

    asyncio.run(scenario())


def test_chunked_response_without_content_length_is_still_bounded(monkeypatch):
    async def scenario():
        async def chunked(request):
            response = web.StreamResponse(status=200)
            response.enable_chunked_encoding()
            await response.prepare(request)
            await response.write(b"x" * 101)
            await response.write_eof()
            return response

        monkeypatch.setattr("web.image_setup.RESPONSE_LIMIT", 100)
        async with fake_comfy(chunked) as url:
            result = await check_image_setup(url, *PARAMS)
        assert not result["ok"]
        assert "上限" in by_key(result)["service"]["error"]

    asyncio.run(scenario())


@pytest.mark.parametrize("inputs", [{"required": {}}, {"required": None}, {"required": {}, "optional": []}])
def test_existing_node_with_unknown_or_incompatible_inputs_fails(inputs):
    async def scenario():
        async def handler(request):
            if request.path.endswith("/KSampler"):
                return web.json_response({"KSampler": {"input": inputs}})
            return web.json_response(payload_for(request.path))

        async with fake_comfy(handler) as url:
            result = await check_image_setup(url, *PARAMS)
        assert not result["ok"]
        assert not by_key(result)["node:KSampler"]["ok"]

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["", None, "bad\nfilename"])
def test_invalid_filenames_are_configuration_errors(name):
    async def scenario():
        async def handler(request):
            return web.json_response(payload_for(request.path))

        async with fake_comfy(handler) as url:
            result = await check_image_setup(url, name, IPADAPTER, CLIP)
        assert not result["ok"]
        assert "檔名" in by_key(result)["checkpoint"]["error"]

    asyncio.run(scenario())
