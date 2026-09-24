"""Composition root: builds every adapter from Settings (dependency injection by construction)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
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
from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.nim import NvidiaNimProvider
from app.rag.documents import load_policy_chunks
from app.rag.lexical import LexicalRetrieverProvider
from app.rag.nvidia import NvidiaRetrieverProvider
from app.repositories.agent import AgentRepository
from app.security.governed_http import GovernedHttpClient
from app.security.policy import OpenShellPolicy
from app.services.knowledge import PolicyKnowledgeService
from app.services.optimization import OptimizationService
from app.tools.airline import GetAffectedPassengers, GetDisruptedFlight, SearchAlternativeFlights
from app.tools.base import ToolRegistry
from app.tools.knowledge import SearchRebookingPolicy
from app.tools.optimization import OptimizeRebooking
from app.tools.rebooking import ExecuteRebooking, ProposeRebooking

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

        # --- reasoning model
        self.llm: LLMProvider
        if settings.llm_provider == "nvidia" and key:
            self.llm = NvidiaNimProvider(
                self.http,
                key,
                settings.nim_base_url,
                settings.nim_model,
                temperature=settings.nim_temperature,
                enable_thinking=settings.nim_enable_thinking,
            )
        else:
            if settings.llm_provider == "nvidia":
                log.warning("LLM_PROVIDER=nvidia but NVIDIA_API_KEY is not set - using mock planner")
            self.llm = MockLLMProvider()

        self.tools = ToolRegistry(
            [
                GetDisruptedFlight(),
                GetAffectedPassengers(),
                SearchAlternativeFlights(),
                SearchRebookingPolicy(),
                OptimizeRebooking(),
                ProposeRebooking(),
                ExecuteRebooking(),
            ]
        )
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
        )

    def runtime_info(self) -> dict[str, Any]:
        s = self.settings
        return {
            "demo_mode": s.demo_mode,
            "app_role": s.app_role,
            "llm": {"provider": self.llm.name, "model": self.llm.model, "nvidia": self.llm.nvidia},
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
                "policy_file": str(s.openshell_policy.relative_to(s.project_root))
                if s.openshell_policy.is_relative_to(s.project_root)
                else str(s.openshell_policy),
                "enforced_by": "NVIDIA OpenShell sandbox"
                if s.security_runtime == "openshell"
                else "ReRoute policy mirror (same OpenShell policy file, in-process)",
            },
            "approval": {"ttl_minutes": s.approval_ttl_minutes, "required_for": ["execute_rebooking"]},
            "agent": {"execution": s.agent_execution},
            "tools": [{"name": t, "mutating": self.tools.get(t).mutating} for t in self.tools.names()],
        }
