"""Read-only checks for the exact ComfyUI workflow used by this Bot.

This never submits a prompt, uploads a file, downloads a model, or installs nodes.
Remote response bodies and configured URLs are not reflected into error strings.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

from providers.base import ProviderError
from providers.http import validate_base_url


RESPONSE_LIMIT = 8 * 1024 * 1024
SETUP_TIMEOUT = 25
REQUEST_TIMEOUT = 10
REQUIRED_NODES = (
    "KSampler", "CheckpointLoaderSimple", "EmptyLatentImage", "CLIPTextEncode",
    "VAEDecode", "PreviewImage", "LoadImage", "CLIPVisionLoader",
    "IPAdapterModelLoader", "IPAdapterAdvanced",
)
WORKFLOW_INPUTS = {
    "KSampler": {"model", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "positive", "negative", "latent_image"},
    "CheckpointLoaderSimple": {"ckpt_name"},
    "EmptyLatentImage": {"width", "height", "batch_size"},
    "CLIPTextEncode": {"text", "clip"},
    "VAEDecode": {"samples", "vae"},
    "PreviewImage": {"images"},
    "LoadImage": {"image"},
    "CLIPVisionLoader": {"clip_name"},
    "IPAdapterModelLoader": {"ipadapter_file"},
    "IPAdapterAdvanced": {"model", "ipadapter", "image", "weight", "weight_type", "combine_embeds", "start_at", "end_at", "embeds_scaling", "clip_vision"},
}
MODEL_INPUTS = (
    ("checkpoint", "SDXL／NoobAI checkpoint", "CheckpointLoaderSimple", "ckpt_name"),
    ("ipadapter_model", "NoobAI IP-Adapter 權重", "IPAdapterModelLoader", "ipadapter_file"),
    ("clip_vision_model", "CLIP Vision 權重", "CLIPVisionLoader", "clip_name"),
)


def _check(key: str, label: str, error: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "ok": not error, "error": error}


def _result(checks: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [f"{item['label']}：{item['error']}" for item in checks if not item["ok"]]
    return {"ok": not errors, "checks": checks, "errors": errors}


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> tuple[dict[str, Any] | None, str]:
    try:
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                # Do not read/reflect bodies or Location: either may contain keys.
                return None, f"ComfyUI 回傳 HTTP {response.status}，請檢查服務與存取權限"
            if response.content_length is not None and response.content_length > RESPONSE_LIMIT:
                return None, "ComfyUI 回應超過 8 MiB 上限"
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > RESPONSE_LIMIT:
                    return None, "ComfyUI 回應超過 8 MiB 上限"
            value = json.loads(body)
            if not isinstance(value, dict):
                return None, "ComfyUI 回應格式不正確"
            return value, ""
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return None, "無法連線至 ComfyUI 或請求逾時，請檢查 IP、埠與防火牆"
    except (ValueError, UnicodeDecodeError):
        return None, "ComfyUI 未回傳有效 JSON"


async def check_image_setup(
    base_url: str,
    checkpoint: str,
    ipadapter_model: str,
    clip_vision_model: str,
) -> dict[str, Any]:
    """Validate service, ten nodes and the exact three configured model names.

    Returns ``{ok, checks: [{key, label, ok, error}], errors: [str]}``. Node
    keys are ``node:<class name>``; model keys match the GUI configuration.
    An unrecognised model-choice schema fails explicitly instead of guessing.
    """
    try:
        endpoint = validate_base_url(base_url)
    except ProviderError:
        return _result([_check("service", "ComfyUI 服務", "請填入有效的 http(s) URL，不可包含帳密、query 或 fragment")])
    paths = ["/system_stats", *(f"/object_info/{node}" for node in REQUIRED_NODES)]
    try:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT, connect=3)
        async with asyncio.timeout(SETUP_TIMEOUT):
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                replies = await asyncio.gather(*(_fetch_json(session, endpoint + path) for path in paths))
    except asyncio.TimeoutError:
        return _result([_check("service", "ComfyUI 服務", "安裝檢查超過 25 秒，請確認服務狀態後重試")])

    health, health_error = replies[0]
    if not health_error and (not isinstance(health.get("system"), dict) or not isinstance(health.get("devices"), list)):
        health_error = "服務未回傳有效的 ComfyUI system_stats，請確認 API 位址"
    checks = [_check("service", "ComfyUI 服務", health_error)]
    node_inputs: dict[str, dict[str, Any]] = {}
    for node, (payload, error) in zip(REQUIRED_NODES, replies[1:]):
        if not error:
            definition = payload.get(node)
            inputs = definition.get("input") if isinstance(definition, dict) else None
            required = inputs.get("required") if isinstance(inputs, dict) else None
            optional = inputs.get("optional", {}) if isinstance(inputs, dict) else None
            if not isinstance(required, dict) or not isinstance(optional, dict):
                error = "找不到必要節點或節點 input.required 格式不正確"
            elif not WORKFLOW_INPUTS[node].issubset(required.keys() | optional.keys()):
                error = "節點缺少此生圖流程需要的輸入欄位，請檢查節點版本"
            elif any(not isinstance(spec, list) or not spec or not isinstance(spec[0], (str, list)) for name, spec in (required | optional).items() if name in WORKFLOW_INPUTS[node]):
                error = "節點輸入欄位格式不正確，無法確認此生圖流程是否相容"
            else:
                node_inputs[node] = required
        checks.append(_check(f"node:{node}", f"節點 {node}", error))

    filenames = {"checkpoint": checkpoint, "ipadapter_model": ipadapter_model, "clip_vision_model": clip_vision_model}
    for key, label, node, input_name in MODEL_INPUTS:
        filename = filenames[key]
        error = ""
        if not isinstance(filename, str) or not filename or len(filename) > 1024 or any(ord(char) < 32 for char in filename):
            error = "請填入有效的完整模型檔名"
        elif node not in node_inputs:
            error = "無法讀取對應節點的模型清單，請先修復節點或連線"
        else:
            spec = node_inputs[node].get(input_name)
            choices = spec[0] if isinstance(spec, list) and spec else None
            if not isinstance(choices, list) or any(not isinstance(item, str) for item in choices):
                error = "節點未提供可驗證的模型選單，無法確認是否安裝"
            elif filename not in choices:
                error = "找不到指定模型；檔名須與 ComfyUI 選單完全一致，安裝後請重新整理服務"
        checks.append(_check(key, label, error))
    return _result(checks)
