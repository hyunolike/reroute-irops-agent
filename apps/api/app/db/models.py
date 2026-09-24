"""SQLAlchemy ORM models.

Two bounded contexts share one PostgreSQL database but are owned by different services:
- Airline domain (flights, passengers, reservations, disruptions) -> owned by the Mock Airline API.
- Agent domain (tasks, events, plans, approvals, audit) -> owned by the ReRoute Agent API.
The agent never touches airline tables directly; it goes through the Airline HTTP API.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


# ----------------------------------------------------------------------------- airline domain
class Flight(Base):
    __tablename__ = "flights"
    __table_args__ = (UniqueConstraint("flight_no", "departure_time", name="uq_flight_departure"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    flight_no: Mapped[str] = mapped_column(String(16), index=True)
    carrier: Mapped[str] = mapped_column(String(4))
    origin: Mapped[str] = mapped_column(String(3), index=True)
    destination: Mapped[str] = mapped_column(String(3), index=True)
    departure_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    arrival_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="SCHEDULED")
    economy_capacity: Mapped[int] = mapped_column(Integer)
    business_capacity: Mapped[int] = mapped_column(Integer)
    aircraft: Mapped[str] = mapped_column(String(16), default="")
    terminal: Mapped[str] = mapped_column(String(4), default="T2")

    reservations: Mapped[list[Reservation]] = relationship(back_populates="flight")


class Passenger(Base):
    __tablename__ = "passengers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    tier: Mapped[str] = mapped_column(String(16))  # PLATINUM | GOLD | SILVER | BASIC
    vip: Mapped[bool] = mapped_column(Boolean, default=False)
    special_assistance: Mapped[str | None] = mapped_column(String(16), nullable=True)  # WCHC | UMNR | ...


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    pnr: Mapped[str] = mapped_column(String(8), index=True)
    passenger_id: Mapped[str] = mapped_column(ForeignKey("passengers.id"))
    flight_id: Mapped[str] = mapped_column(ForeignKey("flights.id"), index=True)
    cabin: Mapped[str] = mapped_column(String(16))
    fare_class: Mapped[str] = mapped_column(String(2), default="Y")
    status: Mapped[str] = mapped_column(String(16), default="CONFIRMED")
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    onward_flight_no: Mapped[str | None] = mapped_column(String(16), nullable=True)
    onward_departure_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    onward_destination: Mapped[str | None] = mapped_column(String(3), nullable=True)
    rebooked_from_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    passenger: Mapped[Passenger] = relationship()
    flight: Mapped[Flight] = relationship(back_populates="reservations")


class Disruption(Base):
    __tablename__ = "disruptions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    flight_id: Mapped[str] = mapped_column(ForeignKey("flights.id"), index=True)
    type: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(256))
    airline_fault: Mapped[bool] = mapped_column(Boolean, default=True)
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ----------------------------------------------------------------------------- agent domain
class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    command: Mapped[str] = mapped_column(Text)
    flight_no: Mapped[str | None] = mapped_column(String(16), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="RECEIVED")
    plan_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    runtime: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(32))
    component: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RebookingPlan(Base):
    __tablename__ = "rebooking_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    flight_no: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="PENDING_APPROVAL")
    solver: Mapped[str] = mapped_column(String(64))
    objective_value: Mapped[float] = mapped_column(Float)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    baseline: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    execution_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list[RebookingPlanItem]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="RebookingPlanItem.position"
    )


class RebookingPlanItem(Base):
    """One passenger's proposed re-accommodation (the spec's per-passenger RebookingPlan)."""

    __tablename__ = "rebooking_plan_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column(ForeignKey("rebooking_plans.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    passenger_id: Mapped[str] = mapped_column(String(32))
    passenger_name: Mapped[str] = mapped_column(String(128))
    tier: Mapped[str] = mapped_column(String(16))
    vip: Mapped[bool] = mapped_column(Boolean, default=False)
    reservation_id: Mapped[str] = mapped_column(String(32))
    original_flight: Mapped[str] = mapped_column(String(16))
    alternative_flight: Mapped[str | None] = mapped_column(String(16), nullable=True)
    original_cabin: Mapped[str] = mapped_column(String(16))
    new_cabin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    delay_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    penalties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    policy_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24))
    new_reservation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    plan: Mapped[RebookingPlan] = relationship(back_populates="items")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column(ForeignKey("rebooking_plans.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    requested_by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_manual_item_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    agent: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(512))
    action: Mapped[str] = mapped_column(String(64))
    policy: Mapped[str] = mapped_column(String(128))
    result: Mapped[str] = mapped_column(String(16))  # ALLOW | DENY | SUCCESS | FAILURE
    enforced_by: Mapped[str] = mapped_column(String(32), default="")
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
