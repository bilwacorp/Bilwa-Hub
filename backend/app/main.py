import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.casbin_enforcer import init_enforcer
from app.core.casbin_watcher import start_watcher, stop_watcher
from app.core.maintenance_scheduler import start_scheduler as start_maintenance_scheduler, stop_scheduler as stop_maintenance_scheduler
from app.core.expiry_reminder_scheduler import start_scheduler as start_expiry_scheduler, stop_scheduler as stop_expiry_scheduler
from app.api.routers import auth, register, ingest, deployments, tickets, maintenance, users, notifications, rbac, events
from app.approvals import api as approvals_api
from app.approvals import deployment_hooks  # noqa: F401 — registers deployment completion hooks at import time
from app.rules import api as rules_api
from app.workflow import api as workflow_api

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"
PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Same retry-loop shape as PoultryOS-CBP's main.py — a transient DB blip
    # at exactly the wrong moment shouldn't crash startup outright.
    for attempt in range(5):
        try:
            await init_enforcer()
            break
        except Exception:
            if attempt == 4:
                raise
            logger.warning("init_enforcer failed (attempt %d/5), retrying in 2s", attempt + 1, exc_info=True)
            await asyncio.sleep(2)
    start_watcher()
    start_maintenance_scheduler()
    start_expiry_scheduler()

    yield

    stop_expiry_scheduler()
    stop_maintenance_scheduler()
    stop_watcher()


app = FastAPI(
    title="BilwaCorp Fleet Hub",
    version=APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    logger.error("Unhandled exception on %s %s (request_id=%s)", request.method, request.url.path, request_id, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": request_id})


@app.get("/api/health")
async def health():
    return {"status": "ok"}


app.include_router(auth.router, prefix=PREFIX)
app.include_router(register.router, prefix=PREFIX)
app.include_router(ingest.router, prefix=PREFIX)
app.include_router(deployments.router, prefix=PREFIX)
app.include_router(tickets.router, prefix=PREFIX)
app.include_router(maintenance.router, prefix=PREFIX)
app.include_router(users.router, prefix=PREFIX)
app.include_router(notifications.router, prefix=PREFIX)
app.include_router(rbac.router, prefix=PREFIX)
app.include_router(events.router, prefix=PREFIX)
app.include_router(workflow_api.router, prefix=PREFIX)
app.include_router(workflow_api.instances_router, prefix=PREFIX)
app.include_router(rules_api.router, prefix=PREFIX)
app.include_router(approvals_api.router, prefix=PREFIX)
