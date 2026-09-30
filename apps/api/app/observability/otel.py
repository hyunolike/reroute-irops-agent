"""Optional OTLP export of task traces (Langfuse, Arize Phoenix, Jaeger, Grafana Tempo, ... - anything OTLP).

Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set and the `otel` extra is installed. A trace is exported once, when its
task settles (COMPLETED / REJECTED / FAILED), so it includes the operator's decisions. The spans are replayed from
the trace built by app/observability/trace.py with their recorded start and end times.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

log = logging.getLogger("reroute.otel")


def _ns(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1_000_000_000)


class TraceExporter:
    def __init__(self, tracer_provider: Any, build_trace: Callable[[str], dict[str, Any] | None]) -> None:
        self.provider = tracer_provider
        self.tracer = tracer_provider.get_tracer("reroute")
        self.build_trace = build_trace

    def export_task(self, task_id: str) -> None:
        """Never raises: observability must not break the recovery workflow."""
        try:
            trace = self.build_trace(task_id)
            if trace:
                self._emit(trace)
        except Exception:  # noqa: BLE001
            log.exception("OTLP export of task %s failed", task_id)

    def _emit(self, trace: dict[str, Any]) -> None:
        from opentelemetry.trace import Status, StatusCode, set_span_in_context

        started: dict[str, Any] = {}
        pending = list(trace["spans"])
        while pending:  # parents first
            ready = [s for s in pending if s["parent_id"] is None or s["parent_id"] in started]
            if not ready:  # orphaned spans: attach to the root rather than drop them
                ready = pending
            for s in ready:
                parent = started.get(s["parent_id"]) if s["parent_id"] else None
                span = self.tracer.start_span(
                    s["name"],
                    context=set_span_in_context(parent) if parent else None,
                    start_time=_ns(s["start"]),
                    attributes={k: v for k, v in s["attributes"].items() if v is not None},
                )
                if s["status"] == "ERROR":
                    span.set_status(Status(StatusCode.ERROR))
                started[s["span_id"]] = span
                pending.remove(s)
        for s in trace["spans"]:
            started[s["span_id"]].end(end_time=_ns(s["end"]))

    def shutdown(self) -> None:
        self.provider.shutdown()


def build_exporter(
    endpoint: str | None,
    service_name: str,
    build_trace: Callable[[str], dict[str, Any] | None],
    *,
    span_exporter: Any = None,
) -> TraceExporter | None:
    """`span_exporter` overrides the OTLP/HTTP exporter (tests pass an in-memory one)."""
    if not endpoint and span_exporter is None:
        return None
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError:
        log.warning("OTEL_EXPORTER_OTLP_ENDPOINT is set but the 'otel' extra is not installed - traces are not exported")
        return None
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if span_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")))
    log.info("exporting task traces over OTLP to %s", endpoint or "a custom exporter")
    return TraceExporter(provider, build_trace)
