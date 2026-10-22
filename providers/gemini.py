"""Google Gemini native generateContent API, using header authentication."""

from __future__ import annotations

import re
from typing import Any

from .base import ModelInfo, ProviderError
from .http import HTTPProvider, output_limit, validate_messages


class GeminiProvider(HTTPProvider):
    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key} if self.api_key else {}

    async def list_models(self) -> list[ModelInfo]:
        configured = self._static_models()
        if configured is not None:
            return configured
        models: list[ModelInfo] = []
        page_token = ""
        seen_tokens: set[str] = set()
        for _ in range(100):
            params: dict[str, str | int] = {"pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            data = await self._request_json("GET", "/models", params=params)
            entries = data.get("models")
            if not isinstance(entries, list):
                raise ProviderError("Gemini API 未回傳有效模型清單")
            for item in entries:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                methods = item.get("supportedGenerationMethods", [])
                if not isinstance(methods, list) or "generateContent" not in methods:
                    continue
                model_id = item["name"].removeprefix("models/")
                if model_id:
                    models.append(ModelInfo(
                        model_id=model_id,
                        display_name=item["displayName"] if isinstance(item.get("displayName"), str) and item["displayName"] else model_id,
                        metadata={key: item[key] for key in ("inputTokenLimit", "outputTokenLimit") if key in item},
                    ))
            page_token = data.get("nextPageToken", "")
            if not page_token:
                return models
            if not isinstance(page_token, str) or page_token in seen_tokens:
                raise ProviderError("Gemini API 回傳了無效的分頁資訊")
            seen_tokens.add(page_token)
        raise ProviderError("Gemini API 模型清單超過分頁上限")

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
    ) -> str:
        validate_messages(messages)
        limit = output_limit(max_output_tokens)
        if not isinstance(model, str):
            raise ProviderError("Gemini 模型名稱不正確")
        model = model.removeprefix("models/")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
            raise ProviderError("Gemini 模型名稱不正確")
        system_parts = []
        contents: list[dict[str, Any]] = []
        for message in messages:
            if message["role"] in {"system", "developer"}:
                system_parts.append({"text": message["content"]})
            else:
                role = "model" if message["role"] == "assistant" else "user"
                # Multiple people may send consecutive messages in a room.
                if contents and contents[-1]["role"] == role:
                    contents[-1]["parts"].append({"text": message["content"]})
                else:
                    contents.append({"role": role, "parts": [{"text": message["content"]}]})
        if not contents:
            raise ProviderError("對話至少需要一則使用者或模型訊息")
        generation_config: dict[str, Any] = {"temperature": self.temperature}
        if limit is not None:
            generation_config["maxOutputTokens"] = limit
        payload: dict[str, Any] = {"contents": contents, "generationConfig": generation_config}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        data = await self._request_json("POST", f"/models/{model}:generateContent", payload=payload)
        candidates = data.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            content = candidates[0].get("content")
            parts = content.get("parts") if isinstance(content, dict) else None
            if isinstance(parts, list):
                text = "".join(
                    part["text"] for part in parts
                    if isinstance(part, dict) and not part.get("thought") and isinstance(part.get("text"), str)
                ).strip()
                if text:
                    return text
        raise ProviderError("Gemini 沒有回傳文字內容；請檢查請求、模型限制與額度")
