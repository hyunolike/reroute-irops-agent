"""Standalone ReRoute agent worker - the process that runs inside the NVIDIA OpenShell sandbox.

    AGENT_WORKER_TOKEN=... REROUTE_API_BASE_URL=http://reroute-api:8000 python -m app.agent.worker

Claims queued tasks from the control plane, plans with Nemotron (NIM), calls tools over HTTP, and
reports every event back. It has no DB credentials and no approval-signing key.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.agent.orchestrator import AgentOrchestrator
from app.agent.remote import ControlPlaneClient, RemoteAgentRepository, RemoteApprovalGateway, RemoteAuditService
from app.config import Settings, get_settings
from app.domain.enums import Component
from app.providers.llm.factory import build_llm
from app.security.governed_http import GovernedHttpClient
from app.security.policy import OpenShellPolicy
from app.tools.catalog import build_tool_registry

log = logging.getLogger("reroute.worker")


class AgentWorker:
    def __init__(self, settings: Settings, *, internal_transport: httpx.AsyncBaseTransport | None = None) -> None:
        if settings.agent_worker_token is None:
            raise RuntimeError("AGENT_WORKER_TOKEN is required for the remote agent worker")
        self.settings = settings
        policy = OpenShellPolicy.load(settings.openshell_policy)
        self.cp = ControlPlaneClient(
            settings.reroute_api_base_url,
            settings.agent_worker_token.get_secret_value(),
            policy,
            security_runtime=settings.security_runtime,
        )
        audit = RemoteAuditService(self.cp)
        http = GovernedHttpClient(
            policy,
            audit,  # type: ignore[arg-type]
            services={"airline-service": settings.airline_api_base_url, "reroute-api": settings.reroute_api_base_url},
            logical_ports={"airline-service": 8000, "reroute-api": 8000},
            runtime=settings.security_runtime,
            agent=settings.agent_name,
            transport=internal_transport,
            timeout=settings.nim_timeout_seconds,
        )
        llm, reason = build_llm(settings, http)
        log.info("reasoning model: %s", reason)
        tools = build_tool_registry(settings.exception_resolution_mode)
        # Tool badges reflect the providers the control plane ACTUALLY runs (retriever / solver)
        overrides = {k: Component(v) for k, v in self._control_plane_runtime()["component_overrides"].items()}
        self.orchestrator = AgentOrchestrator(
            repo=RemoteAgentRepository(self.cp),  # type: ignore[arg-type]
            llm=llm,
            tools=tools,
            gateway=RemoteApprovalGateway(self.cp),  # type: ignore[arg-type]
            http=http,
            agent_name=settings.agent_name,
            security_component=Component.OPENSHELL if settings.security_runtime == "openshell" else Component.POLICY_MIRROR,
            component_overrides=overrides,
            step_delay_ms=settings.agent_step_delay_ms,
            max_steps=settings.agent_max_steps,
            resolution_mode=settings.exception_resolution_mode,
        )

    def _control_plane_runtime(self, max_delay: float = 30.0) -> dict:
        """The worker and the control plane start together (host boot, redeploy), so wait for it instead of exiting.

        Only "not up yet" is retried - connection errors and 5xx. A 4xx (wrong worker token) or a policy violation
        is a configuration error and fails fast.
        """
        delay = 1.0
        while True:
            try:
                r = self.cp.request("GET", "/internal/agent/runtime")
                if r.status_code < 500:
                    r.raise_for_status()
                    return r.json()
                reason = f"HTTP {r.status_code}"
            except httpx.TransportError as e:
                reason = type(e).__name__
            log.warning("control plane not ready (%s); retrying in %.0fs", reason, delay)
            time.sleep(delay)
            delay = min(delay * 2, max_delay)

    async def run_once(self) -> bool:
        r = self.cp.request("POST", "/internal/agent/tasks/claim")
        if r.status_code == 204:
            return False
        r.raise_for_status()
        job = r.json()
        task_id = job["task"]["id"]
        log.info("claimed %s (%s)", task_id, job["action"])
        if job["action"] == "run":
            await self.orchestrator.run(task_id)
        elif job["action"] == "resume":
            await self.orchestrator.resume_after_approval(task_id, job["task"]["plan_id"])
        return True

    async def serve(self) -> None:
        log.info("ReRoute agent worker started (llm=%s, security=%s)", self.orchestrator.llm.name, self.settings.security_runtime)
        while True:
            try:
                worked = await self.run_once()
            except Exception:  # noqa: BLE001 - keep the worker alive
                log.exception("worker iteration failed")
                worked = False
            if not worked:
                await asyncio.sleep(self.settings.worker_poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(AgentWorker(get_settings()).serve())


if __name__ == "__main__":
    main()
