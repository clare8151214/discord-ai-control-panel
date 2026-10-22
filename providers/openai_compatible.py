"""OpenAI Chat Completions-compatible endpoints (Grok, DeepSeek, etc.)."""

from __future__ import annotations

from typing import Any

from .base import ModelInfo, ProviderError
from .http import HTTPProvider, output_limit, validate_messages


class OpenAICompatibleProvider(HTTPProvider):
    def __init__(
        self,
        *,
        token_parameter: str = "max_tokens",
        send_temperature: bool = True,
        **kwargs: Any,
    ) -> None:
        if token_parameter not in ("max_tokens", "max_completion_tokens"):
            raise ProviderError("token_parameter 只支援 max_tokens 或 max_completion_tokens")
        if not isinstance(send_temperature, bool):
            raise ProviderError("send_temperature 必須是布林值")
        super().__init__(**kwargs)
        self.token_parameter = token_parameter
        self.send_temperature = send_temperature

    async def list_models(self) -> list[ModelInfo]:
        configured = self._static_models()
        if configured is not None:
            return configured
        data = await self._request_json("GET", "/models")
        entries = data.get("data")
        if not isinstance(entries, list):
            raise ProviderError("模型 API 未回傳有效模型清單；可在設定中手動填入模型名稱")
        return [
            ModelInfo(
                model_id=item["id"],
                display_name=item["name"] if isinstance(item.get("name"), str) and item["name"] else item["id"],
                metadata={key: item[key] for key in ("owned_by", "context_window", "max_output_tokens") if key in item},
            )
            for item in entries
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
        ]

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
    ) -> str:
        validate_messages(messages)
        limit = output_limit(max_output_tokens)
        if not isinstance(model, str) or not model.strip():
            raise ProviderError("請先選擇模型")
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if self.send_temperature:
            payload["temperature"] = self.temperature
        if limit is not None:
            payload[self.token_parameter] = limit
        data = await self._request_json("POST", "/chat/completions", payload=payload)
        choices = data.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "".join(
                part["text"] for part in content
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
            )
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("模型沒有回傳文字內容")
        return content.strip()
