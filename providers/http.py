"""Shared HTTP transport: credentials stay in headers and errors stay local."""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .base import ChatProvider, ModelInfo, ProviderError


def validate_base_url(value: str) -> str:
    """Allow private/LAN endpoints, but never URL-embedded credentials."""
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        raise ProviderError("API URL 必須是有效的 http:// 或 https:// 網址")
    try:
        url = urlsplit(value)
        if (
            url.scheme.lower() not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or "?" in value
            or "#" in value
            or "\\" in value
        ):
            raise ValueError
        _ = url.port
    except (ValueError, TypeError):
        raise ProviderError("API URL 只接受 http(s)，不可包含帳密、query 或 fragment") from None
    return urlunsplit((url.scheme.lower(), url.netloc, url.path.rstrip("/"), "", ""))


def validate_messages(messages: list[dict[str, str]]) -> None:
    if not isinstance(messages, list) or not messages:
        raise ProviderError("對話訊息不可為空")
    for message in messages:
        if (
            not isinstance(message, dict)
            or message.get("role") not in {"system", "developer", "user", "assistant"}
            or not isinstance(message.get("content"), str)
        ):
            raise ProviderError("對話訊息必須包含有效 role 與文字 content")


def output_limit(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProviderError("max_output_tokens 必須是正整數")
    return value


class HTTPProvider(ChatProvider):
    """Reusable non-streaming JSON transport without reflecting remote errors."""

    model_list_path = "/models"

    def __init__(
        self,
        *,
        provider_id: str,
        display_name: str,
        base_url: str,
        api_key: str = "",
        models: list[str] | None = None,
        temperature: float = 0.7,
    ) -> None:
        self.provider_id = provider_id
        self.display_name = display_name
        self.base_url = validate_base_url(base_url)
        if not isinstance(api_key, str) or any(ord(c) < 32 or ord(c) == 127 for c in api_key):
            raise ProviderError("API Key 格式不正確")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
            or not 0 <= temperature <= 2
        ):
            raise ProviderError("temperature 必須介於 0 到 2")
        self.api_key = api_key
        self.configured_models = list(models or [])
        self.temperature = temperature
        self.session: aiohttp.ClientSession | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=600, connect=10),
                headers=self._headers(),
            )

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            raise ProviderError("Provider HTTP client 尚未啟動")
        return self.session

    def _static_models(self) -> list[ModelInfo] | None:
        if not self.configured_models:
            return None
        return [ModelInfo(model_id=name, display_name=name) for name in self.configured_models]

    async def probe(self) -> None:
        """Make a real read-only request, even when model IDs are configured.

        Some accounts disallow listing models. Such accounts can still use
        configured models for chat, but this connectivity probe reports that
        restriction instead of claiming the endpoint was verified.
        """
        await self._request_json("GET", self.model_list_path)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._session().request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                params=params,
                # Redirects could send x-goog-api-key to another host.
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    hints = {
                        401: "，請檢查 API Key",
                        403: "，請檢查 API Key 與存取權限",
                        404: "，請檢查 API URL 與模型名稱",
                        429: "，請稍後重試或檢查額度",
                    }
                    # Never echo upstream HTML/errors: they can reflect keys,
                    # conversation content, or untrusted executable markup.
                    raise ProviderError(
                        f"模型 API 回傳 HTTP {response.status}{hints.get(response.status, '')}"
                    )
                chunks = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    chunks.extend(chunk)
                    if len(chunks) > 8 * 1024 * 1024:
                        raise ProviderError("模型 API 回應過大")
                data = json.loads(chunks)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise ProviderError("模型 API 連線失敗或逾時，請檢查網址與網路") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ProviderError("模型 API 回傳了無效的 JSON") from None
        if not isinstance(data, dict):
            raise ProviderError("模型 API 回應格式不正確")
        if data.get("error"):
            raise ProviderError("模型 API 回報錯誤，請檢查模型與服務設定")
        return data
