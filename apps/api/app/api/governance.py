"""Audit log, OpenShell policy view, security probes, runtime/system info, demo reset."""

from __future__ import annotations

import os
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import Container
from app.db.models import AgentEvent, AgentTask, Approval, AuditLog, RebookingPlan, RebookingPlanItem
from app.security.openshell_logs import parse_openshell_line
from app.seed.loader import seed_database

router = APIRouter(prefix="/api", tags=["governance"])


def audit_view(a: AuditLog) -> dict:
    return {
        "id": a.id,
        "timestamp": a.timestamp.isoformat(),
        "agent": a.agent,
        "tool": a.tool,
        "target": a.target,
        "action": a.action,
        "policy": a.policy,
        "result": a.result,
        "enforced_by": a.enforced_by,
        "task_id": a.task_id,
        "details": a.details,
    }


@router.get("/audit")
def list_audit(
    task_id: str | None = None, result: str | None = None, limit: int = 200, c: Container = Depends(get_container)
) -> dict:
    rows = c.audit.list(limit=min(limit, 1000), task_id=task_id, result=result)
    return {"count": len(rows), "entries": [audit_view(r) for r in rows]}


class OpenShellLogIngest(BaseModel):
    lines: list[str] = Field(min_length=1, max_length=5000)
    sandbox: str = "reroute-agent"


@router.post("/audit/openshell")
def ingest_openshell_logs(body: OpenShellLogIngest, c: Container = Depends(get_container)) -> dict:
    """Import decisions made by a real NVIDIA OpenShell sandbox (`openshell logs <name>`) into the audit log."""
    imported = 0
    for line in body.lines:
        ev = parse_openshell_line(line)
        if ev is None:
            continue
        c.audit.record(
            agent=body.sandbox,
            tool=ev["binary"] or "sandbox",
            target=ev["target"],
            action=ev["action"],
            policy=ev["policy"],
            result=ev["result"],
            enforced_by="openshell",
            timestamp=ev["timestamp"],
            details={"severity": ev["severity"], "raw": ev["raw"]},
        )
        imported += 1
    return {"imported": imported, "skipped": len(body.lines) - imported}


# ---------------------------------------------------------------------- security
PRESET_PROBES = [
    {
        "id": "flight-api",
        "label": "GET airline-service /api/flights/KE123",
        "kind": "network",
        "method": "GET",
        "url": "http://airline-service:8000/api/flights/KE123",
        "expect": "ALLOW",
    },
    {
        "id": "optimize",
        "label": "POST reroute-api /api/optimization/rebooking",
        "kind": "network",
        "method": "POST",
        "url": "http://reroute-api:8000/api/optimization/rebooking",
        "expect": "ALLOW",
    },
    {
        "id": "nim",
        "label": "POST integrate.api.nvidia.com /v1/chat/completions",
        "kind": "network",
        "method": "POST",
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "expect": "ALLOW",
    },
    {
        "id": "exfil",
        "label": "GET https://unknown-external-api.com/passengers",
        "kind": "network",
        "method": "GET",
        "url": "https://unknown-external-api.com/passengers",
        "expect": "DENY",
    },
    {
        "id": "self-approve",
        "label": "POST reroute-api /api/rebooking/plans/{id}/approve (agent self-approval)",
        "kind": "network",
        "method": "POST",
        "url": "http://reroute-api:8000/api/rebooking/plans/any/approve",
        "expect": "DENY",
    },
    {"id": "ssh-key", "label": "cat ~/.ssh/id_rsa", "kind": "file", "path": "~/.ssh/id_rsa", "expect": "DENY"},
    {
        "id": "env-secrets",
        "label": "cat /run/secrets/approval_signing_secret (credential theft)",
        "kind": "file",
        "path": "/run/secrets/approval_signing_secret",
        "expect": "DENY",
    },
    {
        "id": "policy-docs",
        "label": "read /app/documents/connection-policy.md",
        "kind": "file",
        "path": "/app/documents/connection-policy.md",
        "expect": "ALLOW",
    },
]


class Probe(BaseModel):
    kind: Literal["network", "file"]
    method: str = "GET"
    url: str | None = None
    path: str | None = None
    write: bool = False


@router.get("/security/policy")
def security_policy(c: Container = Depends(get_container)) -> dict:
    return {"runtime": c.settings.security_runtime, **c.policy.summary(), "probes": PRESET_PROBES}


@router.post("/security/probes")
def run_probe(probe: Probe, c: Container = Depends(get_container)) -> dict:
    """Simulate an agent action and evaluate it against the sandbox policy. Nothing is actually sent/read."""
    if probe.kind == "network":
        if not probe.url:
            raise HTTPException(422, "url required")
        d = c.http.check(probe.method, probe.url, tool="security-probe", task_id=None)
        target = f"{probe.method.upper()} {probe.url}"
    else:
        if not probe.path:
            raise HTTPException(422, "path required")
        d = c.policy.check_file(probe.path, write=probe.write)
        target = f"{'write' if probe.write else 'read'} {probe.path}"
        c.audit.record(
            agent=c.settings.agent_name,
            tool="security-probe",
            target=target,
            action="filesystem.open",
            policy=d.policy,
            result=d.result,
            enforced_by=c.settings.security_runtime,
            details={"reason": d.reason, "home": os.path.expanduser("~")},
        )
    return {
        "target": target,
        "result": d.result,
        "policy": d.policy,
        "reason": d.reason,
        "enforced_by": c.settings.security_runtime,
    }


# ---------------------------------------------------------------------- system
@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/system/runtime")
async def runtime(c: Container = Depends(get_container)) -> dict:
    info = c.runtime_info()
    if c.cuopt is not None:
        info["optimizer"]["health"] = await c.cuopt.health()
    return info


@router.post("/demo/reset")
def demo_reset(c: Container = Depends(get_container)) -> dict:
    if not c.settings.allow_demo_reset:
        raise HTTPException(403, "demo reset disabled")
    with c.db.session() as s:
        for model in (AuditLog, AgentEvent, Approval, RebookingPlanItem, RebookingPlan, AgentTask):
            s.query(model).delete()
        s.commit()
        if c.settings.app_role in ("all", "airline"):
            result = seed_database(s, c.settings.seed_path, c.settings.demo_service_date, reset=True)
    if c.settings.app_role == "control-plane":
        # Airline data is owned by the airline service - ask it to reseed its own tables.
        r = httpx.post(f"{c.settings.airline_api_base_url.rstrip('/')}/api/demo/reset", timeout=30)
        r.raise_for_status()
        result = r.json()
    return {"reset": True, **result}
