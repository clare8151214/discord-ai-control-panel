from __future__ import annotations

import re
from typing import Any

from .base import ChatProvider, ProviderError
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider


def create_provider(
    config: dict[str, Any],
    context_length: int = 8192,
    temperature: float = 0.7,
    keep_alive: str = "-1",
) -> ChatProvider:
    """Build one provider from the same schema used by GUI and deployment config.

    base_url is the API root (e.g. /v1 for compatible, /v1beta for Gemini).
    models is optional: a nonempty list bypasses model discovery requests.
    """
    if not isinstance(config, dict):
        raise ProviderError("Provider 設定必須是 JSON object")
    provider_type = config.get("type", "ollama")
    if not isinstance(provider_type, str) or provider_type not in {"ollama", "openai", "gemini", "anthropic"}:
        raise ProviderError("Provider type 只支援 ollama、openai、gemini 或 anthropic")
    provider_id = config.get("id", provider_type)
    if not isinstance(provider_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider_id):
        raise ProviderError("Provider ID 需以小寫英文字開頭，只能包含小寫英文、數字、底線與連字號")
    display_name = config.get("name", provider_id)
    if display_name == "":
        display_name = provider_id
    if not isinstance(display_name, str) or len(display_name) > 100 or any(ord(c) < 32 for c in display_name):
        raise ProviderError("Provider 名稱必須是 100 字元內的文字")
    models = config.get("models", [])
    if not isinstance(models, list) or len(models) > 1000:
        raise ProviderError("Provider models 必須是模型名稱清單（最多 1000 筆）")
    if any(not isinstance(m, str) or not m.strip() or len(m) > 256 or any(ord(c) < 32 for c in m) for m in models):
        raise ProviderError("模型名稱必須是 1 到 256 字元的文字")
    models = list(dict.fromkeys(m.strip() for m in models))
    default_url = {
        "ollama": "http://127.0.0.1:11434",
        "openai": "https://api.openai.com/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "anthropic": "https://api.anthropic.com/v1",
    }[provider_type]
    kwargs = {
        "provider_id": provider_id,
        "display_name": display_name,
        "base_url": config.get("base_url") or default_url,
        "api_key": config.get("api_key", ""),
        "models": models,
        "temperature": temperature,
    }
    if provider_type == "ollama":
        return OllamaProvider(context_length=context_length, keep_alive=keep_alive, **kwargs)
    if provider_type == "openai":
        return OpenAICompatibleProvider(
            token_parameter=config.get("token_parameter", "max_tokens"),
            send_temperature=config.get("send_temperature", True),
            **kwargs,
        )
    if provider_type == "anthropic":
        return AnthropicProvider(send_temperature=config.get("send_temperature", False), **kwargs)
    return GeminiProvider(**kwargs)
