from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    reasoning: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0


class LLMProvider(ABC):
    """Port for the reasoning model. NVIDIA NIM (Nemotron) is the production adapter."""

    name: str
    nvidia: bool
    model: str

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        task_id: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse: ...
