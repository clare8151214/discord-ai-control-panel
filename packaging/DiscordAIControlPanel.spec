"""Build the Windows desktop and its bot worker into one executable.

Run scripts/build-windows.ps1 instead of invoking this spec directly. The build
script keeps generated files and dependency licenses outside the source tree.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all, collect_data_files, collect_dynamic_libs, collect_submodules,
    copy_metadata,
)

root = Path(os.environ["DISCORD_AI_SOURCE_ROOT"])
resources = Path(os.environ["DISCORD_AI_BUNDLE_RESOURCES"])

datas = [
    (str(root / "web" / "static"), "web/static"),
    (str(resources / "ThirdPartyNotices.txt"), "."),
    (str(resources / "licenses"), "licenses"),
    (str(resources / "build-info.json"), "."),
    # FFmpeg is a separate, unmodified subprocess, not linked into Python.
    (str(resources / "bin" / "ffmpeg.exe"), "bin"),
]
binaries = []
hiddenimports = [
    "bot", "history_archive", "image_generation", "tts",
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on", "aiohttp", "discord", "edge_tts", "pydantic",
    "clr", "webview.platforms.winforms", "webview.platforms.edgechromium",
]
for package in ("providers", "desktop", "web"):
    hiddenimports.extend(collect_submodules(package))

# discord.opus loads this DLL lazily through a dynamically constructed ctypes
# path. Import analysis does not find it; PCM voice playback requires it at
# precisely discord/bin/libopus-0.x64.dll in the extracted bundle.
binaries.extend(collect_dynamic_libs("discord"))
datas.extend(collect_data_files("discord", includes=["bin/COPYING"]))

# pywebview's PyInstaller hook handles its Windows .NET assemblies. Explicit
# data collection also retains the JS bridge, WebView2Loader and license files.
for package in ("webview", "pythonnet", "clr_loader", "nacl"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_imports)

for distribution in ("discord.py", "edge-tts", "pywebview", "pythonnet", "uvicorn"):
    datas.extend(copy_metadata(distribution))

analysis = Analysis(
    [str(root / "desktop_entry.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[str(root / "packaging" / "runtime_hook.py")],
    excludes=[
        "PyQt5", "PyQt6", "PySide2", "PySide6", "pytest",
        "IPython", "numpy", "webview.platforms.android",
        "webview.platforms.cocoa", "webview.platforms.gtk", "webview.platforms.qt",
    ],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="DiscordAIControlPanel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    uac_admin=False,
)
