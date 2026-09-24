"""HTTP adapters that let the agent orchestrator run in a separate, sandboxed worker process.

They implement the same methods the orchestrator/tools use on AgentRepository, ApprovalGateway and
AuditService, but talk to the control plane's /internal/agent API. The worker therefore needs no
database credentials and never sees the approval-signing secret.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.approval.gateway import ApprovalConflict, ApprovalRequired, ExecutionGrant
from app.domain.models import OptimizationResult, PolicyHit
from app.security.governed_http import PolicyViolation
from app.security.policy import OpenShellPolicy


def _ns(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_ns(v) for v in value]
    return value


class ControlPlaneClient:
    """Sync client for the internal API. Every call is checked against the sandbox policy."""

    def __init__(
        self,
        base_url: str,
        token: str,
        policy: OpenShellPolicy,
        *,
        logical_host: str = "reroute-api",
        logical_port: int = 8000,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.policy = policy
        self.logical = (logical_host, logical_port)
        self.http = httpx.Client(timeout=timeout, headers={"X-Agent-Worker-Token": token})

    def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        d = self.policy.check_network(method, *self.logical, urlsplit(path).path)
        if not d.allowed:
            raise PolicyViolation(d, f"{method} {self.logical[0]}{path}")
        return self.http.request(method, f"{self.base_url}{path}", **kw)


class RemoteAgentRepository:
    def __init__(self, cp: ControlPlaneClient) -> None:
        self.cp = cp

    def get_task(self, task_id: str):
        r = self.cp.request("GET", f"/internal/agent/tasks/{task_id}")
        return _ns(r.json()) if r.status_code == 200 else None

    def update_task(self, task_id: str, **fields: Any):
        r = self.cp.request("PATCH", f"/internal/agent/tasks/{task_id}", json=fields)
        r.raise_for_status()
        return _ns(r.json())

    def add_event(self, task_id: str, **event: Any):
        r = self.cp.request("POST", f"/internal/agent/tasks/{task_id}/events", json=event)
        r.raise_for_status()
        return _ns(r.json())


class RemoteAuditService:
    def __init__(self, cp: ControlPlaneClient) -> None:
        self.cp = cp

    def record(self, **entry: Any):
        entry.pop("timestamp", None)
        r = self.cp.request("POST", "/internal/agent/audit", json=entry)
        r.raise_for_status()
        return _ns(r.json())


class RemoteApprovalGateway:
    def __init__(self, cp: ControlPlaneClient) -> None:
        self.cp = cp

    def create_plan(
        self,
        *,
        task_id: str,
        flight_no: str,
        result: OptimizationResult,
        policy_hits: list[PolicyHit],
        explanation: str,
        requested_by: str,
    ):
        body = {
            "task_id": task_id,
            "flight_no": flight_no,
            "result": result.model_dump(mode="json"),
            "policy_hits": [h.model_dump() for h in policy_hits],
            "explanation": explanation,
            "requested_by": requested_by,
        }
        r = self.cp.request("POST", "/internal/agent/plans", json=body)
        r.raise_for_status()
        data = r.json()
        approval = _ns(data["approval"])
        approval.expires_at = datetime.fromisoformat(data["approval"]["expires_at"])
        return _ns(data["plan"]), approval

    def get_plan(self, plan_id: str):
        r = self.cp.request("GET", f"/internal/agent/plans/{plan_id}")
        r.raise_for_status()
        return _ns(r.json())

    def get_approval(self, plan_id: str):
        return self.get_plan(plan_id).approval

    def authorize_execution(self, plan_id: str) -> ExecutionGrant:
        r = self.cp.request("POST", f"/internal/agent/plans/{plan_id}/execution-grant")
        if r.status_code == 403:
            raise ApprovalRequired(r.json()["detail"]["message"])
        if r.status_code == 409:
            raise ApprovalConflict(r.json()["detail"]["message"])
        r.raise_for_status()
        g = r.json()
        return ExecutionGrant(g["plan_id"], g["approval_id"], g["approved_by"], g["items"], g["token"])

    def record_execution(self, plan_id: str, results: list[dict[str, Any]]):
        r = self.cp.request("POST", f"/internal/agent/plans/{plan_id}/execution", json={"results": results})
        r.raise_for_status()
        return _ns(r.json())
