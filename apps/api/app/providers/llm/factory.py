"""Chooses the reasoning model. The real agent is Nemotron via NIM; the scripted planner is only used when
no NVIDIA key is configured (or explicitly requested) and is always labelled as such."""

from __future__ import annotations

import logging

from app.config import Settings
from app.providers.llm.base import LLMProvider
from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.nim import NvidiaNimProvider
from app.security.governed_http import GovernedHttpClient

log = logging.getLogger("reroute")


def build_llm(settings: Settings, http: GovernedHttpClient) -> tuple[LLMProvider, str]:
    """Returns (provider, reason) - the reason is shown in the dashboard and runtime API."""
    key = settings.nvidia_api_key.get_secret_value().strip() if settings.nvidia_api_key else ""
    if settings.llm_provider == "mock":
        return MockLLMProvider(), "LLM_PROVIDER=mock (scripted planner requested)"
    if not key:
        reason = "NVIDIA_API_KEY is not set - scripted planner in use; set the key to run Nemotron"
        log.warning(reason)
        return MockLLMProvider(), reason
    provider = NvidiaNimProvider(
        http,
        key,
        settings.nim_base_url,
        settings.nim_model,
        temperature=settings.nim_temperature,
        enable_thinking=settings.nim_enable_thinking,
        max_retries=settings.nim_max_retries,
    )
    return provider, f"Nemotron via NIM ({settings.nim_model})"
