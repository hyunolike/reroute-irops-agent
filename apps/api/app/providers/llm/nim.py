"""NVIDIA NIM adapter for Nemotron (OpenAI-compatible Chat Completions on build.nvidia.com or a
self-hosted NIM).

POST {base}/chat/completions  Authorization: Bearer $NVIDIA_API_KEY
Tool calling: `tools` + `tool_choice: "auto"`; results come back as `message.tool_calls`.
Reasoning toggle: Nemotron 3 models use `chat_template_kwargs.enable_thinking`;
Llama-3.3-Nemotron-Super-49B-v1.5 / Nemotron-Nano-9B-v2 use a "/think" | "/no_think" system prompt tag.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.providers.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall
from app.security.governed_http import GovernedHttpClient


class NvidiaNimProvider(LLMProvider):
    name = "nvidia-nim"
    nvidia = True

    def __init__(
        self,
        http: GovernedHttpClient,
        api_key: str,
        base_url: str,
        model: str,
        *,
        temperature: float = 0.0,
        enable_thinking: bool = False,
    ) -> None:
        if not api_key:
            raise LLMError("NVIDIA_API_KEY is required for LLM_PROVIDER=nvidia")
        self.http = http
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.enable_thinking = enable_thinking

    def _prepare(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        extra: dict[str, Any] = {}
        msgs = [dict(m) for m in messages]
        if "nemotron-3" in self.model:
            extra["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}
        elif "nemotron" in self.model and msgs and msgs[0]["role"] == "system":
            tag = "/think" if self.enable_thinking else "/no_think"
            msgs[0]["content"] = f"{tag}\n{msgs[0]['content']}"
        return msgs, extra

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        task_id: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        msgs, extra = self._prepare(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            **extra,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        start = time.perf_counter()
        r = await self.http.request(
            "POST",
            f"{self.base_url}/chat/completions",
            tool="llm.chat",
            task_id=task_id,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
            json=body,
        )
        latency = (time.perf_counter() - start) * 1000
        if r.status_code != 200:
            raise LLMError(f"NIM {r.status_code}: {r.text[:300]}")
        data = r.json()
        msg = data["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            raw = tc["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except json.JSONDecodeError as e:
                raise LLMError(f"invalid tool arguments from model: {raw[:200]}") from e
            calls.append(
                ToolCall(id=tc.get("id") or f"call_{len(calls)}", name=tc["function"]["name"], arguments=args, raw_arguments=raw)
            )
        return LLMResponse(
            content=msg.get("content"),
            tool_calls=calls,
            model=data.get("model", self.model),
            reasoning=msg.get("reasoning_content") or msg.get("reasoning"),
            usage=data.get("usage") or {},
            latency_ms=latency,
        )
