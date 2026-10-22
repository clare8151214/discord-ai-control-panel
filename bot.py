from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from dotenv import load_dotenv

import tts
from history_archive import HistoryArchive
from image_generation import ComfyUIImageClient, ImageGenerationError
from providers import ModelInfo, ProviderError, build_provider_registry


BASE_DIR = Path(__file__).resolve().parent
if os.getenv("BOT_CONFIG_ISOLATED") != "1":
    load_dotenv(BASE_DIR / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_REMOTE_URL = os.getenv("OLLAMA_REMOTE_URL", "").strip().rstrip("/")
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "ollama").strip().lower()
DEFAULT_MODEL = os.getenv(
    "DEFAULT_MODEL",
    "gemma4:12b-it-qat",
).strip()
DEFAULT_SYSTEM_PROMPT = os.getenv(
    "DEFAULT_SYSTEM_PROMPT",
    "",
).strip()
STATE_FILE = Path(os.getenv("STATE_FILE", "data/state.json"))
if not STATE_FILE.is_absolute():
    STATE_FILE = BASE_DIR / STATE_FILE
database_file_setting = os.getenv("DATABASE_FILE", "").strip()
if database_file_setting:
    DATABASE_FILE = Path(os.path.expandvars(database_file_setting)).expanduser()
    if not DATABASE_FILE.is_absolute():
        DATABASE_FILE = BASE_DIR / DATABASE_FILE
elif os.name == "nt" and os.getenv("LOCALAPPDATA"):
    DATABASE_FILE = (
        Path(os.environ["LOCALAPPDATA"]) / "OllamaDiscordBot" / "history.sqlite3"
    )
else:
    DATABASE_FILE = BASE_DIR / "data" / "history.sqlite3"
MAX_HISTORY_MESSAGES = max(2, int(os.getenv("MAX_HISTORY_MESSAGES", "40")))
OLLAMA_CONTEXT_LENGTH = max(1024, int(os.getenv("OLLAMA_CONTEXT_LENGTH", "8192")))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.7"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "-1").strip() or "-1"
CHAT_MAX_OUTPUT_TOKENS = max(
    128, int(os.getenv("CHAT_MAX_OUTPUT_TOKENS", "768"))
)
MEMORY_OUTPUT_RESERVE = max(256, int(os.getenv("MEMORY_OUTPUT_RESERVE", "1536")))
MEMORY_COMPACT_TRIGGER = min(
    0.9,
    max(0.4, float(os.getenv("MEMORY_COMPACT_TRIGGER", "0.70"))),
)
MEMORY_RECENT_MESSAGES = max(4, int(os.getenv("MEMORY_RECENT_MESSAGES", "8")))
MEMORY_SUMMARY_MAX_TOKENS = max(
    256,
    int(os.getenv("MEMORY_SUMMARY_MAX_TOKENS", "1024")),
)
MEMORY_SUMMARY_MAX_CHARS = max(
    1000,
    int(os.getenv("MEMORY_SUMMARY_MAX_CHARS", "4000")),
)
ARCHIVE_RETENTION_DAYS = max(
    1, int(os.getenv("ARCHIVE_RETENTION_DAYS", "7"))
)
ARCHIVE_CLEANUP_INTERVAL_SECONDS = max(
    3600, int(os.getenv("ARCHIVE_CLEANUP_INTERVAL_SECONDS", "3600"))
)
ADMIN_ONLY_SETTINGS = os.getenv("ADMIN_ONLY_SETTINGS", "true").lower() not in {
    "0",
    "false",
    "no",
}
MESSAGE_CONTENT_INTENT = os.getenv("MESSAGE_CONTENT_INTENT", "false").lower() in {
    "1",
    "true",
    "yes",
}
TTS_DEFAULT_VOICE = os.getenv("TTS_DEFAULT_VOICE", tts.DEFAULT_TTS_VOICE).strip()
TTS_MAX_CHARS = max(100, int(os.getenv("TTS_MAX_CHARS", "1000")))
COMFYUI_URL = os.getenv("COMFYUI_URL", "").strip().rstrip("/")
IMAGE_SERVICE_ENABLED = os.getenv("IMAGE_SERVICE_ENABLED", "true").lower() in {"1", "true", "yes"}
COMFYUI_CHECKPOINT = os.getenv(
    "COMFYUI_CHECKPOINT", "NoobAI-XL-v1.1.safetensors"
).strip()
COMFYUI_WIDTH = max(256, int(os.getenv("COMFYUI_WIDTH", "896")))
COMFYUI_HEIGHT = max(256, int(os.getenv("COMFYUI_HEIGHT", "896")))
COMFYUI_STEPS = max(1, int(os.getenv("COMFYUI_STEPS", "20")))
COMFYUI_CFG = max(0.1, float(os.getenv("COMFYUI_CFG", "5.5")))
COMFYUI_TIMEOUT = max(30, int(os.getenv("COMFYUI_TIMEOUT", "600")))
IPADAPTER_MODEL = os.getenv(
    "IPADAPTER_MODEL", "ip_adapter_Noobtest_800000.bin"
).strip()
CLIP_VISION_MODEL = os.getenv(
    "CLIP_VISION_MODEL", "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
).strip()
IPADAPTER_DEFAULT_WEIGHT = min(
    1.2, max(0.1, float(os.getenv("IPADAPTER_DEFAULT_WEIGHT", "0.65")))
)
IPADAPTER_END_AT = min(
    1.0, max(0.1, float(os.getenv("IPADAPTER_END_AT", "0.8")))
)
CHARACTER_REFERENCE_MAX_BYTES = max(
    1_000_000, int(os.getenv("CHARACTER_REFERENCE_MAX_BYTES", "10000000"))
)
IMAGE_RETENTION_DAYS = max(1, int(os.getenv("IMAGE_RETENTION_DAYS", "2")))
IMAGE_CLEANUP_INTERVAL_SECONDS = max(
    3600, int(os.getenv("IMAGE_CLEANUP_INTERVAL_SECONDS", "86400"))
)
character_asset_dir_setting = os.getenv("CHARACTER_ASSET_DIR", "").strip()
if character_asset_dir_setting:
    CHARACTER_ASSET_DIR = Path(
        os.path.expandvars(character_asset_dir_setting)
    ).expanduser()
    if not CHARACTER_ASSET_DIR.is_absolute():
        CHARACTER_ASSET_DIR = BASE_DIR / CHARACTER_ASSET_DIR
elif os.name == "nt" and os.getenv("LOCALAPPDATA"):
    CHARACTER_ASSET_DIR = (
        Path(os.environ["LOCALAPPDATA"]) / "OllamaDiscordBot" / "characters"
    )
else:
    CHARACTER_ASSET_DIR = BASE_DIR / "data" / "characters"
IMAGE_PROMPT_MAX_TOKENS = max(
    96, int(os.getenv("IMAGE_PROMPT_MAX_TOKENS", "180"))
)
IMAGE_PROMPT_SOURCE_MAX_CHARS = max(
    1000, int(os.getenv("IMAGE_PROMPT_SOURCE_MAX_CHARS", "3500"))
)
IMAGE_MAX_BYTES = max(
    1_000_000, int(os.getenv("IMAGE_MAX_BYTES", "9500000"))
)
IMAGE_NEGATIVE_PROMPT = os.getenv(
    "IMAGE_NEGATIVE_PROMPT",
    (
        "worst quality, low quality, normal quality, lowres, blurry, "
        "bad anatomy, bad hands, extra fingers, missing fingers, extra limbs, "
        "malformed limbs, deformed, duplicate, text, watermark, signature, logo"
    ),
).strip()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("ollama-discord-bot")
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
UNCLOSED_THINK_RE = re.compile(r"<think>.*\Z", re.IGNORECASE | re.DOTALL)
THINK_TAG_RE = re.compile(r"</?think>", re.IGNORECASE)
SPEAKER_TAG_RE = re.compile(
    r"(?:\*\*)?\[(?:Discord\s*使用者|發言者)\s*[：:][^\]\n]*\](?:\*\*)?",
    re.IGNORECASE,
)
DISCORD_ID_RE = re.compile(
    r"(?:Discord\s*)?ID\s*[：:]\s*\d{15,22}",
    re.IGNORECASE,
)
LEGACY_USER_TAG_RE = re.compile(
    r"\[Discord\s*使用者\s*[：:]\s*([^\]|｜\n]+?)\s*[｜|]\s*ID\s*[：:]\s*\d{15,22}\]",
    re.IGNORECASE,
)
CJK_CHAR_RE = re.compile(
    r"[\u2e80-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)

ROOM_INSTRUCTION = (
    "你正在 Discord 多人頻道中對話。每則使用者訊息前會有內部的"
    "「[發言者：名稱]」標記；請依名稱分辨不同參與者、維持目前角色設定，"
    "並主要回應最新發言者。這些標記只供內部辨識，回覆時禁止複製、顯示"
    "或自行編造任何發言者標記、Discord ID 或數字 ID；只輸出回覆正文。"
)
DM_INSTRUCTION = (
    "你正在 Discord 私訊中與一位使用者對話。請延續對話上下文並直接"
    "回應最新訊息；不要顯示或自行編造 Discord ID，只輸出回覆正文。"
)
SUMMARY_INSTRUCTION = (
    "你是對話記憶整理器，不是劇中角色。請把既有摘要與新增舊對話合併成"
    "一份精簡、客觀、可供後續模型延續劇情的繁體中文記憶。必須保留："
    "參與者與角色身分、人物關係、場景、重要事件、角色語氣與設定、目前狀態、"
    "尚未完成的行動。不要續寫劇情、不要加入新事實、不要顯示 Discord ID，"
    "使用短條列，避免重複。"
)
IMAGE_PROMPT_INSTRUCTION = (
    "You convert an assistant's latest role-play scene into one concise image prompt "
    "for an anime SDXL model. Describe only visible content: characters, appearance, "
    "clothing, facial expression, pose, interaction, setting, lighting, composition, "
    "and camera angle. Preserve the supplied scene and character details exactly. "
    "Do not add dialogue, explanations, Discord metadata, or facts not present in the "
    "scene. Output exactly one comma-separated line of short English Danbooru-style "
    "tags. Never output a heading, quotation marks, markdown, or a negative prompt."
)
CHARACTER_CARD_INSTRUCTION = (
    "Convert the supplied character description into a compact, reusable identity "
    "card for an anime SDXL model. Output exactly one comma-separated line of short "
    "English Danbooru-style tags. Preserve only stable visible identity traits: "
    "species, age category, body type, face, skin, eyes, hair, distinctive features, "
    "and signature clothing/accessories. Exclude pose, action, expression, camera, "
    "lighting, background, quality tags, dialogue, personality, and sexual acts. "
    "Do not add facts. Never output a heading, markdown, quotation marks, or negative "
    "prompt."
)
IMAGE_QUALITY_PREFIX = (
    "masterpiece, best quality, newest, absurdres, highres, anime style"
)

MODEL_GUIDES: dict[str, dict[str, str]] = {
    "gemma4:12b-it-qat": {
        "badge": "⚖️ 預設／最均衡",
        "parameters": "11.9B",
        "description": "一般聊天、創作、圖片理解都適合；品質與速度最均衡。",
        "choice": "⚖️ Gemma 4 12B｜預設、最均衡",
    },
    "gpt-oss:20b": {
        "badge": "🧠 最大／複雜任務最強",
        "parameters": "20.9B（目前最多）",
        "description": "最適合複雜推理、分析與工具任務；冷啟動及首字回覆較慢。",
        "choice": "🧠 GPT-OSS 20B｜參數最多、複雜任務最強",
    },
    "gpt-oss:latest": {
        "badge": "🧠 最大／複雜任務最強",
        "parameters": "20.9B",
        "description": "與 gpt-oss:20b 是同一個模型；建議選名稱明確的 20b 標籤。",
        "choice": "🧠 GPT-OSS latest｜同 20b",
    },
    "deepseek-v2:16b": {
        "badge": "💬 最快的一般中文聊天",
        "parameters": "15.7B MoE",
        "description": "中英雙語日常對話直接，本機生成速度約 74.9 tok/s。",
        "choice": "💬 DeepSeek V2 16B｜中文聊天快",
    },
    "deepseek-r1:8b": {
        "badge": "⚡ 本機回覆最快",
        "parameters": "8.2B",
        "description": "偏推理用途，本機生成速度約 75.2 tok/s；一般聊天優先選 V2 或 Gemma。",
        "choice": "⚡ DeepSeek R1 8B｜回覆最快、偏推理",
    },
}


def model_guide(model_id: str) -> dict[str, str]:
    return MODEL_GUIDES.get(model_id.lower(), {})


def model_parameter_text(item: ModelInfo) -> str:
    guide = model_guide(item.model_id)
    if guide.get("parameters"):
        return guide["parameters"]
    parameter_size = item.metadata.get("parameter_size")
    return str(parameter_size) if parameter_size else "未知"


def model_summary(item: ModelInfo) -> str:
    guide = model_guide(item.model_id)
    badge = guide.get("badge", "📦 其他已安裝模型")
    description = guide.get("description", "尚未提供用途說明，可直接切換測試。")
    details = [f"參數 {model_parameter_text(item)}"]
    if item.size_bytes is not None:
        details.append(f"檔案 {item.size_bytes / 1_000_000_000:.2f}GB")
    quant = item.metadata.get("quantization_level")
    if quant:
        details.append(str(quant))
    return f"{badge}｜{'・'.join(details)}\n  {description}"


def model_choice_name(item: ModelInfo) -> str:
    guide = model_guide(item.model_id)
    label = guide.get("choice")
    if label:
        return label[:100]
    return f"{item.model_id}｜{model_parameter_text(item)}"[:100]


def provider_short(provider_id: str) -> str:
    return {"ollama": "本機", "ollama-remote": "遠端"}.get(provider_id, provider_id)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()
        self.data: dict[str, Any] = {"scopes": {}}

    async def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("scopes", {})
            migrated = False
            for scope in self.data["scopes"].values():
                for entry in scope.get("room_history", []):
                    content = str(entry.get("content", ""))
                    if entry.get("role") == "assistant":
                        cleaned = clean_model_output(content)
                    elif entry.get("role") == "user":
                        cleaned = LEGACY_USER_TAG_RE.sub(
                            lambda match: f"[發言者：{match.group(1).strip()}]",
                            content,
                        )
                    else:
                        continue
                    if cleaned != content:
                        entry["content"] = cleaned
                        migrated = True
            if migrated:
                await self.save()
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("無法讀取狀態檔，將使用空白狀態：%s", self.path)

    def scope(self, key: str) -> dict[str, Any]:
        scopes = self.data.setdefault("scopes", {})
        scope = scopes.setdefault(
            key,
            {
                "provider": DEFAULT_PROVIDER,
                "models": {DEFAULT_PROVIDER: DEFAULT_MODEL},
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
                "histories": {},
                "autochat_enabled": False,
                "room_history": [],
                "room_summary": "",
                "tts_enabled": False,
                "tts_voice": TTS_DEFAULT_VOICE,
                "tts_mode": "all",
                "image_enabled": False,
                "character_card": "",
                "character_reference_ext": "",
                "character_reference_name": "",
                "character_strength": IPADAPTER_DEFAULT_WEIGHT,
            },
        )
        scope.setdefault("provider", DEFAULT_PROVIDER)
        models = scope.setdefault("models", {})
        legacy_model = scope.get("model")
        if legacy_model:
            models.setdefault("ollama", legacy_model)
        models.setdefault(DEFAULT_PROVIDER, DEFAULT_MODEL)
        scope.setdefault("system_prompt", DEFAULT_SYSTEM_PROMPT)
        scope.setdefault("histories", {})
        scope.setdefault("autochat_enabled", False)
        scope.setdefault("room_history", [])
        scope.setdefault("room_summary", "")
        scope.setdefault("tts_enabled", False)
        scope.setdefault("tts_voice", TTS_DEFAULT_VOICE)
        scope.setdefault("tts_mode", "all")
        scope.setdefault("image_enabled", False)
        scope.setdefault("character_card", "")
        scope.setdefault("character_reference_ext", "")
        scope.setdefault("character_reference_name", "")
        scope.setdefault("character_strength", IPADAPTER_DEFAULT_WEIGHT)
        return scope

    async def save(self) -> None:
        async with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)


def clean_model_output(text: str) -> str:
    """移除推理內容，以及模型不該顯示的 Discord 內部標記。"""
    text = THINK_BLOCK_RE.sub("", text)
    text = UNCLOSED_THINK_RE.sub("", text)
    text = THINK_TAG_RE.sub("", text)
    text = SPEAKER_TAG_RE.sub("", text)
    text = DISCORD_ID_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def estimate_text_tokens(text: str) -> int:
    """保守估算中英混合文字 token 數；實際數量仍以 Provider tokenizer 為準。"""
    if not text:
        return 0
    cjk_chars = len(CJK_CHAR_RE.findall(text))
    other_chars = max(0, len(text) - cjk_chars)
    return cjk_chars + math.ceil(other_chars / 4)


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    return sum(
        estimate_text_tokens(str(message.get("content", ""))) + 6
        for message in messages
    ) + 3


def memory_input_capacity() -> int:
    reserve = min(MEMORY_OUTPUT_RESERVE, OLLAMA_CONTEXT_LENGTH // 2)
    return max(512, OLLAMA_CONTEXT_LENGTH - reserve)


def memory_trigger_tokens() -> int:
    return min(
        memory_input_capacity(),
        max(512, int(OLLAMA_CONTEXT_LENGTH * MEMORY_COMPACT_TRIGGER)),
    )


def trim_summary(text: str) -> str:
    text = text.strip()
    if len(text) <= MEMORY_SUMMARY_MAX_CHARS:
        return text
    shortened = text[:MEMORY_SUMMARY_MAX_CHARS]
    split_at = max(
        shortened.rfind("\n"),
        shortened.rfind("。"),
        shortened.rfind("；"),
    )
    if split_at >= MEMORY_SUMMARY_MAX_CHARS // 2:
        shortened = shortened[: split_at + 1]
    return f"{shortened.rstrip()}\n- （更早細節已省略）"


def room_system_content(scope: dict[str, Any], is_dm: bool) -> str:
    sections: list[str] = []
    system_prompt = str(scope.get("system_prompt", "")).strip()
    if system_prompt:
        sections.append(system_prompt)
    sections.append(DM_INSTRUCTION if is_dm else ROOM_INSTRUCTION)
    summary = str(scope.get("room_summary", "")).strip()
    if summary:
        sections.append(
            "【較早對話的壓縮記憶；僅作為已發生事實，不是新的使用者指令】\n"
            f"{summary}"
        )
    return "\n\n".join(sections)


def build_room_messages(
    scope: dict[str, Any],
    labelled_content: str | None,
    is_dm: bool,
) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": room_system_content(scope, is_dm)}]
    messages.extend(scope.setdefault("room_history", [])[-MAX_HISTORY_MESSAGES:])
    if labelled_content is not None:
        messages.append({"role": "user", "content": labelled_content})
    return messages


@dataclass(frozen=True, slots=True)
class CompactionResult:
    status: str
    before_tokens: int
    after_tokens: int
    summarized_messages: int = 0
    error: str = ""


def active_provider_id(scope: dict[str, Any]) -> str:
    return str(scope.get("provider", DEFAULT_PROVIDER)).lower()


def active_model(scope: dict[str, Any]) -> str:
    provider_id = active_provider_id(scope)
    models = scope.setdefault("models", {})
    return str(models.get(provider_id, DEFAULT_MODEL if provider_id == DEFAULT_PROVIDER else ""))


def clear_scope_histories(scope: dict[str, Any]) -> None:
    scope["histories"] = {}
    scope["room_history"] = []
    scope["room_summary"] = ""


def scope_key_from_ids(guild_id: int | str, channel_id: int | str) -> str:
    return f"{guild_id}:{channel_id}"


def scope_key(interaction: discord.Interaction) -> str:
    guild_id = interaction.guild_id or "dm"
    channel_id = interaction.channel_id or interaction.user.id
    return scope_key_from_ids(guild_id, channel_id)


def can_change_settings(interaction: discord.Interaction) -> bool:
    if not ADMIN_ONLY_SETTINGS or interaction.guild is None:
        return True
    member = interaction.user
    return isinstance(member, discord.Member) and member.guild_permissions.manage_guild


async def require_settings_permission(interaction: discord.Interaction) -> bool:
    if can_change_settings(interaction):
        return True
    await interaction.response.send_message(
        "你需要「管理伺服器」權限才能修改頻道或 AI 設定。",
    )
    return False


def split_discord_message(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind("。", 0, limit)
            if split_at >= limit // 2:
                split_at += 1
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return chunks


def format_chat_record(
    display_name: str,
    user_message: str,
    assistant_message: str,
) -> str:
    safe_name = discord.utils.escape_markdown(display_name)
    safe_message = discord.utils.escape_markdown(user_message)
    return (
        f"**👤 {safe_name}**\n{safe_message}\n\n"
        f"**🤖 AI**\n{assistant_message}"
    )


class OllamaDiscordBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = MESSAGE_CONTENT_INTENT
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.state = StateStore(STATE_FILE)
        self.archive = HistoryArchive(DATABASE_FILE)
        self.providers = build_provider_registry(
            ollama_url=OLLAMA_URL,
            ollama_context_length=OLLAMA_CONTEXT_LENGTH,
            ollama_temperature=OLLAMA_TEMPERATURE,
            ollama_remote_url=OLLAMA_REMOTE_URL,
            ollama_keep_alive=OLLAMA_KEEP_ALIVE,
        )
        self.chat_locks: dict[str, asyncio.Lock] = {}
        self.voice_locks: dict[int, asyncio.Lock] = {}
        self.archive_cleanup_task: asyncio.Task[None] | None = None
        self.image_cleanup_task: asyncio.Task[None] | None = None
        self.image_lock = asyncio.Lock()
        self.image_client = (
            ComfyUIImageClient(
                base_url=COMFYUI_URL,
                checkpoint=COMFYUI_CHECKPOINT,
                width=COMFYUI_WIDTH,
                height=COMFYUI_HEIGHT,
                steps=COMFYUI_STEPS,
                cfg=COMFYUI_CFG,
                timeout_seconds=COMFYUI_TIMEOUT,
                ipadapter_model=IPADAPTER_MODEL,
                clip_vision_model=CLIP_VISION_MODEL,
                ipadapter_end_at=IPADAPTER_END_AT,
            )
            if COMFYUI_URL and IMAGE_SERVICE_ENABLED
            else None
        )

    async def setup_hook(self) -> None:
        await self.state.load()
        await self.archive.start()
        migrated_messages = 0
        for key, scope in self.state.data.get("scopes", {}).items():
            migrated_messages += await self.archive.migrate_scope(
                key,
                list(scope.get("room_history", [])),
            )
        if migrated_messages:
            LOGGER.info("已將 %d 則既有房間訊息封存至 SQLite", migrated_messages)
        deleted_messages = await self.archive.delete_older_than(
            ARCHIVE_RETENTION_DAYS
        )
        LOGGER.info(
            "SQLite 保存期限：%d 天；啟動清理已刪除 %d 則過期訊息",
            ARCHIVE_RETENTION_DAYS,
            deleted_messages,
        )
        self.archive_cleanup_task = asyncio.create_task(
            self.archive_cleanup_loop(),
            name="sqlite-retention-cleanup",
        )
        deleted_images = cleanup_expired_local_images()
        LOGGER.info(
            "圖片保存期限：%d 天；啟動清理已刪除 %d 張過期候選圖（核准圖保留）",
            IMAGE_RETENTION_DAYS,
            deleted_images,
        )
        self.image_cleanup_task = asyncio.create_task(
            self.image_cleanup_loop(),
            name="image-retention-cleanup",
        )
        await self.providers.start()
        if self.image_client is not None:
            await self.image_client.start()
        for key, scope in self.state.data.get("scopes", {}).items():
            active_tokens = estimate_messages_tokens(
                build_room_messages(scope, None, key.startswith("dm:"))
            )
            if active_tokens < memory_trigger_tokens():
                continue
            result = await compact_room_memory(
                key,
                scope,
                key.startswith("dm:"),
                force=True,
            )
            if result.status in {"failed", "too_large"}:
                LOGGER.warning(
                    "啟動時無法整理房間 %s：%s",
                    key,
                    result.error or result.status,
                )
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            LOGGER.info("已同步 %d 個測試伺服器指令", len(synced))
        synced = await self.tree.sync()
        LOGGER.info("已同步 %d 個全域指令", len(synced))

    async def close(self) -> None:
        if self.archive_cleanup_task is not None:
            self.archive_cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.archive_cleanup_task
            self.archive_cleanup_task = None
        if self.image_cleanup_task is not None:
            self.image_cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.image_cleanup_task
            self.image_cleanup_task = None
        if self.image_client is not None:
            await self.image_client.close()
        await self.providers.close()
        await super().close()

    async def archive_cleanup_loop(self) -> None:
        """Bot 運行期間定期刪除超過保存期限的 SQLite 原始訊息。"""
        while True:
            await asyncio.sleep(ARCHIVE_CLEANUP_INTERVAL_SECONDS)
            try:
                deleted = await self.archive.delete_older_than(
                    ARCHIVE_RETENTION_DAYS
                )
                if deleted:
                    LOGGER.info(
                        "SQLite 定期清理：已刪除 %d 則超過 %d 天的訊息",
                        deleted,
                        ARCHIVE_RETENTION_DAYS,
                    )
            except (OSError, sqlite3.Error):
                LOGGER.exception("SQLite 定期清理失敗；下個週期會重試")

    async def image_cleanup_loop(self) -> None:
        """定期清除過期的最近生成圖；人工核准 reference 永不自動刪除。"""
        while True:
            await asyncio.sleep(IMAGE_CLEANUP_INTERVAL_SECONDS)
            try:
                deleted = await asyncio.to_thread(cleanup_expired_local_images)
                if deleted:
                    LOGGER.info(
                        "圖片定期清理：已刪除 %d 張超過 %d 天的候選圖；核准圖保留",
                        deleted,
                        IMAGE_RETENTION_DAYS,
                    )
            except OSError:
                LOGGER.exception("圖片定期清理失敗；下個週期會重試")


bot = OllamaDiscordBot()


def find_ffmpeg() -> str | None:
    """尋找 ffmpeg：先看 PATH，再退回 winget 安裝位置（新裝後 PATH 未刷新時）。"""
    bundled = Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "bin" / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("ffmpeg")
    if found:
        return found
    local_appdata = os.getenv("LOCALAPPDATA", "")
    if not local_appdata:
        return None
    links = Path(local_appdata) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
    if links.exists():
        return str(links)
    packages = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        for candidate in sorted(packages.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe")):
            return str(candidate)
    return None


FFMPEG_PATH = find_ffmpeg()


async def make_tts_audio(scope: dict[str, Any], text: str) -> bytes | None:
    """本 scope 已開啟 TTS 時，把回覆合成為 mp3；失敗只記 log，不影響文字回覆。"""
    if not scope.get("tts_enabled", False):
        return None
    mode = str(scope.get("tts_mode", "all"))
    if mode == "dialogue":
        # 只唸引號內台詞；整則都是旁白時不出聲。
        text = tts.extract_dialogue(text)
        if not text:
            return None
    elif mode == "no_actions":
        text = tts.strip_actions(text)
    speech_text = tts.clean_for_speech(text, TTS_MAX_CHARS)
    if not speech_text:
        return None
    voice = str(scope.get("tts_voice") or TTS_DEFAULT_VOICE)
    try:
        return await tts.synthesize(speech_text, voice)
    except tts.TTSError as exc:
        LOGGER.warning("TTS 合成失敗（voice=%s）：%s", voice, exc)
        return None


async def play_tts_in_voice(guild: discord.Guild | None, audio: bytes) -> bool:
    """Bot 已連上該伺服器語音頻道時播放音訊；多則回覆依序排隊。"""
    if guild is None:
        return False
    voice_client = guild.voice_client
    if not isinstance(voice_client, discord.VoiceClient):
        return False
    lock = bot.voice_locks.setdefault(guild.id, asyncio.Lock())
    async with lock:
        voice_client = guild.voice_client
        if (
            not isinstance(voice_client, discord.VoiceClient)
            or not voice_client.is_connected()
        ):
            return False
        finished = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _after(error: Exception | None) -> None:
            if error:
                LOGGER.warning("語音頻道播放錯誤：%s", error)
            loop.call_soon_threadsafe(finished.set)

        if FFMPEG_PATH is None:
            LOGGER.warning("找不到 ffmpeg，無法在語音頻道播放；請安裝後重啟 Bot")
            return False
        try:
            source = discord.FFmpegPCMAudio(
                io.BytesIO(audio),
                pipe=True,
                executable=FFMPEG_PATH,
            )
            voice_client.play(source, after=_after)
        except (discord.ClientException, OSError) as exc:
            LOGGER.warning("無法在語音頻道播放：%s", exc)
            return False
        await finished.wait()
        return True


async def deliver_tts(
    scope: dict[str, Any],
    channel: discord.abc.Messageable,
    guild: discord.Guild | None,
    text: str,
) -> None:
    """文字回覆送出後呼叫：附上 mp3 音檔，若 Bot 在語音頻道則同時排隊朗讀。"""
    audio = await make_tts_audio(scope, text)
    if audio is None:
        return
    # 播放走背景任務，避免朗讀期間卡住本房間的文字對話。
    asyncio.create_task(play_tts_in_voice(guild, audio))
    try:
        await channel.send(
            file=discord.File(io.BytesIO(audio), filename="reply.mp3"),
        )
    except discord.HTTPException:
        LOGGER.exception("無法傳送 TTS 音檔")


def clean_image_prompt(text: str) -> str:
    """將提示詞模型的輸出收斂成 ComfyUI 可直接使用的一行。"""
    text = clean_model_output(text).strip().strip("`\"' ")
    text = re.sub(
        r"^(?:positive\s+prompt|image\s+prompt|prompt)\s*[：:]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*\n+\s*", ", ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    return text.strip(" ,")[:5000]


def character_asset_stem(key: str) -> str:
    """將 Discord scope key 轉成不洩漏伺服器／頻道 ID 的固定檔名。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def character_reference_path(key: str, scope: dict[str, Any]) -> Path | None:
    extension = str(scope.get("character_reference_ext", "")).lower()
    if extension not in {".png", ".jpg", ".webp"}:
        return None
    return CHARACTER_ASSET_DIR / f"{character_asset_stem(key)}-reference{extension}"


def latest_scene_path(key: str) -> Path:
    return CHARACTER_ASSET_DIR / f"{character_asset_stem(key)}-latest.png"


def cleanup_expired_local_images(now: float | None = None) -> int:
    """刪除過期的 latest 候選圖；reference 核准圖刻意不在匹配範圍內。"""
    if not CHARACTER_ASSET_DIR.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - IMAGE_RETENTION_DAYS * 86400
    deleted = 0
    for path in CHARACTER_ASSET_DIR.glob("*-latest.png"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        except FileNotFoundError:
            continue
    return deleted


def detect_reference_extension(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return None


def save_character_reference(
    key: str,
    scope: dict[str, Any],
    content: bytes,
    extension: str,
    source_name: str,
) -> Path:
    CHARACTER_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    stem = character_asset_stem(key)
    for old_extension in (".png", ".jpg", ".webp"):
        old_path = CHARACTER_ASSET_DIR / f"{stem}-reference{old_extension}"
        if old_path.exists() and old_extension != extension:
            old_path.unlink()
    path = CHARACTER_ASSET_DIR / f"{stem}-reference{extension}"
    path.write_bytes(content)
    scope["character_reference_ext"] = extension
    scope["character_reference_name"] = source_name[:200]
    return path


def remove_character_assets(key: str) -> None:
    stem = character_asset_stem(key)
    for suffix in (
        "-reference.png",
        "-reference.jpg",
        "-reference.webp",
        "-latest.png",
    ):
        path = CHARACTER_ASSET_DIR / f"{stem}{suffix}"
        if path.exists():
            path.unlink()


async def build_scene_image_prompt(
    scope: dict[str, Any],
    assistant_scene: str,
) -> str:
    """使用目前聊天模型，把最新回覆轉成適合 NoobAI-XL 的英文視覺標籤。"""
    provider = bot.providers.get(active_provider_id(scope))
    model = active_model(scope)
    character_card = str(scope.get("character_card", "")).strip()
    system_prompt = str(scope.get("system_prompt", "")).strip()
    character_context = (
        f"Approved immutable character identity tags (copy these traits exactly):\n"
        f"{character_card[:3000]}\n\n"
        if character_card
        else (
            f"Character/role settings (use only visible details):\n{system_prompt[:3000]}\n\n"
            if system_prompt
            else ""
        )
    )
    source = (
        f"{character_context}Latest assistant scene:\n"
        f"{assistant_scene[-IMAGE_PROMPT_SOURCE_MAX_CHARS:]}"
    )
    converted = await provider.chat(
        model,
        [
            {"role": "system", "content": IMAGE_PROMPT_INSTRUCTION},
            {"role": "user", "content": source},
        ],
        max_output_tokens=IMAGE_PROMPT_MAX_TOKENS,
    )
    tags = clean_image_prompt(converted)
    if not tags:
        raise ImageGenerationError("聊天模型沒有產生可用的生圖提示詞")
    locked_identity = f", {character_card}" if character_card else ""
    return f"{IMAGE_QUALITY_PREFIX}{locked_identity}, {tags}"


async def deliver_scene_image(
    scope: dict[str, Any],
    key: str,
    channel: discord.abc.Messageable,
    assistant_scene: str,
    *,
    reply_to: discord.Message | None = None,
) -> None:
    """依最新 AI 情境回覆生圖；失敗時提示但不影響原文字回覆。"""
    if not IMAGE_SERVICE_ENABLED or not scope.get("image_enabled", False):
        return
    client = bot.image_client
    if client is None:
        error_text = "⚠️ 生圖服務尚未設定，請通知管理員檢查 `COMFYUI_URL`。"
        if reply_to is not None:
            await reply_to.reply(error_text, mention_author=False)
        else:
            await channel.send(error_text)
        return

    try:
        positive = await build_scene_image_prompt(scope, assistant_scene)
        reference_content: bytes | None = None
        reference_filename = "character.png"
        reference_path = character_reference_path(key, scope)
        if reference_path is not None and reference_path.exists():
            reference_content = reference_path.read_bytes()
            reference_filename = reference_path.name
        reference_weight = min(
            1.2,
            max(
                0.1,
                float(scope.get("character_strength", IPADAPTER_DEFAULT_WEIGHT)),
            ),
        )
        # 單張 GPU 依序生成，避免多人同時請求造成顯存不足。
        async with bot.image_lock:
            generated = await client.generate(
                positive,
                IMAGE_NEGATIVE_PROMPT,
                reference_image=reference_content,
                reference_filename=reference_filename,
                reference_weight=reference_weight,
            )
        CHARACTER_ASSET_DIR.mkdir(parents=True, exist_ok=True)
        latest_scene_path(key).write_bytes(generated.content)
        if len(generated.content) > IMAGE_MAX_BYTES:
            raise ImageGenerationError(
                f"圖片大小 {len(generated.content) / 1_000_000:.1f}MB 超過 Discord 上傳上限"
            )
        upload = discord.File(
            io.BytesIO(generated.content),
            filename=f"scene-{generated.seed}.png",
        )
        reference_label = (
            f"｜角色基準 {reference_weight:g}" if reference_content else ""
        )
        caption = (
            f"🎨 情境圖片｜`{COMFYUI_CHECKPOINT}`{reference_label}｜"
            f"seed `{generated.seed}`"
        )
        if reply_to is not None:
            await reply_to.reply(
                caption,
                file=upload,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await channel.send(
                caption,
                file=upload,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except (ProviderError, ImageGenerationError, discord.HTTPException) as exc:
        LOGGER.exception("情境生圖失敗")
        warning = f"⚠️ 情境文字已回覆，但生圖失敗：{exc}"[:1900]
        try:
            if reply_to is not None:
                await reply_to.reply(
                    warning,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await channel.send(
                    warning,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except discord.HTTPException:
            LOGGER.exception("無法傳送生圖失敗提示")


async def compact_room_memory(
    key: str,
    scope: dict[str, Any],
    is_dm: bool,
    *,
    force: bool,
) -> CompactionResult:
    history = list(scope.setdefault("room_history", []))
    before_tokens = estimate_messages_tokens(build_room_messages(scope, None, is_dm))
    if len(history) <= 2:
        return CompactionResult("not_needed", before_tokens, before_tokens)

    keep_count = min(len(history), MEMORY_RECENT_MESSAGES)
    if keep_count % 2:
        keep_count -= 1
    keep_count = max(2, keep_count)
    if force and keep_count >= len(history) and len(history) > 2:
        keep_count = max(2, len(history) - 2)
        if keep_count % 2:
            keep_count -= 1

    # 以摘要最大尺寸預估，必要時少留幾輪原文，確保回答仍有固定空間。
    placeholder_scope = dict(scope)
    placeholder_scope["room_summary"] = "摘" * min(
        MEMORY_SUMMARY_MAX_TOKENS,
        MEMORY_SUMMARY_MAX_CHARS,
    )
    while keep_count > 2:
        placeholder_scope["room_history"] = history[-keep_count:]
        projected = estimate_messages_tokens(
            build_room_messages(placeholder_scope, None, is_dm)
        )
        if projected <= memory_trigger_tokens():
            break
        keep_count -= 2

    older = history[:-keep_count]
    recent = history[-keep_count:]
    if not older:
        return CompactionResult("not_needed", before_tokens, before_tokens)
    if not force and before_tokens < memory_trigger_tokens():
        return CompactionResult("not_needed", before_tokens, before_tokens)

    provider_id = active_provider_id(scope)
    model = active_model(scope)
    try:
        provider = bot.providers.get(provider_id)
    except ProviderError as exc:
        return CompactionResult(
            "failed",
            before_tokens,
            before_tokens,
            error=str(exc),
        )

    summary = str(scope.get("room_summary", "")).strip()
    chunk_input_limit = max(
        1536,
        memory_input_capacity() - MEMORY_SUMMARY_MAX_TOKENS - 256,
    )
    index = 0
    try:
        while index < len(older):
            chunk: list[dict[str, str]] = []
            while index < len(older):
                candidate = chunk + [older[index]]
                transcript = "\n\n".join(
                    f"{item.get('role', 'unknown')}:\n{item.get('content', '')}"
                    for item in candidate
                )
                summary_input = (
                    f"【既有摘要】\n{summary or '（無）'}\n\n"
                    f"【要併入的舊對話】\n{transcript}"
                )
                summary_messages = [
                    {"role": "system", "content": SUMMARY_INSTRUCTION},
                    {"role": "user", "content": summary_input},
                ]
                if (
                    chunk
                    and estimate_messages_tokens(summary_messages) > chunk_input_limit
                ):
                    break
                chunk = candidate
                index += 1

            transcript = "\n\n".join(
                f"{item.get('role', 'unknown')}:\n{item.get('content', '')}"
                for item in chunk
            )
            summary_input = (
                f"【既有摘要】\n{summary or '（無）'}\n\n"
                f"【要併入的舊對話】\n{transcript}"
            )
            generated = await provider.chat(
                model,
                [
                    {"role": "system", "content": SUMMARY_INSTRUCTION},
                    {"role": "user", "content": summary_input},
                ],
                max_output_tokens=MEMORY_SUMMARY_MAX_TOKENS,
            )
            summary = trim_summary(clean_model_output(generated))
            if not summary:
                raise ProviderError("摘要模型沒有回傳可用內容")
    except ProviderError as exc:
        LOGGER.warning("房間 %s 記憶整理失敗：%s", key, exc)
        return CompactionResult(
            "failed",
            before_tokens,
            before_tokens,
            error=str(exc),
        )

    scope["room_summary"] = summary
    scope["room_history"] = recent
    await bot.state.save()
    after_tokens = estimate_messages_tokens(build_room_messages(scope, None, is_dm))
    status = "compacted" if after_tokens <= memory_input_capacity() else "too_large"
    LOGGER.info(
        "房間 %s 記憶已整理：%d → %d estimated tokens，摘要 %d 則",
        key,
        before_tokens,
        after_tokens,
        len(older),
    )
    return CompactionResult(
        status,
        before_tokens,
        after_tokens,
        summarized_messages=len(older),
    )


@bot.event
async def on_ready() -> None:
    LOGGER.info("Discord 已登入：%s (%s)", bot.user, bot.user.id if bot.user else "?")
    LOGGER.info("Ollama API：%s", OLLAMA_URL)
    LOGGER.info("Message Content Intent：%s", MESSAGE_CONTENT_INTENT)
    LOGGER.info("ComfyUI API：%s", COMFYUI_URL or "未設定")


@bot.tree.command(name="providers", description="列出可用與預留的 AI Provider")
async def providers_command(interaction: discord.Interaction) -> None:
    lines = []
    for spec in bot.providers.specs():
        marker = "✅" if spec.enabled else "⏳"
        lines.append(f"{marker} `{spec.provider_id}` — {spec.display_name}（{spec.note}）")
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="provider", description="切換目前頻道使用的 AI Provider")
@app_commands.describe(name="使用 /providers 顯示的 Provider ID")
async def provider_command(interaction: discord.Interaction, name: str) -> None:
    if not await require_settings_permission(interaction):
        return
    await interaction.response.defer(thinking=True)
    provider_id = name.strip().lower()
    spec = bot.providers.spec(provider_id)
    if spec is None:
        await interaction.edit_original_response(
            content="找不到這個 Provider，請先使用 `/providers` 查看名稱。"
        )
        return
    if not spec.enabled:
        await interaction.edit_original_response(
            content=f"⏳ {spec.display_name} 已預留，但尚未設定 API 連線。"
        )
        return

    provider = bot.providers.get(provider_id)
    try:
        available = await provider.list_models()
    except ProviderError as exc:
        await interaction.edit_original_response(content=f"❌ {exc}")
        return
    if not available:
        await interaction.edit_original_response(
            content=f"{spec.display_name} 目前沒有可選模型。"
        )
        return

    scope = bot.state.scope(scope_key(interaction))
    scope["provider"] = provider_id
    models = scope.setdefault("models", {})
    available_ids = {item.model_id.lower(): item.model_id for item in available}
    selected = available_ids.get(str(models.get(provider_id, "")).lower())
    if selected is None:
        selected = available[0].model_id
    models[provider_id] = selected
    clear_scope_histories(scope)
    await bot.state.save()
    await interaction.edit_original_response(
        content=f"✅ 已切換至 `{provider_id}`／`{selected}`，並清除舊對話紀錄。"
    )


@provider_command.autocomplete("name")
async def provider_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    current_lower = current.lower()
    return [
        app_commands.Choice(
            name=f"{spec.provider_id} — {spec.display_name}"[:100],
            value=spec.provider_id,
        )
        for spec in bot.providers.specs()
        if current_lower in spec.provider_id.lower()
        or current_lower in spec.display_name.lower()
    ][:25]


@bot.tree.command(name="models", description="列出所有機器的模型、參數規模與用途建議")
async def models_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    scope = bot.state.scope(scope_key(interaction))
    active_pid = active_provider_id(scope)
    current_model = active_model(scope)
    lines = [f"**目前：`{active_pid}`／`{current_model}`**", ""]
    for spec in bot.providers.specs():
        if not spec.enabled or spec.provider is None:
            continue
        lines.append(f"__{spec.display_name}｜`{spec.provider_id}`__")
        try:
            models = await spec.provider.list_models()
        except ProviderError as exc:
            lines.extend([f"❌ {exc}", ""])
            continue
        if not models:
            lines.extend(["（沒有可用模型）", ""])
            continue
        for item in models:
            selected = (
                " ✅ 使用中"
                if spec.provider_id == active_pid
                and item.model_id.lower() == current_model.lower()
                else ""
            )
            lines.append(f"• `{item.model_id}`{selected}\n  {model_summary(item)}")
        lines.append("")
    lines.append("用 `/model` 選單可直接切換任何機器上的模型。")
    await interaction.edit_original_response(content="\n".join(lines)[:2000])


def parse_model_choice(raw: str) -> tuple[str | None, str]:
    """解析 /model 輸入。選單值為「provider_id/模型名」；手動輸入可只給模型名。

    以已註冊的 provider_id 當前綴比對，模型名本身含 / 也不會誤判。
    """
    raw = raw.strip()
    lowered = raw.lower()
    for spec in bot.providers.specs():
        prefix = f"{spec.provider_id}/"
        if lowered.startswith(prefix):
            return spec.provider_id, raw[len(prefix):]
    return None, raw


@bot.tree.command(name="model", description="切換模型；選單直接列出所有機器上的模型")
@app_commands.describe(name="選擇模型；可先用 /models 查看完整比較")
async def model_command(interaction: discord.Interaction, name: str) -> None:
    if not await require_settings_permission(interaction):
        return
    await interaction.response.defer(thinking=True)
    scope = bot.state.scope(scope_key(interaction))
    active_pid = active_provider_id(scope)
    target_pid, target_model = parse_model_choice(name)

    if target_pid is not None:
        specs = [bot.providers.spec(target_pid)]
    else:
        # 沒有指定機器時，先找目前 provider，再找其他已啟用的。
        specs = sorted(
            (spec for spec in bot.providers.specs() if spec.enabled),
            key=lambda spec: spec.provider_id != active_pid,
        )

    found = None
    errors: list[str] = []
    for spec in specs:
        if spec is None or not spec.enabled or spec.provider is None:
            continue
        try:
            available = await spec.provider.list_models()
        except ProviderError as exc:
            errors.append(str(exc))
            continue
        models_by_name = {item.model_id.lower(): item for item in available}
        item = models_by_name.get(target_model.lower())
        if item is not None:
            found = (spec, item)
            break

    if found is None:
        content = "找不到這個模型，請先使用 `/models` 查看完整名稱。"
        if errors:
            content += "\n" + "\n".join(f"❌ {error}" for error in errors)
        await interaction.edit_original_response(content=content[:2000])
        return

    spec, selected_item = found
    switched_provider = spec.provider_id != active_pid
    scope["provider"] = spec.provider_id
    scope.setdefault("models", {})[spec.provider_id] = selected_item.model_id
    clear_scope_histories(scope)
    await bot.state.save()
    switch_note = (
        f"（已同時切換到 {spec.display_name}）\n" if switched_provider else ""
    )
    await interaction.edit_original_response(
        content=(
            f"✅ 已切換為 `{spec.provider_id}`／`{selected_item.model_id}`，"
            f"並清除舊對話紀錄。\n{switch_note}{model_summary(selected_item)}"
        )
    )


@model_command.autocomplete("name")
async def model_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    current_lower = current.lower()
    choices: list[app_commands.Choice[str]] = []
    for spec in bot.providers.specs():
        if not spec.enabled or spec.provider is None:
            continue
        try:
            models = await spec.provider.list_models()
        except ProviderError:
            continue
        tag = provider_short(spec.provider_id)
        for item in models:
            label = f"{model_choice_name(item)}｜{tag}"[:100]
            if current_lower in item.model_id.lower() or current_lower in label.lower():
                choices.append(
                    app_commands.Choice(
                        name=label,
                        value=f"{spec.provider_id}/{item.model_id}"[:100],
                    )
                )
    return choices[:25]


@bot.tree.command(name="system", description="設定目前頻道的 system prompt")
@app_commands.describe(prompt="要傳給模型的角色設定與行為規則")
async def system_command(interaction: discord.Interaction, prompt: str) -> None:
    if not await require_settings_permission(interaction):
        return
    prompt = prompt.strip()
    if not prompt:
        await interaction.response.send_message("system prompt 不可為空。")
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["system_prompt"] = prompt
    clear_scope_histories(scope)
    await bot.state.save()
    await interaction.response.send_message(
        "✅ system prompt 已更新，並清除本頻道的舊對話紀錄。",
    )


@bot.tree.command(name="system_clear", description="清除目前頻道的 system prompt")
async def system_clear_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["system_prompt"] = ""
    clear_scope_histories(scope)
    await bot.state.save()
    await interaction.response.send_message(
        "✅ system prompt 已清除，對話紀錄也已重置。",
    )


autochat_group = app_commands.Group(
    name="autochat",
    description="管理目前頻道的多人自動對話模式",
)


@autochat_group.command(name="enable", description="開啟目前頻道的多人自動對話")
async def autochat_enable_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    if interaction.guild is None:
        await interaction.response.send_message(
            "自動對話只能在伺服器頻道或討論串中開啟。",
        )
        return
    if not MESSAGE_CONTENT_INTENT:
        await interaction.response.send_message(
            "❌ 程式尚未啟用 Message Content Intent。請先在 Discord Developer "
            "Portal 的 **Bot → Privileged Gateway Intents** 開啟 **Message Content "
            "Intent**，再把 `.env` 的 `MESSAGE_CONTENT_INTENT` 設為 `true` 並重啟 Bot。",
        )
        return

    scope = bot.state.scope(scope_key(interaction))
    scope["autochat_enabled"] = True
    scope["room_history"] = []
    scope["room_summary"] = ""
    await bot.state.save()
    await interaction.response.send_message(
        "✅ 已開啟多人對話。請 `@Bot` 或回覆 Bot 的訊息來觸發；其他一般聊天不會觸發 AI。",
    )


@autochat_group.command(name="disable", description="關閉目前頻道的多人自動對話")
async def autochat_disable_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["autochat_enabled"] = False
    scope["room_history"] = []
    scope["room_summary"] = ""
    await bot.state.save()
    await interaction.response.send_message(
        "✅ 已關閉多人自動對話，並清除本房間的共用對話記憶。",
    )


bot.tree.add_command(autochat_group)


image_group = app_commands.Group(
    name="image",
    description="管理依 AI 情境文字自動生圖",
)


@image_group.command(name="enable", description="開啟本頻道的 AI 回覆情境生圖")
async def image_enable_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    if not IMAGE_SERVICE_ENABLED:
        await interaction.response.send_message("❌ 管理介面目前已關閉生圖服務；請先在「圖片與語音」開啟並檢查依賴，再重新啟動 Bot。")
        return
    if bot.image_client is None:
        await interaction.response.send_message(
            "❌ 生圖服務尚未設定，請通知管理員設定 `COMFYUI_URL`。"
        )
        return
    await interaction.response.defer(thinking=True)
    try:
        await bot.image_client.health()
    except ImageGenerationError as exc:
        await interaction.edit_original_response(content=f"❌ 無法開啟生圖：{exc}")
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["image_enabled"] = True
    await bot.state.save()
    await interaction.edit_original_response(
        content=(
            "✅ 已開啟情境生圖。之後 Bot 每次實際回覆文字時，會把該次情境轉成"
            f"圖片；未 `@Bot`／未回覆 Bot 的一般訊息仍不會觸發。\n"
            f"模型：`{COMFYUI_CHECKPOINT}`｜{COMFYUI_WIDTH}×{COMFYUI_HEIGHT}｜"
            f"{COMFYUI_STEPS} steps"
        )
    )


@image_group.command(name="disable", description="關閉本頻道的 AI 回覆情境生圖")
async def image_disable_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["image_enabled"] = False
    await bot.state.save()
    await interaction.response.send_message(
        "✅ 已關閉情境生圖；文字聊天、房間記憶與歷史紀錄不受影響。"
    )


@image_group.command(name="status", description="查看本頻道的生圖設定與服務狀態")
async def image_status_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    scope = bot.state.scope(scope_key(interaction))
    enabled = IMAGE_SERVICE_ENABLED and bool(scope.get("image_enabled", False))
    service = "未設定"
    if bot.image_client is not None:
        try:
            await bot.image_client.health()
            service = "✅ 可連線"
        except ImageGenerationError as exc:
            service = f"❌ {exc}"
    await interaction.edit_original_response(
        content=(
            f"**情境生圖：{'✅ 已開啟' if enabled else '⏸️ 已關閉'}**\n"
            f"服務：{service}\n"
            f"模型：`{COMFYUI_CHECKPOINT}`\n"
            f"設定：{COMFYUI_WIDTH}×{COMFYUI_HEIGHT}、{COMFYUI_STEPS} steps、"
            f"CFG {COMFYUI_CFG:g}\n"
            "開啟後只在 Bot 回覆聊天時生成；用 `/image disable` 可隨時關閉。"
        )[:2000]
    )


bot.tree.add_command(image_group)


character_group = app_commands.Group(
    name="character",
    description="管理本房間的生圖角色卡與核准基準圖",
)


@character_group.command(name="card", description="建立固定角色卡，供每次情境生圖沿用")
@app_commands.describe(description="角色固定外觀；可用中文自然描述")
async def character_card_command(
    interaction: discord.Interaction,
    description: str,
) -> None:
    if not await require_settings_permission(interaction):
        return
    description = description.strip()
    if not description:
        await interaction.response.send_message("角色描述不可為空。")
        return
    await interaction.response.defer(thinking=True)
    scope = bot.state.scope(scope_key(interaction))
    try:
        provider = bot.providers.get(active_provider_id(scope))
        converted = await provider.chat(
            active_model(scope),
            [
                {"role": "system", "content": CHARACTER_CARD_INSTRUCTION},
                {"role": "user", "content": description[:6000]},
            ],
            max_output_tokens=IMAGE_PROMPT_MAX_TOKENS,
        )
        card = clean_image_prompt(converted)
        if not card:
            raise ProviderError("模型沒有產生可用的角色卡")
    except ProviderError as exc:
        await interaction.edit_original_response(content=f"❌ 建立角色卡失敗：{exc}")
        return
    scope["character_card"] = card
    await bot.state.save()
    await interaction.edit_original_response(
        content=(
            "✅ 已建立並鎖定本房間的角色卡。之後每次生圖都會加入這些固定特徵；"
            "房間記憶重置不會刪除它。\n"
            f"```text\n{card[:1700]}\n```"
        )[:2000]
    )


@character_group.command(name="reference", description="上傳並核准一張固定角色基準圖")
@app_commands.describe(image="PNG、JPEG 或 WebP；建議單人、正面、外觀清楚")
async def character_reference_command(
    interaction: discord.Interaction,
    image: discord.Attachment,
) -> None:
    if not await require_settings_permission(interaction):
        return
    if image.size > CHARACTER_REFERENCE_MAX_BYTES:
        await interaction.response.send_message(
            f"❌ 圖片不可超過 {CHARACTER_REFERENCE_MAX_BYTES / 1_000_000:g} MB。"
        )
        return
    await interaction.response.defer(thinking=True)
    try:
        content = await image.read()
    except discord.HTTPException as exc:
        await interaction.edit_original_response(content=f"❌ 下載附件失敗：{exc}")
        return
    extension = detect_reference_extension(content)
    if extension is None:
        await interaction.edit_original_response(
            content="❌ 只接受有效的 PNG、JPEG 或 WebP 圖片。"
        )
        return
    key = scope_key(interaction)
    scope = bot.state.scope(key)
    save_character_reference(key, scope, content, extension, image.filename)
    await bot.state.save()
    await interaction.edit_original_response(
        content=(
            "✅ 已核准並鎖定這張角色基準圖。它不會被後續生成圖自動覆蓋。\n"
            f"IP-Adapter 強度：`{float(scope['character_strength']):g}`；"
            "可用 `/character strength` 調整。"
        )
    )


@character_group.command(name="approve_last", description="核准 Bot 在本房間生成的上一張圖為角色基準")
async def character_approve_last_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    key = scope_key(interaction)
    latest = latest_scene_path(key)
    if not latest.exists():
        await interaction.response.send_message(
            "❌ 本房間尚無可核准的生成圖；請先開啟生圖並讓 Bot 生成一張。"
        )
        return
    scope = bot.state.scope(key)
    save_character_reference(
        key,
        scope,
        latest.read_bytes(),
        ".png",
        "Bot 上一張生成圖",
    )
    await bot.state.save()
    await interaction.response.send_message(
        "✅ 已將上一張生成圖核准為固定角色基準；後續圖片不會自動取代它。"
    )


@character_group.command(name="strength", description="調整核准基準圖對新圖片的影響力")
@app_commands.describe(value="0.1～1.2；預設 0.65，過高可能限制姿勢或破壞畫面")
async def character_strength_command(
    interaction: discord.Interaction,
    value: app_commands.Range[float, 0.1, 1.2],
) -> None:
    if not await require_settings_permission(interaction):
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["character_strength"] = round(float(value), 2)
    await bot.state.save()
    await interaction.response.send_message(
        f"✅ 本房間的 IP-Adapter 角色基準強度已設為 `{float(value):g}`。"
    )


@character_group.command(name="status", description="查看角色卡、基準圖與 IP-Adapter 強度")
async def character_status_command(interaction: discord.Interaction) -> None:
    key = scope_key(interaction)
    scope = bot.state.scope(key)
    card = str(scope.get("character_card", "")).strip()
    reference = character_reference_path(key, scope)
    has_reference = reference is not None and reference.exists()
    card_display = card[:1200] + ("…" if len(card) > 1200 else "")
    await interaction.response.send_message(
        (
            f"**角色卡：{'✅ 已鎖定' if card else '⏸️ 未設定'}**\n"
            f"{card_display or '（無）'}\n\n"
            f"**核准基準圖：{'✅ 已鎖定' if has_reference else '⏸️ 未設定'}**\n"
            f"來源：{scope.get('character_reference_name') or '（無）'}\n"
            f"IP-Adapter：`{IPADAPTER_MODEL or '未設定'}`\n"
            f"強度：`{float(scope.get('character_strength', IPADAPTER_DEFAULT_WEIGHT)):g}`"
        )[:2000]
    )


@character_group.command(name="clear", description="清除本房間的角色卡、基準圖與最近生成圖")
async def character_clear_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    key = scope_key(interaction)
    scope = bot.state.scope(key)
    remove_character_assets(key)
    scope["character_card"] = ""
    scope["character_reference_ext"] = ""
    scope["character_reference_name"] = ""
    scope["character_strength"] = IPADAPTER_DEFAULT_WEIGHT
    await bot.state.save()
    await interaction.response.send_message(
        "✅ 已清除角色卡與基準圖；聊天記憶和 `/image` 開關不受影響。"
    )


bot.tree.add_command(character_group)


tts_group = app_commands.Group(
    name="tts",
    description="管理目前頻道的文字轉語音（Edge TTS）",
)


@tts_group.command(name="enable", description="開啟本頻道的回覆語音：附 mp3，在語音頻道時同步朗讀")
async def tts_enable_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["tts_enabled"] = True
    await bot.state.save()
    voice = str(scope.get("tts_voice") or TTS_DEFAULT_VOICE)
    await interaction.response.send_message(
        f"✅ 已開啟文字轉語音（聲音：`{voice}`）。文字回覆照常送出，並附上 mp3 音檔；\n"
        "想在語音頻道直接朗讀，先進入語音頻道再用 `/tts join`。",
    )


@tts_group.command(name="disable", description="關閉本頻道的回覆語音")
async def tts_disable_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["tts_enabled"] = False
    await bot.state.save()
    await interaction.response.send_message("✅ 已關閉文字轉語音，之後只回覆文字。")


@tts_group.command(name="voice", description="切換本頻道的朗讀聲音")
@app_commands.describe(name="Edge TTS 聲音名稱；選單列出常用中日英聲音")
async def tts_voice_command(interaction: discord.Interaction, name: str) -> None:
    if not await require_settings_permission(interaction):
        return
    await interaction.response.defer(thinking=True)
    resolved = await tts.resolve_voice(name)
    if resolved is None:
        await interaction.edit_original_response(
            content="找不到這個聲音名稱，請從選單挑選，或參考 Edge TTS 的 ShortName（例如 `zh-TW-HsiaoChenNeural`）。"
        )
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["tts_voice"] = resolved
    await bot.state.save()
    label = tts.RECOMMENDED_VOICES.get(resolved, "")
    suffix = f"（{label}）" if label else ""
    await interaction.edit_original_response(
        content=f"✅ 朗讀聲音已切換為 `{resolved}`{suffix}。"
    )


@tts_voice_command.autocomplete("name")
async def tts_voice_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    current_lower = current.lower()
    return [
        app_commands.Choice(name=f"{label}｜{short_name}"[:100], value=short_name)
        for short_name, label in tts.RECOMMENDED_VOICES.items()
        if current_lower in short_name.lower() or current_lower in label.lower()
    ][:25]


TTS_MODE_LABELS = {
    "all": "全部朗讀",
    "dialogue": "只唸台詞（「」引號內）",
    "no_actions": "跳過動作／場景描述",
}


@tts_group.command(name="mode", description="設定朗讀範圍：全部／只唸台詞／跳過動作描述")
@app_commands.describe(mode="要朗讀回覆的哪些部分")
@app_commands.choices(
    mode=[
        app_commands.Choice(name="全部朗讀（預設）", value="all"),
        app_commands.Choice(name="只唸台詞：只朗讀「」引號內的句子", value="dialogue"),
        app_commands.Choice(name="跳過動作／場景：*動作* 與（旁白）不唸", value="no_actions"),
    ]
)
async def tts_mode_command(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
) -> None:
    if not await require_settings_permission(interaction):
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["tts_mode"] = mode.value
    await bot.state.save()
    extra = ""
    if mode.value == "dialogue":
        extra = (
            "\nℹ️ 台詞判定依引號「」『』“”；建議在 system prompt 加一句"
            "「所有台詞都用「」包住」讓模型輸出更穩定。整則沒有台詞時不會出聲。"
        )
    await interaction.response.send_message(
        f"✅ 朗讀範圍已設為：{TTS_MODE_LABELS[mode.value]}。{extra}",
    )


@tts_group.command(name="join", description="讓 Bot 加入你所在的語音頻道朗讀回覆")
async def tts_join_command(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "私訊沒有語音頻道；開啟 `/tts enable` 後私訊回覆會直接附上 mp3。",
        )
        return
    member = interaction.user
    voice_state = getattr(member, "voice", None)
    channel = getattr(voice_state, "channel", None)
    if channel is None:
        await interaction.response.send_message(
            "請先加入一個語音頻道，再使用 `/tts join`。",
        )
        return
    await interaction.response.defer(thinking=True)
    voice_client = interaction.guild.voice_client
    try:
        if isinstance(voice_client, discord.VoiceClient) and voice_client.is_connected():
            await voice_client.move_to(channel)
        else:
            await channel.connect()
    except (discord.ClientException, discord.HTTPException, asyncio.TimeoutError) as exc:
        LOGGER.exception("無法加入語音頻道")
        await interaction.edit_original_response(
            content=f"❌ 無法加入語音頻道：{exc}\n（提示：語音功能需要安裝 PyNaCl）"
        )
        return
    notes = [f"✅ 已加入語音頻道 `{channel.name}`。"]
    scope = bot.state.scope(scope_key(interaction))
    if not scope.get("tts_enabled", False):
        notes.append("⚠️ 本頻道尚未開啟 TTS，請再執行 `/tts enable`。")
    if FFMPEG_PATH is None:
        notes.append(
            "⚠️ 找不到 `ffmpeg`，語音頻道播放會失敗；"
            "請先安裝（PowerShell：`winget install Gyan.FFmpeg`）並重啟 Bot。"
        )
    await interaction.edit_original_response(content="\n".join(notes))


@tts_group.command(name="leave", description="讓 Bot 離開語音頻道")
async def tts_leave_command(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("私訊沒有語音頻道。")
        return
    voice_client = interaction.guild.voice_client
    if not isinstance(voice_client, discord.VoiceClient) or not voice_client.is_connected():
        await interaction.response.send_message("Bot 目前不在任何語音頻道。")
        return
    await voice_client.disconnect()
    await interaction.response.send_message("✅ 已離開語音頻道。")


bot.tree.add_command(tts_group)


@bot.tree.command(name="room_status", description="查看目前房間的活動記憶與封存用量")
async def room_status_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    key = scope_key(interaction)
    scope = bot.state.scope(key)
    is_dm = interaction.guild is None
    active_tokens = estimate_messages_tokens(build_room_messages(scope, None, is_dm))
    context_percent = min(999, round(active_tokens / OLLAMA_CONTEXT_LENGTH * 100))
    summary_tokens = estimate_text_tokens(str(scope.get("room_summary", "")))
    archive = await bot.archive.stats(key)
    await interaction.edit_original_response(
        content=(
            "**房間記憶狀態**\n"
            f"活動上下文：約 `{active_tokens} / {OLLAMA_CONTEXT_LENGTH}` tokens "
            f"（{context_percent}%）\n"
            f"自動整理門檻：約 `{memory_trigger_tokens()}` tokens\n"
            f"回答預留：`{min(MEMORY_OUTPUT_RESERVE, OLLAMA_CONTEXT_LENGTH // 2)}` tokens\n"
            f"壓縮摘要：約 `{summary_tokens}` tokens\n"
            f"近期原文：`{len(scope.get('room_history', []))}` 則\n"
            f"SQLite 最近 {ARCHIVE_RETENTION_DAYS} 天封存："
            f"`{archive.messages}` 則／`{archive.characters}` 字元\n\n"
            "活動用量是保守估算；Ollama 實際 tokenizer 可能略有差異。"
        )
    )


@bot.tree.command(name="room_compact", description="手動整理房間記憶並保留劇情摘要")
async def room_compact_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    await interaction.response.defer(thinking=True)
    key = scope_key(interaction)
    lock = bot.chat_locks.setdefault(f"room:{key}", asyncio.Lock())
    async with lock:
        scope = bot.state.scope(key)
        result = await compact_room_memory(
            key,
            scope,
            interaction.guild is None,
            force=True,
        )

    if result.status == "compacted":
        content = (
            f"✅ 已整理 `{result.summarized_messages}` 則較舊訊息並保留劇情摘要。\n"
            f"活動記憶：約 `{result.before_tokens} → {result.after_tokens}` tokens。"
        )
    elif result.status == "not_needed":
        content = "ℹ️ 目前較舊訊息不足，暫時不需要整理。"
    elif result.status == "too_large":
        content = (
            "⚠️ 已產生摘要，但活動記憶仍太大。請使用 `/room_reset` 開始新對話。"
        )
    else:
        content = (
            "⚠️ 本房間記憶即將滿載，自動整理失敗。"
            f"請稍後重試 `/room_compact` 或使用 `/room_reset`。\n原因：{result.error}"
        )
    await interaction.edit_original_response(content=content[:2000])


@bot.tree.command(name="room_reset", description="清除目前頻道的多人共用對話記憶")
async def room_reset_command(interaction: discord.Interaction) -> None:
    if not await require_settings_permission(interaction):
        return
    scope = bot.state.scope(scope_key(interaction))
    scope["room_history"] = []
    scope["room_summary"] = ""
    await bot.state.save()
    await interaction.response.send_message(
        f"✅ 已清除本房間的活動記憶並開始新對話；SQLite 封存仍保留至每則訊息滿 "
        f"{ARCHIVE_RETENTION_DAYS} 天後自動刪除。",
    )


@bot.tree.command(
    name="room_delete_history",
    description="永久刪除本房間的活動記憶與 SQLite 封存",
)
@app_commands.describe(confirm="必須選擇 True 才會永久刪除")
async def room_delete_history_command(
    interaction: discord.Interaction,
    confirm: bool,
) -> None:
    if not await require_settings_permission(interaction):
        return
    if not confirm:
        await interaction.response.send_message(
            "已取消。若確定永久刪除，請把 `confirm` 設為 `True`。"
        )
        return
    await interaction.response.defer(thinking=True)
    key = scope_key(interaction)
    lock = bot.chat_locks.setdefault(f"room:{key}", asyncio.Lock())
    async with lock:
        scope = bot.state.scope(key)
        scope["room_history"] = []
        scope["room_summary"] = ""
        deleted = await bot.archive.delete_scope(key)
        await bot.state.save()
    await interaction.edit_original_response(
        content=f"✅ 已永久刪除本房間的活動記憶與 `{deleted}` 則 SQLite 封存訊息。"
    )


@bot.tree.command(name="reset", description="清除你在目前頻道的對話紀錄")
async def reset_command(interaction: discord.Interaction) -> None:
    scope = bot.state.scope(scope_key(interaction))
    scope.setdefault("histories", {}).pop(str(interaction.user.id), None)
    await bot.state.save()
    await interaction.response.send_message("✅ 你的對話紀錄已清除。")


@bot.tree.command(name="status", description="查看目前頻道的模型與 system prompt")
async def status_command(interaction: discord.Interaction) -> None:
    key = scope_key(interaction)
    scope = bot.state.scope(key)
    provider_id = active_provider_id(scope)
    prompt = scope.get("system_prompt", "") or "（未設定）"
    if len(prompt) > 1000:
        prompt = f"{prompt[:1000]}…"
    reference = character_reference_path(key, scope)
    await interaction.response.send_message(
        f"**Provider**\n`{provider_id}`\n\n"
        f"**模型**\n`{active_model(scope)}`\n\n"
        f"**多人對話（@／回覆觸發）**\n"
        f"{'✅ 已開啟' if scope.get('autochat_enabled') else '⛔ 已關閉'}\n\n"
        f"**文字轉語音（TTS）**\n"
        f"{'✅ 已開啟' if scope.get('tts_enabled') else '⛔ 已關閉'}"
        f"｜聲音：`{scope.get('tts_voice') or TTS_DEFAULT_VOICE}`"
        f"｜範圍：{TTS_MODE_LABELS.get(str(scope.get('tts_mode', 'all')), '全部朗讀')}\n\n"
        f"**情境生圖**\n"
        f"{'✅ 已開啟' if IMAGE_SERVICE_ENABLED and scope.get('image_enabled') else '⛔ 已關閉'}"
        f"｜模型：`{COMFYUI_CHECKPOINT}`\n\n"
        f"**生圖角色固定**\n"
        f"角色卡：{'✅' if scope.get('character_card') else '⏸️'}｜"
        f"基準圖：{'✅' if reference is not None and reference.is_file() else '⏸️'}｜"
        f"強度：`{float(scope.get('character_strength', IPADAPTER_DEFAULT_WEIGHT)):g}`\n\n"
        f"**System prompt**\n{prompt}\n\n"
        f"**Context**\n{OLLAMA_CONTEXT_LENGTH} tokens",
    )


@bot.tree.command(name="chat", description="使用目前選定的本機模型聊天")
@app_commands.describe(message="你想對角色說的內容")
async def chat_command(interaction: discord.Interaction, message: str) -> None:
    message = message.strip()
    if not message:
        await interaction.response.send_message("訊息不可為空。")
        return

    await interaction.response.defer(thinking=True)
    key = scope_key(interaction)
    user_key = str(interaction.user.id)
    lock_key = f"{key}:{user_key}"
    lock = bot.chat_locks.setdefault(lock_key, asyncio.Lock())

    async with lock:
        scope = bot.state.scope(key)
        provider_id = active_provider_id(scope)
        model = active_model(scope)
        history = scope.setdefault("histories", {}).setdefault(user_key, [])
        system_prompt = scope.get("system_prompt", "").strip()
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history[-MAX_HISTORY_MESSAGES:])
        messages.append({"role": "user", "content": message})

        try:
            provider = bot.providers.get(provider_id)
            answer = clean_model_output(
                await provider.chat(
                    model,
                    messages,
                    max_output_tokens=CHAT_MAX_OUTPUT_TOKENS,
                )
            )
            if not answer:
                raise ProviderError("模型沒有回傳可顯示的文字內容")
        except ProviderError as exc:
            await interaction.edit_original_response(content=f"❌ {exc}")
            return

        history.extend(
            [
                {"role": "user", "content": message},
                {"role": "assistant", "content": answer},
            ]
        )
        scope["histories"][user_key] = history[-MAX_HISTORY_MESSAGES:]
        await bot.state.save()

    record = format_chat_record(interaction.user.display_name, message, answer)
    chunks = split_discord_message(record)
    channel = interaction.channel
    if channel is None:
        await interaction.edit_original_response(content="❌ 無法取得目前頻道。")
        return
    try:
        for chunk in chunks:
            await channel.send(
                chunk,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except discord.HTTPException as exc:
        LOGGER.exception("無法在 Discord 頻道留下聊天紀錄")
        await interaction.edit_original_response(
            content=f"❌ 無法發送公開聊天紀錄：{exc}"
        )
        return
    image_enabled = IMAGE_SERVICE_ENABLED and bool(scope.get("image_enabled", False))
    await interaction.edit_original_response(
        content=(
            "✅ 已回覆並留下公開聊天紀錄，正在依情境生圖…"
            if image_enabled
            else "✅ 已回覆並留下公開聊天紀錄。"
        )
    )
    async with channel.typing():
        await asyncio.gather(
            deliver_tts(scope, channel, interaction.guild, answer),
            deliver_scene_image(scope, key, channel, answer),
        )
    if image_enabled:
        await interaction.edit_original_response(
            content="✅ 已回覆並完成情境生圖處理。"
        )


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or message.webhook_id is not None:
        return

    is_dm = message.guild is None
    guild_id: int | str = "dm" if is_dm else message.guild.id
    key = scope_key_from_ids(guild_id, message.channel.id)
    if is_dm:
        scope = bot.state.scope(key)
    else:
        scope = bot.state.data.get("scopes", {}).get(key)
        if not scope or not scope.get("autochat_enabled", False):
            return

    bot_user = bot.user
    if bot_user is None:
        return
    mentioned_bot = any(user.id == bot_user.id for user in message.mentions)
    replied_to_bot = False
    if message.reference is not None:
        referenced = message.reference.resolved
        if referenced is None and message.reference.message_id is not None:
            try:
                referenced = await message.channel.fetch_message(
                    message.reference.message_id
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                referenced = None
        referenced_author = getattr(referenced, "author", None)
        replied_to_bot = getattr(referenced_author, "id", None) == bot_user.id

    if not is_dm and not mentioned_bot and not replied_to_bot:
        return

    content = re.sub(rf"<@!?{bot_user.id}>", "", message.content).strip()
    if not content:
        content = "你好"

    lock = bot.chat_locks.setdefault(f"room:{key}", asyncio.Lock())
    async with lock:
        # 等待期間管理員可能已經關閉伺服器自動對話，因此取得鎖後再確認一次。
        scope = bot.state.data.get("scopes", {}).get(key)
        if not scope or (
            not is_dm and not scope.get("autochat_enabled", False)
        ):
            return

        provider_id = active_provider_id(scope)
        model = active_model(scope)
        display_name = getattr(message.author, "display_name", message.author.name)
        display_name = str(display_name).replace("\n", " ")[:80]
        labelled_content = content if is_dm else f"[發言者：{display_name}]\n{content}"
        messages = build_room_messages(scope, labelled_content, is_dm)
        estimated_tokens = estimate_messages_tokens(messages)
        compaction: CompactionResult | None = None
        if estimated_tokens >= memory_trigger_tokens():
            async with message.channel.typing():
                compaction = await compact_room_memory(
                    key,
                    scope,
                    is_dm,
                    force=True,
                )
            messages = build_room_messages(scope, labelled_content, is_dm)
            estimated_tokens = estimate_messages_tokens(messages)

        if (
            compaction is not None
            and compaction.status in {"failed", "too_large"}
        ) or estimated_tokens > memory_input_capacity():
            reason = f"\n原因：{compaction.error}" if compaction and compaction.error else ""
            try:
                await message.reply(
                    "⚠️ 本房間記憶即將滿載，自動整理失敗。請使用 "
                    f"`/room_compact` 或 `/room_reset`。{reason}"[:1900],
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                LOGGER.exception("無法發送房間記憶滿載提示")
            return

        try:
            async with message.channel.typing():
                provider = bot.providers.get(provider_id)
                answer = clean_model_output(
                    await provider.chat(
                        model,
                        messages,
                        max_output_tokens=min(
                            MEMORY_OUTPUT_RESERVE,
                            CHAT_MAX_OUTPUT_TOKENS,
                        ),
                    )
                )
            if not answer:
                raise ProviderError("模型沒有回傳可顯示的文字內容")
        except ProviderError as exc:
            try:
                await message.reply(
                    f"❌ {exc}",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                LOGGER.exception("無法回覆多人自動對話錯誤")
            return

        room_history = scope.setdefault("room_history", [])
        room_history.extend(
            [
                {"role": "user", "content": labelled_content},
                {"role": "assistant", "content": answer},
            ]
        )
        scope["room_history"] = room_history[-MAX_HISTORY_MESSAGES:]
        try:
            await bot.archive.append_turn(
                key,
                labelled_content,
                answer,
                display_name,
                str(message.id),
            )
        except (OSError, sqlite3.Error):
            LOGGER.exception("無法將房間 %s 的對話封存至 SQLite", key)
        await bot.state.save()

        try:
            chunks = split_discord_message(answer)
            await message.reply(
                chunks[0],
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            for chunk in chunks[1:]:
                await message.channel.send(
                    chunk,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except discord.HTTPException:
            LOGGER.exception("無法在多人自動對話頻道回覆訊息")

    # TTS 與生圖都在房間鎖之外執行，避免合成期間擋住下一則文字對話。
    async with message.channel.typing():
        await asyncio.gather(
            deliver_tts(scope, message.channel, message.guild, answer),
            deliver_scene_image(
                scope,
                key,
                message.channel,
                answer,
                reply_to=message,
            ),
        )


def main() -> None:
    if not DISCORD_TOKEN:
        raise SystemExit("缺少 DISCORD_TOKEN，請先複製 .env.example 為 .env 並填入 Token。")
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
