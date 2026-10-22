"""Check the actual one-file archive, independently of import-only self-tests.

This runs in the build environment, never starts Discord or a model, and compares
the bundled browser assets to the exact source tree used for the release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def inspect_bundle(executable: Path, source_root: Path) -> dict:
    archive = CArchiveReader(str(executable))
    names = {name.replace("\\", "/"): name for name in archive.toc}
    required_files = (
        "bin/ffmpeg.exe",
        "discord/bin/libopus-0.x64.dll",
        "discord/bin/COPYING",
        "certifi/cacert.pem",
        "_sqlite3.pyd",
        "sqlite3.dll",
        "nacl/_sodium.pyd",
        "ThirdPartyNotices.txt",
        "licenses/dependency-inventory.json",
    )
    missing = [name for name in required_files if name not in names]
    if not any(name.startswith("davey/") and name.endswith(".pyd") for name in names):
        missing.append("davey native extension")
    if not any(name.startswith("licenses/discord.py/") and name.endswith("-COPYING") for name in names):
        missing.append("licenses/discord.py/libopus-COPYING")
    embedded = archive.open_embedded_archive("PYZ.pyz")
    required_modules = (
        "bot", "image_generation", "history_archive", "tts", "discord.opus",
        "discord.voice_client", "davey", "nacl.bindings", "edge_tts",
        "providers.anthropic", "providers.gemini", "providers.ollama",
        "providers.openai_compatible", "web.app", "web.image_setup", "desktop.launcher",
    )
    missing.extend(name for name in required_modules if name not in embedded.toc)
    if missing:
        return {"ok": False, "missing": missing}
    hashes = {}
    mismatched_assets = []
    for name in ("web/static/index.html", "web/static/app.js", "web/static/style.css"):
        if name not in names:
            mismatched_assets.append(name)
            continue
        bundled_hash = hashlib.sha256(archive.extract(names[name])).hexdigest()
        with (source_root / name).open("rb") as source_stream:
            source_hash = hashlib.file_digest(source_stream, "sha256").hexdigest()
        hashes[name] = bundled_hash
        if bundled_hash != source_hash:
            mismatched_assets.append(name)
    # User credentials and data are never a legitimate resource in the bundle.
    forbidden_files = [name for name in names if Path(name).name == ".env" or name.startswith("data/")]
    return {
        "ok": not mismatched_assets and not forbidden_files,
        "bundled_voice_dll": "discord/bin/libopus-0.x64.dll",
        "bundled_voice_license": "discord/bin/COPYING",
        "required_modules_present": True,
        "static_asset_sha256": hashes,
        "mismatched_assets": mismatched_assets,
        "forbidden_files": forbidden_files,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_bundle(args.executable, args.source_root)
    args.result_file.parent.mkdir(parents=True, exist_ok=True)
    args.result_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
