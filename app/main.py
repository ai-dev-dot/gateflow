import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import async_session
from app.models.agent_type import AgentType
from app.routers.agent_types import router as agent_types_router
from app.routers.anthropic_forward import router as anthropic_forward_router
from app.routers.api_keys import router as api_keys_router
from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.backup import router as backup_router
from app.routers.chat import router as chat_router
from app.routers.gateway_forward import router as gateway_forward_router
from app.routers.model_configs import router as model_configs_router
from app.routers.pages import router as pages_router
from app.routers.provider_keys import router as provider_keys_router
from app.routers.usage import router as usage_router
from app.routers.users import router as users_router
from app.services.auth_service import AuthService
from app.services.cleanup_service import stale_pending_cleanup_loop
from app.utils.crypto import verify_fernet_works
from app.utils.hashing import verify_hmac_works
from app.utils.http_client import close_http_client
from app.utils.request_id import RequestIDMiddleware
from app.utils.startup_checks import verify_jwt_secret_not_placeholder


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail-fast: verify all critical config BEFORE serving any traffic.
    # Order matters — cheapest / most likely to fail checks first.
    verify_jwt_secret_not_placeholder()  # P0-1
    verify_fernet_works()
    verify_hmac_works()

    # Schema is managed by Alembic -- run `alembic upgrade head` before
    # starting the app (start.bat / start.sh do this automatically).
    # Startup no longer creates tables or ALTERs columns.

    # Initialize admin user
    async with async_session() as db:
        auth_service = AuthService(db)
        await auth_service.init_admin()

    # Seed default AgentType values
    async with async_session() as db:
        from sqlalchemy import select

        result = await db.execute(select(AgentType).limit(1))
        if not result.scalar_one_or_none():
            for name in ["Claude Code", "Codex", "Cursor", "Dify", "LangChain", "自定义"]:
                db.add(AgentType(name=name))
            await db.commit()

    # P2-6: periodically mark zombie pending audit logs as failed
    # (finally-block saves that never landed due to crash / DB outage).
    cleanup_task = asyncio.create_task(stale_pending_cleanup_loop())

    yield

    # Cleanup on shutdown
    cleanup_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task
    await close_http_client()


app = FastAPI(title="闸机 GateFlow", version="0.1.0", lifespan=lifespan)

# Request ID middleware must be added BEFORE CORS so every request has
# an id available during CORS preflight handling too.
app.add_middleware(RequestIDMiddleware)

# CORS origins from .env (comma-separated). Default: localhost dev only.
# Production: set ALLOWED_ORIGINS="https://gateflow.example.com,https://admin.gateflow.example.com"
_allowed_origins = [
    origin.strip() for origin in get_settings().ALLOWED_ORIGINS.split(",") if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(api_keys_router)
app.include_router(provider_keys_router)
app.include_router(model_configs_router)
app.include_router(gateway_forward_router)
app.include_router(anthropic_forward_router)
app.include_router(audit_router)
app.include_router(usage_router)
app.include_router(chat_router)
app.include_router(agent_types_router)
app.include_router(backup_router)
app.include_router(pages_router)

# Static files (CSS, JS)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def root(request: Request):
    """Root: logged in → /pages/chat, not logged in → /pages/login."""
    from fastapi.responses import RedirectResponse
    from jose import JWTError, jwt

    token = request.cookies.get("gf_session")
    if token:
        try:
            settings = get_settings()
            jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
            return RedirectResponse(url="/pages/chat")
        except JWTError:
            pass
    return RedirectResponse(url="/pages/login")


@app.get("/health")
async def health_check():
    return {"status": "ok"}
