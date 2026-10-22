"""Anthropic's native Messages API; keys stay in headers, never in URLs.

Protocol references:
https://platform.claude.com/docs/en/api/messages/create
https://platform.claude.com/docs/en/api/models/list
https://platform.claude.com/docs/en/api/versioning
"""

from __future__ import annotations

from typing import Any

from .base import ModelInfo, ProviderError
from .http import HTTPProvider, output_limit, validate_messages


class AnthropicProvider(HTTPProvider):
    # Pin the supported API contract.  2026-10-21 removes temperature entirely;
    # retaining 2023-06-01 also lets compatible gateways opt in to temperature.
    api_version = "2023-06-01"

    def __init__(self, *, send_temperature: bool = False, **kwargs: Any) -> None:
        if not isinstance(send_temperature, bool):
            raise ProviderError("send_temperature 必須是布林值")
        super().__init__(**kwargs)
        if send_temperature and self.temperature > 1:
            raise ProviderError("Anthropic temperature 必須介於 0 到 1")
        self.send_temperature = send_temperature

    def _headers(self) -> dict[str, str]:
        headers = {"anthropic-version": self.api_version}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    async def list_models(self) -> list[ModelInfo]:
        configured = self._static_models()
        if configured is not None:
            return configured
        models: list[ModelInfo] = []
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        cursor = ""
        for _ in range(100):
            params: dict[str, str | int] = {"limit": 1000}
            if cursor:
                params["after_id"] = cursor
            data = await self._request_json("GET", "/models", params=params)
            entries = data.get("data")
            if not isinstance(entries, list):
                raise ProviderError("Anthropic API 未回傳有效模型清單")
            for item in entries:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                    continue
                model_id = item["id"]
                if model_id in seen_ids:
                    continue
                seen_ids.add(model_id)
                name = item.get("display_name")
                models.append(ModelInfo(
                    model_id=model_id,
                    display_name=name if isinstance(name, str) and name else model_id,
                    metadata={key: item[key] for key in ("created_at", "max_input_tokens", "max_tokens", "capabilities", "lifecycle", "line") if key in item},
                ))
            has_more = data.get("has_more", False)
            if not isinstance(has_more, bool):
                raise ProviderError("Anthropic API 回傳了無效的分頁資訊")
            if not has_more:
                return models
            cursor = data.get("last_id")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise ProviderError("Anthropic API 回傳了無效的分頁資訊")
            seen_cursors.add(cursor)
        raise ProviderError("Anthropic API 模型清單超過分頁上限")

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
    ) -> str:
        validate_messages(messages)
        limit = output_limit(max_output_tokens)
        if not isinstance(model, str) or not model.strip() or len(model) > 256 or any(ord(char) < 32 for char in model):
            raise ProviderError("Anthropic 模型名稱不正確")
        system: list[dict[str, str]] = []
        turns: list[dict[str, Any]] = []
        for message in messages:
            block = {"type": "text", "text": message["content"]}
            role = message["role"]
            if role in {"system", "developer"}:
                system.append(block)
            elif turns and turns[-1]["role"] == role:
                # Keep all speaker-labelled texts when several room members
                # send messages consecutively; Anthropic combines these turns.
                turns[-1]["content"].append(block)
            else:
                turns.append({"role": role, "content": [block]})
        if not turns:
            raise ProviderError("對話至少需要一則使用者或模型訊息")
        payload: dict[str, Any] = {
            "model": model.strip(), "messages": turns,
            "max_tokens": limit if limit is not None else 1024,
            "stream": False,
        }
        if system:
            payload["system"] = system
        if self.send_temperature:
            payload["temperature"] = self.temperature
        data = await self._request_json("POST", "/messages", payload=payload)
        blocks = data.get("content")
        if isinstance(blocks, list):
            text = "".join(
                block["text"] for block in blocks
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
            ).strip()
            if text:
                return text
        raise ProviderError("Anthropic 沒有回傳文字內容；請檢查模型限制與額度")
