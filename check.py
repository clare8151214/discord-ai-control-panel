"""檢查 Bot 執行環境與 Ollama 連線，不需要 Discord Token。"""

import asyncio
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

import bot as bot_module
from bot import (
    COMFYUI_CFG,
    COMFYUI_CHECKPOINT,
    COMFYUI_HEIGHT,
    COMFYUI_STEPS,
    COMFYUI_TIMEOUT,
    COMFYUI_URL,
    COMFYUI_WIDTH,
    CLIP_VISION_MODEL,
    DEFAULT_MODEL,
    IPADAPTER_END_AT,
    IPADAPTER_MODEL,
    OLLAMA_CONTEXT_LENGTH,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_TEMPERATURE,
    OLLAMA_URL,
    bot as discord_bot,
    compact_room_memory,
)
from image_generation import ComfyUIImageClient
from history_archive import HistoryArchive
from providers import ChatProvider, ModelInfo, build_provider_registry


class MemoryTestProvider(ChatProvider):
    provider_id = "memory-test"
    display_name = "Memory Test"

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo("test", "test")]

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
    ) -> str:
        return (
            "- 參與者：甲、乙\n"
            "- 場景：測試房間\n"
            "- 重要事件：較早對話已整理\n"
            "- 未完成事項：延續目前話題"
        )


async def check_memory_compaction() -> None:
    history: list[dict[str, str]] = []
    for index in range(20):
        history.append(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"第 {index + 1} 則測試劇情：" + "劇情內容" * 180,
            }
        )
    scope = {
        "provider": "memory-test",
        "models": {"memory-test": "test"},
        "system_prompt": "",
        "histories": {},
        "autochat_enabled": True,
        "room_history": history,
        "room_summary": "",
    }
    discord_bot.providers.register(MemoryTestProvider())
    with tempfile.TemporaryDirectory() as temporary:
        discord_bot.state.path = Path(temporary) / "state.json"
        discord_bot.state.data = {"scopes": {"memory-test-room": scope}}
        result = await compact_room_memory(
            "memory-test-room",
            scope,
            False,
            force=True,
        )
    if result.status != "compacted":
        raise RuntimeError(f"Memory compaction failed: {result}")
    if not scope["room_summary"] or len(scope["room_history"]) >= len(history):
        raise RuntimeError("Memory compaction did not create summary/reduce history")


async def check_archive_retention() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "retention.sqlite3"
        archive = HistoryArchive(path)
        await archive.start()
        await archive.append_turn(
            "room",
            "recent user",
            "recent assistant",
            "tester",
            "recent",
        )
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executemany(
                """
                INSERT INTO room_messages
                    (scope_key, role, content, source_message_id, created_at)
                VALUES ('room', ?, ?, 'old', datetime('now', '-8 days'))
                """,
                [
                    ("user", "old user"),
                    ("assistant", "old assistant"),
                ],
            )
        deleted = await archive.delete_older_than(7)
        stats = await archive.stats("room")
        if deleted != 2 or stats.messages != 2:
            raise RuntimeError(
                f"Archive retention failed: deleted={deleted}, remaining={stats.messages}"
            )


def check_image_retention() -> None:
    original_directory = bot_module.CHARACTER_ASSET_DIR
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        bot_module.CHARACTER_ASSET_DIR = directory
        old_latest = directory / "old-latest.png"
        recent_latest = directory / "recent-latest.png"
        approved_reference = directory / "old-reference.png"
        for path in (old_latest, recent_latest, approved_reference):
            path.write_bytes(b"test")
        old_time = time.time() - (bot_module.IMAGE_RETENTION_DAYS + 1) * 86400
        os.utime(old_latest, (old_time, old_time))
        os.utime(approved_reference, (old_time, old_time))
        deleted = bot_module.cleanup_expired_local_images()
        if deleted != 1 or old_latest.exists() or not recent_latest.exists():
            raise RuntimeError("Image retention did not delete only expired latest image")
        if not approved_reference.exists():
            raise RuntimeError("Image retention deleted an approved reference")
    bot_module.CHARACTER_ASSET_DIR = original_directory


async def main() -> None:
    providers = build_provider_registry(
        ollama_url=OLLAMA_URL,
        ollama_context_length=OLLAMA_CONTEXT_LENGTH,
        ollama_temperature=OLLAMA_TEMPERATURE,
        ollama_keep_alive=OLLAMA_KEEP_ALIVE,
    )
    await providers.start()
    try:
        models = await providers.get("ollama").list_models()
    finally:
        await providers.close()

    names = [item.model_id for item in models]
    print(f"Ollama API: {OLLAMA_URL}")
    print(f"Ollama keep-alive: {OLLAMA_KEEP_ALIVE}")
    print(f"Models: {len(names)}")
    for name in names:
        print(f"- {name}")
    if DEFAULT_MODEL.lower() not in {name.lower() for name in names}:
        raise SystemExit(f"Default model is not installed: {DEFAULT_MODEL}")
    await check_memory_compaction()
    print("Memory compaction: OK")
    await check_archive_retention()
    print("SQLite 7-day retention: OK")
    check_image_retention()
    print("Image 2-day retention/reference preservation: OK")
    if COMFYUI_URL:
        image_client = ComfyUIImageClient(
            COMFYUI_URL,
            COMFYUI_CHECKPOINT,
            COMFYUI_WIDTH,
            COMFYUI_HEIGHT,
            COMFYUI_STEPS,
            COMFYUI_CFG,
            COMFYUI_TIMEOUT,
            ipadapter_model=IPADAPTER_MODEL,
            clip_vision_model=CLIP_VISION_MODEL,
            ipadapter_end_at=IPADAPTER_END_AT,
        )
        await image_client.start()
        try:
            await image_client.health()
            if IPADAPTER_MODEL and CLIP_VISION_MODEL:
                await image_client.node_info("IPAdapterAdvanced")
                await image_client.node_info("IPAdapterModelLoader")
        finally:
            await image_client.close()
        print(f"ComfyUI API: {COMFYUI_URL} (OK)")
        print(f"Image checkpoint: {COMFYUI_CHECKPOINT}")
        print(f"IP-Adapter: {IPADAPTER_MODEL}")
        print(f"CLIP Vision: {CLIP_VISION_MODEL}")
    print("Check: OK")


if __name__ == "__main__":
    asyncio.run(main())
