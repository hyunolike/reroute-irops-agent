"""OpenClaw bridge API: the NemoClaw host takes dashboard instructions meant for OpenClaw and relays its answer.

The bridge (infra/openclaw-bridge/bridge.py) runs next to NemoClaw and only makes outbound HTTPS calls here, so the
NemoClaw host needs no open port. It authenticates with the same bearer token NemoClaw already holds for ReRoute's
MCP server (REROUTE_MCP_TOKEN). OpenClaw itself then plans the task over MCP; nothing here can approve or execute.
"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.agent import task_view
from app.api.deps import get_container
from app.container import Container

router = APIRouter(prefix="/api/bridge/openclaw", tags=["openclaw-bridge"])


def bridge_auth(authorization: str = Header(default=""), c: Container = Depends(get_container)) -> Container:
    token = c.settings.mcp_bearer_token
    if token is None:
        raise HTTPException(404, "OpenClaw bridge is disabled (REROUTE_MCP_TOKEN is not set)")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value, token.get_secret_value()):
        raise HTTPException(401, "invalid bridge token")
    return c


class Heartbeat(BaseModel):
    sandbox: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)


class Reply(BaseModel):
    text: str = Field(default="", max_length=20000)
    ok: bool = True


def _seen(c: Container, hb: Heartbeat | None) -> None:
    prev = c.openclaw_bridge or {}
    c.openclaw_bridge = {
        "last_seen": time.time(),
        "sandbox": (hb.sandbox if hb else None) or prev.get("sandbox"),
        "model": (hb.model if hb else None) or prev.get("model"),
    }


@router.post("/heartbeat")
def heartbeat(body: Heartbeat, c: Container = Depends(bridge_auth)) -> dict:
    _seen(c, body)
    return c.openclaw_status()


@router.post("/claim")
def claim(c: Container = Depends(bridge_auth)):
    """Oldest dashboard instruction queued for OpenClaw, or 204. Also counts as a heartbeat."""
    _seen(c, None)
    claimed = c.repo.claim_pending(("openclaw",))
    if claimed is None:
        return Response(status_code=204)
    task, _ = claimed
    return {"task": task_view(task)}


@router.post("/tasks/{task_id}/reply")
def reply(task_id: str, body: Reply, c: Container = Depends(bridge_auth)) -> dict:
    """OpenClaw's final message to the operator; a failed run fails the task unless a plan was already proposed."""
    task = c.repo.get_task(task_id)
    if task is None or (task.runtime or {}).get("requested_agent") != "openclaw":
        raise HTTPException(404, "no OpenClaw task with that id")
    c.orchestrator.external_reply(task_id, body.text, body.ok)
    return task_view(c.repo.get_task(task_id))
