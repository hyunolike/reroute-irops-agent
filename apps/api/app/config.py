"""Application settings. Every credential comes from the environment - never from code."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ReRoute"
    project_root: Path = Field(default=_DEFAULT_ROOT)
    database_url: str = "postgresql+psycopg://reroute:reroute@localhost:5432/reroute"
    seed_on_startup: bool = True
    # Service date for the seeded schedule (defaults to "today" in Asia/Seoul).
    demo_service_date: date | None = None

    # Demo mode = deterministic seed + deterministic mock responses. Never claims NVIDIA usage.
    demo_mode: bool = False

    # --- Service topology (the agent reaches domain systems over HTTP only) ---
    # Process role: "all" (single process), "airline" (Mock Airline API only), "control-plane" (agent + services)
    app_role: Literal["all", "airline", "control-plane"] = "all"
    airline_api_base_url: str = "http://localhost:8000"
    reroute_api_base_url: str = "http://localhost:8000"
    agent_name: str = "reroute-agent"
    # "inline": the API process runs the agent. "remote": a separate worker (e.g. inside an OpenShell
    # sandbox) claims tasks over HTTP and has no database access and no approval-signing secret.
    agent_execution: Literal["inline", "remote"] = "inline"
    agent_worker_token: SecretStr | None = None
    worker_poll_seconds: float = 1.0
    # Cosmetic pause after each tool result so a live demo timeline is readable (0 = off)
    agent_step_delay_ms: int = 0

    # --- LLM: NVIDIA NIM (Nemotron) or deterministic mock ---
    llm_provider: Literal["nvidia", "mock"] = "mock"
    nvidia_api_key: SecretStr | None = None
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_model: str = "nvidia/nemotron-3-super-120b-a12b"
    # Nemotron 3 family: chat_template_kwargs.enable_thinking; Llama-Nemotron v1.5: "/think" | "/no_think"
    nim_enable_thinking: bool = False
    nim_timeout_seconds: float = 60.0
    nim_temperature: float = 0.0

    # --- Retrieval: NeMo Retriever NIMs or local lexical index ---
    retriever_provider: Literal["nvidia", "lexical"] = "lexical"
    nim_embedding_model: str = "nvidia/llama-nemotron-embed-1b-v2"
    nim_embedding_url: str = "https://integrate.api.nvidia.com/v1/embeddings"
    nim_rerank_model: str = "nvidia/llama-nemotron-rerank-1b-v2"
    nim_rerank_url: str = "https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-nemotron-rerank-1b-v2/reranking"

    # --- Optimization: NVIDIA cuOpt server or CPU fallback (HiGHS via SciPy) ---
    optimization_provider: Literal["cuopt", "fallback"] = "fallback"
    cuopt_base_url: str = "http://localhost:5000"
    cuopt_time_limit_seconds: float = 10.0
    cuopt_poll_timeout_seconds: float = 60.0
    optimization_config_path: Path | None = None
    # If cuOpt is unreachable, solve with the CPU fallback and label the result as such (never as cuOpt).
    optimization_allow_fallback: bool = True

    # --- Security runtime ---
    # "openshell": the agent runs inside an NVIDIA OpenShell sandbox (enforced by OpenShell).
    # "policy-mirror": the same policy file is evaluated in-process (demo / dev only).
    security_runtime: Literal["openshell", "policy-mirror"] = "policy-mirror"
    openshell_policy_path: Path | None = None

    # --- Human approval (business authorization boundary) ---
    approval_signing_secret: SecretStr = SecretStr("dev-only-change-me")
    approval_ttl_minutes: int = 30

    documents_dir: Path | None = None
    seed_dir: Path | None = None
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    allow_demo_reset: bool = True

    @property
    def docs_path(self) -> Path:
        return self.documents_dir or self.project_root / "documents"

    @property
    def seed_path(self) -> Path:
        return self.seed_dir or self.project_root / "data" / "seed"

    @property
    def optimization_config(self) -> Path:
        return self.optimization_config_path or self.project_root / "config" / "optimization.yaml"

    @property
    def openshell_policy(self) -> Path:
        return self.openshell_policy_path or self.project_root / "nvidia" / "openshell" / "policies" / "reroute-agent.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
