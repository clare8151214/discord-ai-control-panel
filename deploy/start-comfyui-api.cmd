@echo off
setlocal

rem Local-only by default. For remote clients, set COMFYUI_LISTEN to the
rem appropriate LAN interface and allow only trusted clients in the firewall.
if not defined COMFYUI_LISTEN set "COMFYUI_LISTEN=127.0.0.1"

if not exist C:\AI\logs mkdir C:\AI\logs
if not exist C:\AI\ComfyUI_temp mkdir C:\AI\ComfyUI_temp

cd /d C:\AI\ComfyUI_windows_portable
python_embeded\python.exe -s ComfyUI\main.py ^
  --listen "%COMFYUI_LISTEN%" ^
  --port 8188 ^
  --disable-auto-launch ^
  --windows-standalone-build ^
  --temp-directory C:\AI\ComfyUI_temp ^
  >> C:\AI\logs\comfyui.log 2>&1
