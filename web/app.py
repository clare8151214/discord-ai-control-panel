from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from providers import ProviderError, create_provider
from .models import BotProfile, ProviderConfig, DEFAULT_CHECKPOINT, DEFAULT_IPADAPTER, DEFAULT_CLIP_VISION
from .discord_setup import verify_discord_token
from .image_setup import check_image_setup
from .runtime import ROOT, RuntimeManager
from .store import ProfileStore


class LoginRequest(BaseModel):
    password: str = Field(max_length=1024, repr=False)


class DiscordVerifyRequest(BaseModel):
    discord_token: str = Field(default="", max_length=8192, repr=False)
    profile_id: str | None = None


class ImageCheckRequest(BaseModel):
    comfyui_url: str = Field(min_length=1, max_length=2048)
    comfyui_checkpoint: str = Field(default=DEFAULT_CHECKPOINT, min_length=1, max_length=255)
    ipadapter_model: str = Field(default=DEFAULT_IPADAPTER, min_length=1, max_length=255)
    clip_vision_model: str = Field(default=DEFAULT_CLIP_VISION, min_length=1, max_length=255)


class ProbeRequest(BaseModel):
    provider: ProviderConfig
    profile_id: str | None = None


class ChatTestRequest(ProbeRequest):
    model: str = Field(min_length=1, max_length=200)
    message: str = Field(default="請回覆：連線成功", min_length=1, max_length=4000)
    context_length: int = Field(default=8192, ge=1024, le=262144)
    output_tokens: int = Field(default=768, ge=128, le=16384)


def create_app(
    data_dir: Path | None = None, *, password: str | None = None,
    bot_script: Path | None = None, legacy_env: Path | None = None,
    allow_legacy_import: bool | None = None,
    desktop_bootstrap_token: str | None = None,
) -> FastAPI:
    password = password if password is not None else os.environ.get("CONTROL_PANEL_PASSWORD", "")
    if len(password) < 12:
        raise RuntimeError("請設定至少 12 字元的 CONTROL_PANEL_PASSWORD，再執行 python -m web")
    store = ProfileStore(data_dir or Path(os.environ.get("CONTROL_PANEL_DATA_DIR", ROOT / "data" / "control-panel")))
    manager = RuntimeManager(store, bot_script=bot_script)
    sessions: dict[str, float] = {}
    failures: dict[str, list[float]] = {}
    allow_import = allow_legacy_import if allow_legacy_import is not None else os.environ.get("CONTROL_PANEL_ALLOW_LEGACY_IMPORT", "false").lower() == "true"
    secure_cookie = os.environ.get("CONTROL_PANEL_SECURE_COOKIE", "false").lower() == "true"
    desktop_mode = desktop_bootstrap_token is not None
    # Desktop always owns a plain-HTTP loopback listener. Do not inherit a
    # web deployment's HTTPS-only cookie setting from the surrounding shell.
    secure_cookie = secure_cookie and not desktop_mode
    bootstrap_token = desktop_bootstrap_token
    cookie_name = "bot_panel_session" if not desktop_mode else "bot_desktop_" + secrets.token_hex(6)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await manager.restore()
        yield
        await manager.shutdown()

    app = FastAPI(title="Discord AI Control Panel", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.manager = manager

    def issue_desktop_bootstrap() -> str:
        nonlocal bootstrap_token
        if not desktop_mode:
            raise RuntimeError("Desktop bootstrap is not enabled")
        bootstrap_token = secrets.token_urlsafe(32)
        return bootstrap_token

    app.state.issue_desktop_bootstrap = issue_desktop_bootstrap

    def issue_session(response: Response) -> None:
        now = time.monotonic()
        for key in [key for key, expiration in sessions.items() if expiration < now]:
            sessions.pop(key, None)
        if len(sessions) >= 128:
            sessions.pop(next(iter(sessions)))
        token = secrets.token_urlsafe(32)
        sessions[token] = float("inf") if desktop_mode else now + 43200
        response.set_cookie(cookie_name, token, max_age=None if desktop_mode else 43200, httponly=True, secure=secure_cookie, samesite="strict")

    def authenticated(request: Request) -> bool:
        value = request.cookies.get(cookie_name, "")
        expiration = sessions.get(value, 0)
        if expiration < time.monotonic():
            sessions.pop(value, None)
            return False
        return True

    def require_login(request: Request) -> None:
        if not authenticated(request):
            raise HTTPException(401, "請先登入管理介面")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and (urlsplit(origin).scheme not in {"http", "https"} or urlsplit(origin).netloc != request.headers.get("host")):
                return JSONResponse({"detail": "不允許跨來源修改"}, status_code=403)
            if request.url.path.startswith("/api/") and request.url.path != "/api/login" and request.headers.get("x-control-panel") != "1":
                return JSONResponse({"detail": "缺少管理介面請求標記"}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 1_000_000:
                    return JSONResponse({"detail": "請求過大"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "無效 Content-Length"}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic includes submitted values by default, including tokens.
        return JSONResponse({"detail": [{"loc": item["loc"], "msg": item["msg"]} for item in error.errors()]}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing_profile(request, error):
        return JSONResponse({"detail": "找不到 Bot 設定"}, status_code=404)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready():
        if not os.access(store.directory, os.W_OK):
            raise HTTPException(503, "設定儲存空間不可寫入")
        return {"status": "ready"}

    @app.get("/api/session")
    async def session(request: Request):
        return {"authenticated": authenticated(request), "legacy_import_allowed": allow_import, "desktop": desktop_mode}

    @app.get("/desktop/bootstrap")
    async def desktop_bootstrap(request: Request, token: str = ""):
        nonlocal bootstrap_token
        if not desktop_mode or not request.client or request.client.host not in {"127.0.0.1", "::1"}:
            raise HTTPException(404, "Not found")
        if not bootstrap_token or not hmac.compare_digest(token.encode(), bootstrap_token.encode()):
            raise HTTPException(403, "桌面登入連結已使用或失效，請從桌面程式重新開啟")
        bootstrap_token = None
        response = RedirectResponse("/", status_code=303)
        issue_session(response)
        return response

    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request, response: Response):
        now = time.monotonic()
        address = request.client.host if request.client else "unknown"
        attempts = failures.setdefault(address, [])
        attempts[:] = [stamp for stamp in attempts if now - stamp < 60]
        if len(attempts) >= 5:
            raise HTTPException(429, "登入失敗過多，請一分鐘後重試")
        if not hmac.compare_digest(body.password.encode(), password.encode()):
            attempts.append(now)
            raise HTTPException(401, "管理密碼錯誤")
        failures.pop(address, None)
        issue_session(response)
        return {"authenticated": True}

    @app.post("/api/logout", dependencies=[Depends(require_login)])
    async def logout(request: Request, response: Response):
        sessions.pop(request.cookies.get(cookie_name, ""), None)
        response.delete_cookie(cookie_name)
        return {"authenticated": False}

    @app.get("/api/desktop-info", dependencies=[Depends(require_login)])
    async def desktop_info():
        return {"desktop": True, "data_dir": str(store.directory), "version": "1.0.0"} if desktop_mode else {"desktop": False}

    @app.post("/api/discord/verify", dependencies=[Depends(require_login)])
    async def discord_verify(body: DiscordVerifyRequest):
        token = body.discord_token.strip()
        if not token and body.profile_id:
            token = store.get(body.profile_id).get("discord_token", "")
        if not token:
            return {"ok": False, "error": "請先填入 Discord Bot Token"}
        return await verify_discord_token(token)

    @app.post("/api/image/check", dependencies=[Depends(require_login)])
    async def image_check(body: ImageCheckRequest):
        return await check_image_setup(
            body.comfyui_url, body.comfyui_checkpoint,
            body.ipadapter_model, body.clip_vision_model,
        )

    @app.get("/api/profiles", dependencies=[Depends(require_login)])
    async def profiles():
        return {"profiles": [store.public(config) for config in store.all()]}

    @app.post("/api/profiles", dependencies=[Depends(require_login)], status_code=201)
    async def create_profile(body: BotProfile):
        return store.public(store.save(body))

    @app.put("/api/profiles/{profile_id}", dependencies=[Depends(require_login)])
    async def update_profile(profile_id: str, body: BotProfile):
        return store.public(store.save(body, profile_id))

    @app.delete("/api/profiles/{profile_id}", dependencies=[Depends(require_login)], status_code=204)
    async def delete_profile(profile_id: str):
        try:
            await manager.delete(profile_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return Response(status_code=204)

    @app.get("/api/runtime", dependencies=[Depends(require_login)])
    async def runtime():
        return {"bots": [manager.status(config["id"]) for config in store.all()]}

    @app.get("/api/profiles/{profile_id}/logs", dependencies=[Depends(require_login)])
    async def logs(profile_id: str):
        store.get(profile_id)
        return {"logs": manager.status(profile_id)["logs"]}

    @app.post("/api/profiles/{profile_id}/start", dependencies=[Depends(require_login)])
    async def start(profile_id: str):
        try:
            return await manager.start(profile_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/profiles/{profile_id}/stop", dependencies=[Depends(require_login)])
    async def stop(profile_id: str):
        return await manager.stop(profile_id)

    @app.post("/api/profiles/{profile_id}/restart", dependencies=[Depends(require_login)])
    async def restart(profile_id: str):
        try:
            # A failed preflight must not stop an otherwise working Bot.
            await manager.validate_configuration(profile_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        await manager.stop(profile_id)
        return await start(profile_id)

    @app.post("/api/probe", dependencies=[Depends(require_login)])
    async def probe(body: ProbeRequest):
        config = store.resolve_provider(body.provider.model_dump(), body.profile_id)
        provider = None
        try:
            provider = create_provider(config)
            await provider.start()
            await asyncio.wait_for(provider.probe(), timeout=15)
            models = await asyncio.wait_for(provider.list_models(), timeout=15)
            return {"ok": True, "models": [{"id": item.model_id, "name": item.display_name, "metadata": item.metadata} for item in models]}
        except (ProviderError, asyncio.TimeoutError) as exc:
            message = str(exc) or "連線逾時"
            if config.get("api_key"):
                message = message.replace(config["api_key"], "[REDACTED]")
            return {"ok": False, "models": [], "error": message[:600]}
        finally:
            if provider:
                await provider.close()

    @app.post("/api/chat-test", dependencies=[Depends(require_login)])
    async def chat_test(body: ChatTestRequest):
        config = store.resolve_provider(body.provider.model_dump(), body.profile_id)
        provider = None
        try:
            provider = create_provider(config, context_length=body.context_length, keep_alive="30s")
            await provider.start()
            result = await asyncio.wait_for(provider.chat(body.model, [{"role": "user", "content": body.message}], max_output_tokens=body.output_tokens), timeout=120)
            return {"ok": True, "reply": result}
        except (ProviderError, asyncio.TimeoutError) as exc:
            message = str(exc) or "聊天測試逾時；本地模型首次載入可能需要較久"
            if config.get("api_key"):
                message = message.replace(config["api_key"], "[REDACTED]")
            return {"ok": False, "error": message[:600]}
        finally:
            if provider:
                await provider.close()

    @app.post("/api/import-legacy", dependencies=[Depends(require_login)], status_code=201)
    async def import_legacy():
        if not allow_import:
            raise HTTPException(403, "需由主機設定 CONTROL_PANEL_ALLOW_LEGACY_IMPORT=true 才能匯入舊設定")
        path = legacy_env or ROOT / ".env"
        if not path.is_file():
            raise HTTPException(404, "主機沒有可匯入的 .env")
        values = dotenv_values(path)
        providers = [ProviderConfig(id="ollama", name="Ollama（原本本機）", base_url=values.get("OLLAMA_URL") or "http://127.0.0.1:11434")]
        if values.get("OLLAMA_REMOTE_URL"):
            providers.append(ProviderConfig(id="ollama-remote", name="Ollama（遠端）", base_url=values["OLLAMA_REMOTE_URL"]))
        profile = BotProfile(
            name="匯入的 Discord Bot", discord_token=values.get("DISCORD_TOKEN") or "",
            guild_id=values.get("DISCORD_GUILD_ID") or "", providers=providers,
            default_provider=values.get("DEFAULT_PROVIDER") or "ollama",
            default_model=values.get("DEFAULT_MODEL") or "",
            system_prompt=values.get("DEFAULT_SYSTEM_PROMPT") or "",
            message_content_intent=(values.get("MESSAGE_CONTENT_INTENT") or "false").lower() == "true",
            admin_only_settings=(values.get("ADMIN_ONLY_SETTINGS") or "true").lower() == "true",
            context_length=int(values.get("OLLAMA_CONTEXT_LENGTH") or 8192),
            output_tokens=int(values.get("CHAT_MAX_OUTPUT_TOKENS") or 768),
            retention_days=int(values.get("ARCHIVE_RETENTION_DAYS") or 7),
            compact_trigger=float(values.get("MEMORY_COMPACT_TRIGGER") or 0.7),
            comfyui_url=values.get("COMFYUI_URL") or "",
            comfyui_checkpoint=values.get("COMFYUI_CHECKPOINT") or DEFAULT_CHECKPOINT,
            ipadapter_model=values.get("IPADAPTER_MODEL") or DEFAULT_IPADAPTER,
            clip_vision_model=values.get("CLIP_VISION_MODEL") or DEFAULT_CLIP_VISION,
            tts_voice=values.get("TTS_DEFAULT_VOICE") or "zh-TW-HsiaoChenNeural",
        )
        return store.public(store.save(profile))

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    return app
