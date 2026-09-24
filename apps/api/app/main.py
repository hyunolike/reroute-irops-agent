"""FastAPI entrypoint. APP_ROLE selects which bounded contexts this process serves."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agent, airline, governance, platform, rebooking
from app.config import Settings, get_settings
from app.container import Container
from app.seed.loader import seed_database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, *, internal_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c: Container = app.state.container
        c.db.create_all()
        if settings.seed_on_startup and settings.app_role in ("all", "airline"):
            with c.db.session() as s:
                logging.getLogger("reroute").info("seed: %s", seed_database(s, settings.seed_path, settings.demo_service_date))
        yield
        await c.runner.drain()

    app = FastAPI(
        title="ReRoute API",
        description="Autonomous Airline Disruption Recovery Agent - NVIDIA Nemotron/NIM, NeMo Retriever, cuOpt, OpenShell",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.container = Container(settings, internal_transport=internal_transport)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if settings.app_role in ("all", "airline"):
        app.include_router(airline.router)
    if settings.app_role in ("all", "control-plane"):
        app.include_router(agent.router)
        app.include_router(platform.router)
        app.include_router(rebooking.router)
    app.include_router(governance.router)
    return app
