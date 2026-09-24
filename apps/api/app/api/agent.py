"""Agent tasks + live event stream (SSE)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import Container
from app.db.models import AgentEvent, AgentTask
from app.domain.enums import TERMINAL_STATES, AgentState

router = APIRouter(prefix="/api/agent", tags=["agent"])


class TaskCreate(BaseModel):
    command: str = Field(
        min_length=3, max_length=500, examples=["KE123편이 결항됐어. 영향 승객을 확인하고 최적 재배정안을 만들어줘."]
    )


def task_view(t: AgentTask) -> dict:
    return {
        "id": t.id,
        "command": t.command,
        "state": t.state,
        "flight_no": t.flight_no,
        "plan_id": t.plan_id,
        "runtime": t.runtime,
        "report": t.report,
        "error": t.error,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def event_view(e: AgentEvent) -> dict:
    return {
        "id": e.id,
        "seq": e.seq,
        "type": e.type,
        "state": e.state,
        "component": e.component,
        "title": e.title,
        "detail": e.detail,
        "duration_ms": e.duration_ms,
        "created_at": e.created_at.isoformat(),
    }


@router.post("/tasks", status_code=202)
async def create_task(body: TaskCreate, c: Container = Depends(get_container)) -> dict:
    task = c.repo.create_task(body.command, runtime=c.runtime_info())
    c.runner.submit(c.orchestrator.run(task.id))
    return task_view(task)


@router.get("/tasks")
def list_tasks(c: Container = Depends(get_container)) -> dict:
    return {"tasks": [task_view(t) for t in c.repo.list_tasks()]}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, c: Container = Depends(get_container)) -> dict:
    t = c.repo.get_task(task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    return task_view(t)


@router.get("/tasks/{task_id}/events")
async def stream_events(
    task_id: str, request: Request, after: int = 0, stream: bool = True, c: Container = Depends(get_container)
):
    if c.repo.get_task(task_id) is None:
        raise HTTPException(404, "task not found")
    if not stream:
        return {"events": [event_view(e) for e in c.repo.events(task_id, after)]}

    last = int(request.headers.get("last-event-id") or after)

    async def gen():
        nonlocal last
        idle = 0.0
        yield "retry: 1500\n\n"
        while True:
            if await request.is_disconnected():
                return
            events = await run_in_threadpool(c.repo.events, task_id, last)
            for e in events:
                last = e.seq
                yield f"id: {e.seq}\nevent: agent-event\ndata: {json.dumps(event_view(e), ensure_ascii=False, default=str)}\n\n"
            if events:
                idle = 0.0
                task = await run_in_threadpool(c.repo.get_task, task_id)
                yield f"event: task\ndata: {json.dumps(task_view(task), ensure_ascii=False, default=str)}\n\n"
                if AgentState(task.state) in TERMINAL_STATES:
                    yield "event: end\ndata: {}\n\n"
                    return
            else:
                idle += 0.3
                if idle >= 15:
                    idle = 0.0
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
