from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    """所有模型供應商對 Bot 暴露的統一錯誤。"""


@dataclass(frozen=True, slots=True)
class ModelInfo:
    model_id: str
    display_name: str
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ChatProvider(ABC):
    """Gemini、Grok、Ollama 等聊天後端需實作的最小介面。"""

    provider_id: str
    display_name: str

    async def start(self) -> None:
        """建立 HTTP client 或其他執行資源。"""

    async def close(self) -> None:
        """釋放 Provider 資源。"""

    async def probe(self) -> None:
        """Verify connectivity; HTTP providers override to bypass static lists."""
        await self.list_models()

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        raise NotImplementedError

    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
    ) -> str:
        raise NotImplementedError
