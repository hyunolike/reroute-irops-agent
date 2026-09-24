from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.db.base import Database
from app.db.models import AuditLog


class AuditService:
    """Append-only audit trail: who (agent/operator) did what (tool/action) to which target, under which
    policy, with which result, and which runtime enforced it."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        *,
        agent: str,
        tool: str,
        target: str,
        action: str,
        policy: str,
        result: str,
        enforced_by: str,
        task_id: str | None = None,
        details: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> AuditLog:
        with self.db.session() as s:
            row = AuditLog(
                agent=agent,
                tool=tool,
                target=target[:512],
                action=action,
                policy=policy,
                result=result,
                enforced_by=enforced_by,
                task_id=task_id,
                details=details or {},
            )
            if timestamp:
                row.timestamp = timestamp
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    def list(self, *, limit: int = 200, task_id: str | None = None, result: str | None = None) -> list[AuditLog]:
        with self.db.session() as s:
            q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
            if task_id:
                q = q.where(AuditLog.task_id == task_id)
            if result:
                q = q.where(AuditLog.result == result)
            return list(s.scalars(q))
