"""Generate an auditable license bundle from the isolated build environment."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffmpeg-license", type=Path, required=True)
    parser.add_argument("--ffmpeg-readme", type=Path)
    parser.add_argument("--provenance", default="User-provided FFmpeg binary")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    licenses = args.output / "licenses"
    licenses.mkdir(exist_ok=True)
    inventory = []
    for distribution in sorted(metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = distribution.metadata["Name"]
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name).lower()
        destination = licenses / safe_name
        destination.mkdir(exist_ok=True)
        (destination / "METADATA.txt").write_text(distribution.read_text("METADATA") or "", encoding="utf-8")
        license_files = []
        for file in distribution.files or []:
            normalized = str(file).replace("\\", "/")
            basename = Path(normalized).name
            if not (".dist-info/licenses/" in normalized or re.match(r"^(LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)([._-]|$)", basename, re.IGNORECASE)):
                continue
            source = Path(distribution.locate_file(file))
            if source.is_file():
                filename = f"{len(license_files):02d}-{basename}"
                shutil.copy2(source, destination / filename)
                license_files.append(filename)
        inventory.append({"name": name, "version": distribution.version,
                          "license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License", "See package metadata"),
                          "license_files": license_files})
    (licenses / "dependency-inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"Python distribution license missing: {python_license}")
    (licenses / "python").mkdir(exist_ok=True)
    shutil.copy2(python_license, licenses / "python" / "LICENSE.txt")
    # Python's Tk fallback also bundles Tcl/Tk scripts. Retain their notices in
    # the human-readable release archive, not only in the runtime resources.
    for index, license_path in enumerate((Path(sys.base_prefix) / "tcl").glob("*/license.terms")):
        shutil.copy2(license_path, licenses / "python" / f"tcltk-{index}-{license_path.parent.name}.txt")
    ffmpeg_directory = licenses / "ffmpeg"
    ffmpeg_directory.mkdir(exist_ok=True)
    shutil.copy2(args.ffmpeg_license, ffmpeg_directory / "LICENSE.txt")
    if args.ffmpeg_readme and args.ffmpeg_readme.is_file():
        shutil.copy2(args.ffmpeg_readme, ffmpeg_directory / "README.txt")
    ffmpeg_version = subprocess.run([str(args.ffmpeg), "-version"], check=True, capture_output=True, text=True).stdout
    (ffmpeg_directory / "version-and-configuration.txt").write_text(ffmpeg_version, encoding="utf-8")
    (args.output / "bin").mkdir(exist_ok=True)
    shutil.copy2(args.ffmpeg, args.output / "bin" / "ffmpeg.exe")
    with args.ffmpeg.open("rb") as ffmpeg_stream:
        ffmpeg_hash = hashlib.file_digest(ffmpeg_stream, "sha256").hexdigest()
    build_info = {
        "built_at": datetime.now(timezone.utc).isoformat(), "python": sys.version,
        "platform": platform.platform(), "ffmpeg_provenance": args.provenance,
        "ffmpeg_sha256": ffmpeg_hash,
        "ffmpeg_version": ffmpeg_version.splitlines()[0], "packages": inventory,
    }
    (args.output / "build-info.json").write_text(json.dumps(build_info, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(Path(__file__).with_name("ThirdPartyNotices.txt"), args.output / "ThirdPartyNotices.txt")


if __name__ == "__main__":
    main()
