"""All agent egress goes through this client: policy check -> audit -> request.

Internal services are addressed by their *logical* name (as seen inside the OpenShell deployment,
e.g. `airline-service:8000`), and resolved to the real base URL of the current environment.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from app.audit.service import AuditService
from app.security.policy import Decision, OpenShellPolicy


class PolicyViolation(PermissionError):
    def __init__(self, decision: Decision, target: str) -> None:
        super().__init__(f"DENY {target}: {decision.reason} [policy:{decision.policy}]")
        self.decision = decision
        self.target = target


class GovernedHttpClient:
    def __init__(
        self,
        policy: OpenShellPolicy,
        audit: AuditService,
        *,
        services: dict[str, str],
        logical_ports: dict[str, int],
        runtime: str,
        agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
        external_transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.policy = policy
        self.audit = audit
        self.services = services
        self.logical_ports = logical_ports
        self.runtime = runtime
        self.agent = agent
        self.transport = transport
        self.external_transport = external_transport
        self.timeout = timeout

    def check(self, method: str, url: str, *, tool: str, task_id: str | None, service: str | None = None) -> Decision:
        parts = urlsplit(url)
        host = service or parts.hostname or ""
        port = self.logical_ports.get(service, 0) if service else (parts.port or (443 if parts.scheme == "https" else 80))
        path = parts.path or "/"
        decision = self.policy.check_network(method, host, port, path)
        self.audit.record(
            agent=self.agent,
            tool=tool,
            target=f"{method.upper()} {parts.scheme or 'http'}://{host}:{port}{path}",
            action="network.egress",
            policy=decision.policy,
            result=decision.result,
            enforced_by=self.runtime,
            task_id=task_id,
            details={"reason": decision.reason},
        )
        return decision

    async def request(
        self,
        method: str,
        path_or_url: str,
        *,
        tool: str,
        task_id: str | None = None,
        service: str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        url = f"{self.services[service].rstrip('/')}{path_or_url}" if service else path_or_url
        decision = self.check(method, url, tool=tool, task_id=task_id, service=service)
        if not decision.allowed:
            raise PolicyViolation(decision, url)
        transport = self.transport if service else self.external_transport
        async with httpx.AsyncClient(timeout=self.timeout, transport=transport) as client:
            return await client.request(method, url, headers=headers, **kwargs)
