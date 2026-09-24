"""Tool framework: typed arguments, explicit preconditions, state mapping, audit-friendly results."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel

from app.domain.enums import AgentState, Component
from app.domain.models import AffectedPassengerDTO, FlightDTO, OptimizationResult, PolicyHit
from app.security.governed_http import GovernedHttpClient


@dataclass
class AgentMemory:
    flight: FlightDTO | None = None
    flight_assessment: str | None = None  # "recover" | "no_recovery" | "check_policy"
    flight_lookup_failed: bool = False
    passengers: list[AffectedPassengerDTO] = field(default_factory=list)
    alternatives: list[FlightDTO] = field(default_factory=list)
    policy_hits: dict[str, PolicyHit] = field(default_factory=dict)
    policy_queries: list[str] = field(default_factory=list)
    optimization: OptimizationResult | None = None
    exception_analyses: dict[str, dict[str, Any]] = field(default_factory=dict)
    briefing: str | None = None
    plan_id: str | None = None
    approval_id: str | None = None


@dataclass
class ToolContext:
    task_id: str
    agent: str
    memory: AgentMemory
    http: GovernedHttpClient
    gateway: Any  # ApprovalGateway (in-process) - typed loosely to avoid an import cycle
    write_briefing: Callable[[], Awaitable[str]] | None = None


@dataclass
class ToolResult:
    title: str
    llm_view: dict[str, Any]
    detail: dict[str, Any] = field(default_factory=dict)
    component: Component | None = None


class ToolError(RuntimeError):
    pass


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]]
    state: ClassVar[AgentState]
    component: ClassVar[Component]
    mutating: ClassVar[bool] = False
    requires_approval: ClassVar[bool] = False

    def schema(self) -> dict[str, Any]:
        params = self.Args.model_json_schema()
        params.pop("title", None)
        for p in params.get("properties", {}).values():
            p.pop("title", None)
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": params}}

    def precondition(self, ctx: ToolContext) -> str | None:
        return None

    @abstractmethod
    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self, *, include_mutating: bool = True) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values() if include_mutating or not t.mutating]

    def names(self) -> list[str]:
        return list(self._tools)
