"""The agent's tool set - one definition for the in-process agent and the sandboxed remote worker."""

from __future__ import annotations

from app.tools.airline import GetAffectedPassengers, GetDisruptedFlight, SearchAlternativeFlights
from app.tools.base import Tool, ToolRegistry
from app.tools.exceptions import ExploreExceptionOptions
from app.tools.knowledge import SearchRebookingPolicy
from app.tools.optimization import OptimizeRebooking
from app.tools.rebooking import ExecuteRebooking, ProposeRebooking
from app.tools.resolution import ProposeExceptionResolution


def build_tool_registry(exception_resolution_mode: str) -> ToolRegistry:
    tools: list[Tool] = [
        GetDisruptedFlight(),
        GetAffectedPassengers(),
        SearchAlternativeFlights(),
        SearchRebookingPolicy(),
        OptimizeRebooking(),
        ExploreExceptionOptions(),
    ]
    if exception_resolution_mode != "off":
        tools.append(ProposeExceptionResolution())
    return ToolRegistry([*tools, ProposeRebooking(), ExecuteRebooking()])
