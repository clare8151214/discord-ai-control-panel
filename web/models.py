from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator


DEFAULT_CHECKPOINT = "NoobAI-XL-v1.1.safetensors"
DEFAULT_IPADAPTER = "ip_adapter_Noobtest_800000.bin"
DEFAULT_CLIP_VISION = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"


def validate_url(value: str, *, optional: bool = False) -> str:
    value = value.strip().rstrip("/")
    if not value and optional:
        return value
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("API URL 必須是 http(s)，不可包含帳密、query 或 fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("API URL port 無效") from exc
    return value


class ProviderConfig(BaseModel):
    id: str = Field(default="ollama", pattern=r"^[a-z][a-z0-9-]{0,31}$")
    name: str = Field(default="Ollama", min_length=1, max_length=100)
    type: Literal["ollama", "openai", "gemini", "anthropic"] = "ollama"
    base_url: str = Field(default="http://host.docker.internal:11434", max_length=2048)
    api_key: str = Field(default="", max_length=8192, repr=False)
    clear_api_key: bool = False
    models: list[str] = Field(default_factory=list, max_length=100)
    token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    send_temperature: bool = True

    @model_validator(mode="before")
    @classmethod
    def anthropic_defaults(cls, value):
        # Leave optional generation knobs unset for newer Claude models.
        if isinstance(value, dict) and value.get("type") == "anthropic" and "send_temperature" not in value:
            return {**value, "send_temperature": False}
        return value

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return validate_url(value)

    @field_validator("models")
    @classmethod
    def valid_models(cls, value: list[str]) -> list[str]:
        names = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(name) > 200 or any(ord(char) < 32 for char in name) for name in names):
            raise ValueError("模型名稱不可超過 200 個字元")
        return names

    @field_validator("name", "api_key")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("名稱與 API Key 不可包含控制字元")
        return value


class BotProfile(BaseModel):
    id: str = ""
    name: str = Field(default="我的 Discord Bot", min_length=1, max_length=80)
    discord_token: str = Field(default="", max_length=8192, repr=False)
    clear_discord_token: bool = False
    application_id: str = Field(default="", pattern=r"^(\d{15,22})?$")
    guild_id: str = Field(default="", pattern=r"^(\d{15,22})?$")
    message_content_intent: bool = True
    admin_only_settings: bool = True
    providers: list[ProviderConfig] = Field(default_factory=lambda: [ProviderConfig()], min_length=1, max_length=20)
    default_provider: str = "ollama"
    default_model: str = Field(default="", max_length=200)
    system_prompt: str = Field(default="", max_length=20000)
    context_length: int = Field(default=8192, ge=1024, le=262144)
    output_tokens: int = Field(default=768, ge=128, le=16384)
    retention_days: int = Field(default=7, ge=1, le=365)
    compact_trigger: float = Field(default=0.7, ge=0.4, le=0.9)
    image_service_enabled: bool = False
    comfyui_url: str = Field(default="", max_length=2048)
    comfyui_checkpoint: str = Field(default=DEFAULT_CHECKPOINT, min_length=1, max_length=255)
    ipadapter_model: str = Field(default=DEFAULT_IPADAPTER, min_length=1, max_length=255)
    clip_vision_model: str = Field(default=DEFAULT_CLIP_VISION, min_length=1, max_length=255)
    tts_voice: str = Field(default="zh-TW-HsiaoChenNeural", max_length=100)

    @model_validator(mode="before")
    @classmethod
    def legacy_image_service(cls, value):
        # Before the opt-in existed, a nonempty URL enabled the image client.
        # An explicit false always wins and retains the URL for later use.
        if isinstance(value, dict) and "image_service_enabled" not in value:
            return {**value, "image_service_enabled": bool(str(value.get("comfyui_url", "")).strip())}
        return value

    @field_validator("comfyui_checkpoint", "ipadapter_model", "clip_vision_model")
    @classmethod
    def valid_image_model_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("生圖模型檔名不可為空或包含控制字元")
        return value

    @field_validator("comfyui_url")
    @classmethod
    def valid_image_url(cls, value: str) -> str:
        return validate_url(value, optional=True)

    @model_validator(mode="after")
    def valid_profile(self) -> BotProfile:
        ids = [provider.id for provider in self.providers]
        if len(ids) != len(set(ids)):
            raise ValueError("Provider ID 不可重複")
        if self.default_provider not in ids:
            raise ValueError("預設 Provider 必須存在於此 Bot 的設定")
        if self.output_tokens > self.context_length // 2:
            raise ValueError("回答 token 上限不可超過 context 的一半")
        if self.image_service_enabled and not self.comfyui_url:
            raise ValueError("啟用生圖時請填入 ComfyUI API 網址")
        return self


PROFILE_ID = re.compile(r"^[0-9a-f]{32}$")
