"""Read-only Bot token check for the setup wizard."""

import asyncio
import json

import aiohttp


async def verify_discord_token(token: str) -> dict:
    if any(ord(char) < 32 or ord(char) == 127 for char in token):
        return {"ok": False, "error": "Discord Token 格式無效"}
    try:
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.get(
                "https://discord.com/api/v10/oauth2/applications/@me",
                headers={"Authorization": f"Bot {token}"}, allow_redirects=False,
            ) as response:
                if response.status != 200:
                    hints = {401: "Token 無效，請到 Developer Portal 重新取得", 403: "Token 無法存取 Bot Application", 429: "請求過多，請稍後再試"}
                    return {"ok": False, "error": hints.get(response.status, f"Discord API 回傳 HTTP {response.status}")}
                content = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    content.extend(chunk)
                    if len(content) > 1_000_000:
                        return {"ok": False, "error": "Discord API 回應過大"}
                data = json.loads(content)
        application_id = str(data.get("id", ""))
        if not application_id.isdigit() or not 15 <= len(application_id) <= 22:
            return {"ok": False, "error": "Discord API 未回傳有效 Application ID"}
        bot = data.get("bot") or {}
        return {"ok": True, "application_id": application_id, "username": str(bot.get("username") or data.get("name") or "Discord Bot")[:100]}
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, AttributeError):
        return {"ok": False, "error": "無法連線到 Discord，請檢查網路後重試"}
