from __future__ import annotations

from .base import ModelInfo, ProviderError
from .http import HTTPProvider, output_limit, validate_messages


class OllamaProvider(HTTPProvider):
    model_list_path = "/api/tags"
    provider_id = "ollama"
    display_name = "Ollama（本機）"

    def __init__(
        self,
        base_url: str,
        context_length: int,
        temperature: float,
        keep_alive: str = "-1",
        provider_id: str | None = None,
        display_name: str | None = None,
        api_key: str = "",
        models: list[str] | None = None,
    ) -> None:
        super().__init__(
            provider_id=provider_id or self.provider_id,
            display_name=display_name or self.display_name,
            base_url=base_url,
            api_key=api_key,
            models=models,
            temperature=temperature,
        )
        if isinstance(context_length, bool) or not isinstance(context_length, int) or context_length <= 0:
            raise ProviderError("context_length 必須是正整數")
        self.context_length = context_length
        keep_alive = str(keep_alive)
        self.keep_alive: str | int = (
            int(keep_alive) if keep_alive in {"-1", "0"} else keep_alive
        )

    async def list_models(self) -> list[ModelInfo]:
        configured = self._static_models()
        if configured is not None:
            return configured
        payload = await self._request_json("GET", "/api/tags")
        entries = payload.get("models")
        if not isinstance(entries, list):
            raise ProviderError("Ollama 未回傳有效模型清單")

        models: list[ModelInfo] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model") or ""
            if not isinstance(name, str) or not name:
                continue
            models.append(
                ModelInfo(
                    model_id=name,
                    display_name=name,
                    size_bytes=item["size"] if isinstance(item.get("size"), int) and not isinstance(item["size"], bool) and item["size"] >= 0 else None,
                    metadata=item.get("details") if isinstance(item.get("details"), dict) else {},
                )
            )
        return models

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
        options = {
            "num_ctx": self.context_length,
            "temperature": self.temperature,
        }
        if limit is not None:
            options["num_predict"] = limit
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": options,
        }
        data = await self._request_json("POST", "/api/chat", payload=payload)
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("模型沒有回傳文字內容")
        return content.strip()
