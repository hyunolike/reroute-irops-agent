from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.container import Container
from app.main import create_app
from app.seed.loader import seed_database

ROOT = Path(__file__).resolve().parents[3]
SERVICE_DATE = date(2026, 10, 15)
OPERATOR = {"X-Operator-Id": "ops.kim"}
COMMAND = "KE123편이 결항됐어. 영향 승객을 확인하고 최적 재배정안을 만들어줘."


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        database_url=f"sqlite:///{tmp_path / 'reroute.db'}",
        project_root=ROOT,
        demo_mode=True,
        demo_service_date=SERVICE_DATE,
        llm_provider="mock",
        retriever_provider="lexical",
        optimization_provider="fallback",
        security_runtime="policy-mirror",
        approval_signing_secret="test-secret",
        airline_api_base_url="http://airline-service:8000",
        reroute_api_base_url="http://reroute-api:8000",
        seed_on_startup=False,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def build_app(settings: Settings):
    app = create_app(settings)
    c: Container = app.state.container
    # All internal service calls (airline-service / reroute-api) are real HTTP requests routed to this app.
    c.http.transport = httpx.ASGITransport(app=app)
    c.db.create_all()
    with c.db.session() as s:
        seed_database(s, settings.seed_path, settings.demo_service_date)
    return app, c


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def app_and_container(settings):
    return build_app(settings)


@pytest.fixture
def container(app_and_container) -> Container:
    return app_and_container[1]


@pytest.fixture
async def client(app_and_container):
    app, _ = app_and_container
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def run_agent_to_approval(client: httpx.AsyncClient, container: Container, command: str = COMMAND) -> dict:
    r = await client.post("/api/agent/tasks", json={"command": command})
    assert r.status_code == 202, r.text
    await container.runner.drain()
    return (await client.get(f"/api/agent/tasks/{r.json()['id']}")).json()
