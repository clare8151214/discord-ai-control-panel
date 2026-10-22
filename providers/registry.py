from __future__ import annotations

from dataclasses import dataclass
import json
import os

from .base import ChatProvider, ProviderError
from .ollama import OllamaProvider
from .factory import create_provider


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    provider_id: str
    display_name: str
    provider: ChatProvider | None
    note: str = ""

    @property
    def enabled(self) -> bool:
        return self.provider is not None


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderSpec] = {}

    def register(self, provider: ChatProvider, note: str = "") -> None:
        self._providers[provider.provider_id] = ProviderSpec(
            provider_id=provider.provider_id,
            display_name=provider.display_name,
            provider=provider,
            note=note,
        )

    def reserve(self, provider_id: str, display_name: str, note: str) -> None:
        self._providers[provider_id] = ProviderSpec(
            provider_id=provider_id,
            display_name=display_name,
            provider=None,
            note=note,
        )

    def specs(self) -> list[ProviderSpec]:
        return list(self._providers.values())

    def spec(self, provider_id: str) -> ProviderSpec | None:
        return self._providers.get(provider_id.lower())

    def get(self, provider_id: str) -> ChatProvider:
        spec = self.spec(provider_id)
        if spec is None:
            raise ProviderError(f"未知的 Provider：{provider_id}")
        if spec.provider is None:
            raise ProviderError(f"{spec.display_name} 尚未設定 API 連線")
        return spec.provider

    async def start(self) -> None:
        for spec in self.specs():
            if spec.provider is not None:
                await spec.provider.start()

    async def close(self) -> None:
        for spec in self.specs():
            if spec.provider is not None:
                await spec.provider.close()


def build_provider_registry(
    ollama_url: str,
    ollama_context_length: int,
    ollama_temperature: float,
    ollama_remote_url: str = "",
    ollama_keep_alive: str = "-1",
) -> ProviderRegistry:
    registry = ProviderRegistry()
    configured = os.environ.get("AI_PROVIDERS_JSON")
    if configured is not None:
        try:
            configs = json.loads(configured)
        except (json.JSONDecodeError, TypeError):
            raise ProviderError("AI_PROVIDERS_JSON 必須是有效的 JSON 清單") from None
        if not isinstance(configs, list) or not configs or len(configs) > 100:
            raise ProviderError("AI_PROVIDERS_JSON 必須包含 1 到 100 個 Provider")
        for config in configs:
            provider = create_provider(
                config,
                context_length=ollama_context_length,
                temperature=ollama_temperature,
                keep_alive=ollama_keep_alive,
            )
            if registry.spec(provider.provider_id) is not None:
                raise ProviderError("AI_PROVIDERS_JSON 中的 Provider ID 不可重複")
            registry.register(provider, note="已啟用")
        return registry
    registry.register(
        OllamaProvider(
            base_url=ollama_url,
            context_length=ollama_context_length,
            temperature=ollama_temperature,
            keep_alive=ollama_keep_alive,
        ),
        note="已啟用",
    )
    if ollama_remote_url:
        registry.register(
            OllamaProvider(
                base_url=ollama_remote_url,
                context_length=ollama_context_length,
                temperature=ollama_temperature,
                keep_alive=ollama_keep_alive,
                provider_id="ollama-remote",
                display_name="Ollama（遠端）",
            ),
            note=f"已啟用（{ollama_remote_url}）",
        )
    registry.reserve("gemini", "Google Gemini", "已預留，尚未設定 API")
    registry.reserve("grok", "xAI Grok", "已預留，尚未設定 API")
    return registry
