from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select

from app.db.base import Database, utcnow
from app.db.models import AgentEvent, AgentTask
from app.domain.enums import TERMINAL_STATES


class AgentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        # called once a task reaches a terminal state (e.g. to export its trace); set by the container
        self.on_settled: Callable[[str], None] | None = None

    def create_task(self, command: str, runtime: dict[str, Any]) -> AgentTask:
        with self.db.session() as s:
            task = AgentTask(command=command, runtime=runtime)
            s.add(task)
            s.commit()
            s.refresh(task)
            return task

    def get_task(self, task_id: str) -> AgentTask | None:
        with self.db.session() as s:
            return s.get(AgentTask, task_id)

    def list_tasks(self, limit: int = 20) -> list[AgentTask]:
        with self.db.session() as s:
            return list(s.scalars(select(AgentTask).order_by(AgentTask.created_at.desc()).limit(limit)))

    def update_task(self, task_id: str, **fields: Any) -> AgentTask:
        with self.db.session() as s:
            task = s.get(AgentTask, task_id)
            if task is None:
                raise LookupError(task_id)
            previous = task.state
            for k, v in fields.items():
                setattr(task, k, v)
            settled = "state" in fields and task.state in TERMINAL_STATES and fields["state"] != previous
            task.updated_at = utcnow()
            s.commit()
            s.refresh(task)
        if settled and self.on_settled:
            self.on_settled(task_id)
        return task

    def add_event(
        self,
        task_id: str,
        *,
        type: str,
        state: str,
        component: str,
        title: str,
        detail: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> AgentEvent:
        with self.db.session() as s:
            seq = (s.scalar(select(func.max(AgentEvent.seq)).where(AgentEvent.task_id == task_id)) or 0) + 1
            ev = AgentEvent(
                task_id=task_id,
                seq=seq,
                type=type,
                state=state,
                component=component,
                title=title[:256],
                detail=detail or {},
                duration_ms=duration_ms,
            )
            s.add(ev)
            s.commit()
            s.refresh(ev)
            return ev

    def claim_pending(self, actions: tuple[str, ...] = ("run", "resume")) -> tuple[AgentTask, str] | None:
        """Atomically claim the oldest task with queued work of the given kinds (optimistic compare-and-set).

        ReRoute's worker claims "run"/"resume"; the OpenClaw bridge claims "openclaw" - neither takes the other's.
        """
        with self.db.session() as s:
            for task in s.scalars(
                select(AgentTask).where(AgentTask.pending.in_(actions)).order_by(AgentTask.updated_at).limit(5)
            ):
                action = task.pending
                n = (
                    s.query(AgentTask)
                    .filter(AgentTask.id == task.id, AgentTask.pending == action)
                    .update({AgentTask.pending: None}, synchronize_session=False)
                )
                s.commit()
                if n == 1:
                    return s.get(AgentTask, task.id), action
        return None

    def events(self, task_id: str, after_seq: int = 0) -> list[AgentEvent]:
        with self.db.session() as s:
            q = select(AgentEvent).where(AgentEvent.task_id == task_id, AgentEvent.seq > after_seq).order_by(AgentEvent.seq)
            return list(s.scalars(q))
