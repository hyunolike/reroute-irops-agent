"""NVIDIA NIM adapter for Nemotron (OpenAI-compatible Chat Completions on build.nvidia.com or a
self-hosted NIM).

POST {base}/chat/completions  Authorization: Bearer $NVIDIA_API_KEY
Tool calling: `tools` + `tool_choice: "auto"`; results come back as `message.tool_calls`.
Reasoning toggle: Nemotron 3 models use `chat_template_kwargs.enable_thinking`;
Llama-3.3-Nemotron-Super-49B-v1.5 / Nemotron-Nano-9B-v2 use a "/think" | "/no_think" system prompt tag.

Robustness for real model output:
- transient 429 / 5xx / network errors are retried with backoff
- tool calls emitted as text (`<TOOLCALL>[...]</TOOLCALL>`, `<tool_call>{...}</tool_call>` or a bare JSON
  object) are parsed when the server did not return structured `tool_calls`
- `<think>...</think>` blocks are separated from the visible content
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx

from app.providers.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall
from app.security.governed_http import GovernedHttpClient

_THINK = re.compile(r"<think>(.*?)</think>", re.S)
_TEXT_CALLS = [
    re.compile(r"<TOOLCALL>\s*(.*?)\s*</TOOLCALL>", re.S),
    re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S),
]
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def split_thinking(content: str | None) -> tuple[str | None, str | None]:
    if not content:
        return content, None
    thoughts = _THINK.findall(content)
    visible = _THINK.sub("", content)
    if "</think>" in visible:  # opening tag was consumed by the chat template
        head, _, visible = visible.partition("</think>")
        thoughts.insert(0, head)
    visible = visible.strip()
    return (visible or None), ("\n".join(t.strip() for t in thoughts) or None)


def _as_calls(obj: Any) -> list[dict[str, Any]]:
    items = obj if isinstance(obj, list) else [obj]
    calls = []
    for it in items:
        if not isinstance(it, dict):
            continue
        fn = it.get("function") if isinstance(it.get("function"), dict) else it
        name = fn.get("name")
        args = fn.get("arguments", fn.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                continue
        if isinstance(name, str) and isinstance(args, dict):
            calls.append({"name": name, "arguments": args})
    return calls


def parse_text_tool_calls(content: str | None, tool_names: set[str]) -> tuple[list[ToolCall], str | None]:
    """Extract tool calls that a model wrote into `content` instead of `tool_calls`."""
    if not content or not tool_names:
        return [], content
    found: list[dict[str, Any]] = []
    rest = content
    for pattern in _TEXT_CALLS:
        for block in pattern.findall(content):
            for piece in [block, *re.findall(r"\{.*\}", block, re.S)]:
                try:
                    found += _as_calls(json.loads(piece))
                    break
                except json.JSONDecodeError:
                    continue
        rest = pattern.sub("", rest)
    if not found:
        stripped = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        if stripped[:1] in "[{":
            try:
                found = _as_calls(json.loads(stripped))
                rest = ""
            except json.JSONDecodeError:
                found = []
    calls = [
        ToolCall(id=f"text_call_{i}", name=c["name"], arguments=c["arguments"], raw_arguments=json.dumps(c["arguments"]))
        for i, c in enumerate(found)
        if c["name"] in tool_names
    ]
    return calls, (rest.strip() or None) if calls else content


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
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise LLMError("NVIDIA_API_KEY is required for LLM_PROVIDER=nvidia")
        self.http = http
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.enable_thinking = enable_thinking
        self.max_retries = max_retries
        self.retry_base_delay = 1.0

    def _prepare(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        extra: dict[str, Any] = {}
        msgs = [dict(m) for m in messages]
        if "nemotron-3" in self.model:
            extra["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}
        elif "nemotron" in self.model and msgs and msgs[0]["role"] == "system":
            tag = "/think" if self.enable_thinking else "/no_think"
            msgs[0]["content"] = f"{tag}\n{msgs[0]['content']}"
        return msgs, extra

    async def _post(self, body: dict[str, Any], task_id: str | None) -> httpx.Response:
        delay = self.retry_base_delay
        for attempt in range(self.max_retries + 1):
            try:
                r = await self.http.request(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    tool="llm.chat",
                    task_id=task_id,
                    headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                    json=body,
                )
            except httpx.TransportError as e:
                if attempt == self.max_retries:
                    raise LLMError(f"NIM unreachable: {e}") from e
            else:
                if r.status_code not in _RETRY_STATUS or attempt == self.max_retries:
                    return r
            await asyncio.sleep(delay)
            delay *= 2
        raise LLMError("unreachable")  # pragma: no cover

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
        r = await self._post(body, task_id)
        latency = (time.perf_counter() - start) * 1000
        if r.status_code != 200:
            raise LLMError(f"NIM {r.status_code}: {r.text[:300]}")
        data = r.json()
        msg = data["choices"][0]["message"]
        content, thinking = split_thinking(msg.get("content"))
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
        if not calls and tools:
            names = {t["function"]["name"] for t in tools}
            calls, content = parse_text_tool_calls(content, names)
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or thinking
        return LLMResponse(
            content=content,
            tool_calls=calls,
            model=data.get("model", self.model),
            reasoning=reasoning,
            usage=data.get("usage") or {},
            latency_ms=latency,
        )
