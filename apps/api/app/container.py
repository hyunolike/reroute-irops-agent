"""Composition root: builds every adapter from Settings (dependency injection by construction)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

import httpx

from app.agent.orchestrator import AgentOrchestrator
from app.approval.gateway import ApprovalGateway
from app.audit.service import AuditService
from app.config import Settings
from app.db.base import Database
from app.domain.enums import Component
from app.optimization.config import OptimizationConfig
from app.optimization.cuopt import CuOptOptimizationProvider
from app.optimization.fallback import FallbackOptimizationProvider
from app.optimization.service import RebookingOptimizer
from app.providers.llm.base import LLMProvider
from app.providers.llm.factory import build_llm
from app.rag.documents import load_policy_chunks
from app.rag.lexical import LexicalRetrieverProvider
from app.rag.nvidia import NvidiaRetrieverProvider
from app.repositories.agent import AgentRepository
from app.security.governed_http import GovernedHttpClient
from app.security.policy import OpenShellPolicy
from app.services.knowledge import PolicyKnowledgeService
from app.services.optimization import OptimizationService
from app.tools.catalog import build_tool_registry

log = logging.getLogger("reroute")


class TaskRunner:
    """Runs agent coroutines in the background and keeps references so they are not GC'd."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


class Container:
    def __init__(self, settings: Settings, *, internal_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.db = Database(settings.database_url)
        self.audit = AuditService(self.db)
        self.repo = AgentRepository(self.db)
        self.gateway = ApprovalGateway(
            self.db, settings.approval_signing_secret.get_secret_value(), settings.approval_ttl_minutes
        )
        self.runner = TaskRunner()

        # --- security runtime (same policy file for OpenShell and the in-process mirror)
        self.policy = OpenShellPolicy.load(settings.openshell_policy)
        self.security_component = Component.OPENSHELL if settings.security_runtime == "openshell" else Component.POLICY_MIRROR
        self.http = GovernedHttpClient(
            self.policy,
            self.audit,
            services={
                "airline-service": settings.airline_api_base_url,
                "reroute-api": settings.reroute_api_base_url,
            },
            logical_ports={"airline-service": 8000, "reroute-api": 8000},
            runtime=settings.security_runtime,
            agent=settings.agent_name,
            transport=internal_transport,
            timeout=settings.nim_timeout_seconds,
        )

        # --- knowledge (RAG)
        chunks = load_policy_chunks(settings.docs_path)
        self.lexical = LexicalRetrieverProvider(chunks)
        key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else ""
        primary_retriever = self.lexical
        if settings.retriever_provider == "nvidia" and key:
            primary_retriever = NvidiaRetrieverProvider(
                chunks,
                key,
                settings.nim_embedding_url,
                settings.nim_embedding_model,
                settings.nim_rerank_url,
                settings.nim_rerank_model,
            )
        elif settings.retriever_provider == "nvidia":
            log.warning("RETRIEVER_PROVIDER=nvidia but NVIDIA_API_KEY is not set - using lexical retriever")
        self.knowledge = PolicyKnowledgeService(primary_retriever, self.lexical)

        # --- optimization
        opt_cfg = OptimizationConfig.load(settings.optimization_config)
        opt_cfg.solver.time_limit_seconds = settings.cuopt_time_limit_seconds
        fallback = RebookingOptimizer(FallbackOptimizationProvider(), opt_cfg)
        if settings.optimization_provider == "cuopt":
            self.cuopt = CuOptOptimizationProvider(settings.cuopt_base_url, settings.cuopt_poll_timeout_seconds)
            primary = RebookingOptimizer(self.cuopt, opt_cfg)
        else:
            self.cuopt = None
            primary = fallback
        self.optimization = OptimizationService(primary, fallback if settings.optimization_allow_fallback else None)

        # --- reasoning model: Nemotron via NIM (scripted planner only without a key, labelled)
        self.llm: LLMProvider
        self.llm, self.llm_reason = build_llm(settings, self.http)

        self.tools = build_tool_registry(settings.exception_resolution_mode)
        self.orchestrator = AgentOrchestrator(
            repo=self.repo,
            llm=self.llm,
            tools=self.tools,
            gateway=self.gateway,
            http=self.http,
            agent_name=settings.agent_name,
            security_component=self.security_component,
            component_overrides={
                "search_rebooking_policy": Component.NEMO_RETRIEVER
                if self.knowledge.primary.nvidia
                else Component.LEXICAL_RETRIEVER,
                "optimize_rebooking": Component.CUOPT if self.optimization.primary.provider.nvidia else Component.FALLBACK_SOLVER,
            },
            step_delay_ms=settings.agent_step_delay_ms,
            max_steps=settings.agent_max_steps,
            resolution_mode=settings.exception_resolution_mode,
        )

    # OpenClaw bridge (NemoClaw host): last heartbeat, kept in the control-plane process
    openclaw_bridge: dict[str, Any] | None = None
    OPENCLAW_STALE_SECONDS = 30

    def openclaw_status(self) -> dict[str, Any]:
        b = self.openclaw_bridge
        if b is None:
            return {"connected": False, "last_seen": None}
        age = time.time() - b["last_seen"]
        return {
            "connected": age < self.OPENCLAW_STALE_SECONDS,
            "last_seen": datetime.fromtimestamp(b["last_seen"], UTC).isoformat(),
            "sandbox": b.get("sandbox"),
            "model": b.get("model"),
        }

    # Remote agent worker: last authenticated call and the security runtime it declared (e.g. "openshell")
    agent_worker: dict[str, Any] | None = None

    def worker_seen(self, security_runtime: str | None) -> None:
        declared = security_runtime if security_runtime in ("openshell", "policy-mirror") else None
        self.agent_worker = {"last_seen": time.time(), "security": declared}

    def worker_status(self) -> dict[str, Any]:
        w = self.agent_worker
        if w is None:
            return {"connected": False, "security": None, "last_seen": None}
        return {
            "connected": time.time() - w["last_seen"] < self.OPENCLAW_STALE_SECONDS,
            "security": w["security"],
            "last_seen": datetime.fromtimestamp(w["last_seen"], UTC).isoformat(),
        }

    def agent_security_runtime(self) -> str:
        """Where ReRoute's agent actually runs: the sandboxed worker's runtime when remote, else this process's."""
        if self.settings.agent_execution == "remote":
            w = self.worker_status()
            if w["connected"] and w["security"]:
                return w["security"]
        return self.settings.security_runtime

    def runtime_info(self) -> dict[str, Any]:
        s = self.settings
        return {
            "demo_mode": s.demo_mode,
            "app_role": s.app_role,
            "llm": {"provider": self.llm.name, "model": self.llm.model, "nvidia": self.llm.nvidia, "reason": self.llm_reason},
            "retriever": {
                "provider": self.knowledge.primary.name,
                "nvidia": self.knowledge.primary.nvidia,
                "models": [s.nim_embedding_model, s.nim_rerank_model] if self.knowledge.primary.nvidia else [],
            },
            "optimizer": {
                "provider": self.optimization.primary.provider.name,
                "nvidia": self.optimization.primary.provider.nvidia,
                "endpoint": s.cuopt_base_url if self.cuopt else None,
                "fallback_enabled": self.optimization.fallback is not None,
            },
            "security": {
                "runtime": s.security_runtime,
                "agent_runtime": self.agent_security_runtime(),
                "policy_file": str(s.openshell_policy.relative_to(s.project_root))
                if s.openshell_policy.is_relative_to(s.project_root)
                else str(s.openshell_policy),
                "enforced_by": "NVIDIA OpenShell sandbox"
                if self.agent_security_runtime() == "openshell"
                else "ReRoute policy mirror (same OpenShell policy file, in-process)",
            },
            "approval": {"ttl_minutes": s.approval_ttl_minutes, "required_for": ["execute_rebooking"]},
            "agent": {"execution": s.agent_execution, "worker": self.worker_status()},
            "openclaw": self.openclaw_status(),
            "tools": [{"name": t, "mutating": self.tools.get(t).mutating} for t in self.tools.names()],
        }
