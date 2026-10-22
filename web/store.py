from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .models import BotProfile, PROFILE_ID


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ProfileStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, profile_id: str) -> Path:
        if not PROFILE_ID.fullmatch(profile_id):
            raise KeyError("找不到 Bot 設定")
        return self.directory / "profiles" / profile_id

    def get(self, profile_id: str) -> dict[str, Any]:
        try:
            data = json.loads((self.path(profile_id) / "config.json").read_text(encoding="utf-8"))
            # Normalize older profiles in memory without rewriting credentials
            # or silently changing their on-disk configuration.
            return BotProfile.model_validate(data).model_dump()
        except FileNotFoundError as exc:
            raise KeyError("找不到 Bot 設定") from exc

    def all(self) -> list[dict[str, Any]]:
        root = self.directory / "profiles"
        if not root.exists():
            return []
        return [self.get(path.name) for path in sorted(root.iterdir()) if PROFILE_ID.fullmatch(path.name) and (path / "config.json").is_file()]

    def save(self, profile: BotProfile, profile_id: str | None = None) -> dict[str, Any]:
        previous = self.get(profile_id) if profile_id else {}
        data = profile.model_dump()
        data["id"] = profile_id or uuid.uuid4().hex
        if not data["discord_token"] and not data.pop("clear_discord_token", False):
            data["discord_token"] = previous.get("discord_token", "")
        data.pop("clear_discord_token", None)
        previous_providers = {item["id"]: item for item in previous.get("providers", [])}
        for provider in data["providers"]:
            if not provider["api_key"] and not provider.pop("clear_api_key", False):
                stored = previous_providers.get(provider["id"], {})
                if stored.get("base_url") == provider["base_url"] and stored.get("type") == provider["type"]:
                    provider["api_key"] = stored.get("api_key", "")
            provider.pop("clear_api_key", None)
        atomic_json(self.path(data["id"]) / "config.json", data)
        return data

    @staticmethod
    def public(data: dict[str, Any]) -> dict[str, Any]:
        result = {**data, "discord_token": "", "has_discord_token": bool(data.get("discord_token"))}
        result["providers"] = [{**provider, "api_key": "", "has_api_key": bool(provider.get("api_key"))} for provider in data["providers"]]
        return result

    def resolve_provider(self, provider: dict[str, Any], profile_id: str | None) -> dict[str, Any]:
        result = dict(provider)
        if profile_id and not result.get("api_key") and not result.get("clear_api_key"):
            previous = self.get(profile_id)
            stored = next((item for item in previous["providers"] if item["id"] == result["id"]), {})
            # A saved credential must not be forwarded to a newly changed endpoint.
            if stored.get("base_url") == result.get("base_url") and stored.get("type") == result.get("type"):
                result["api_key"] = stored.get("api_key", "")
        return result
