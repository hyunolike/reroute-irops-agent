"""Idempotent seeding of the Mock Airline System from data/seed/*.json."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Disruption, Flight, Passenger, Reservation

KST = timezone(timedelta(hours=9), name="KST")


def as_kst(dt: datetime | None) -> datetime | None:
    """DB values are stored in UTC; SQLite returns them naive, PostgreSQL (timestamptz) aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(KST)


def default_service_date() -> date:
    return datetime.now(KST).date()


def _at(service_date: date, hhmm: str, day_offset: int = 0) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(service_date + timedelta(days=day_offset), time(h, m), tzinfo=KST).astimezone(UTC)


def seed_database(session: Session, seed_dir: Path, service_date: date | None = None, *, reset: bool = False) -> dict:
    service_date = service_date or default_service_date()
    if reset:
        for model in (Reservation, Disruption, Passenger, Flight):
            session.query(model).delete()
        session.commit()
    if session.scalar(select(Flight.id).limit(1)) is not None:
        return {"seeded": False, "reason": "already seeded"}

    flights_doc = json.loads((seed_dir / "flights.json").read_text(encoding="utf-8"))
    pax_doc = json.loads((seed_dir / "passengers.json").read_text(encoding="utf-8"))

    by_no: dict[str, Flight] = {}
    for f in flights_doc["flights"]:
        dep = _at(service_date, f["departure"], f.get("day_offset", 0))
        arr = _at(service_date, f["arrival"], f.get("day_offset", 0))
        if arr < dep:  # overnight arrival
            arr += timedelta(days=1)
        flight = Flight(
            id=f"{f['flight_no']}-{dep:%Y%m%d}",
            flight_no=f["flight_no"],
            carrier=f["carrier"],
            origin=f["origin"],
            destination=f["destination"],
            departure_time=dep,
            arrival_time=arr,
            status=f["status"],
            economy_capacity=f["economy_capacity"],
            business_capacity=f["business_capacity"],
            aircraft=f.get("aircraft", ""),
            terminal=f.get("terminal", "T2"),
        )
        session.add(flight)
        by_no[flight.flight_no] = flight
    session.flush()

    for d in flights_doc.get("disruptions", []):
        session.add(
            Disruption(
                flight_id=by_no[d["flight_no"]].id,
                type=d["type"],
                reason=d["reason"],
                airline_fault=d.get("airline_fault", True),
                delay_minutes=d.get("delay_minutes", 0),
                occurred_at=_at(service_date, d["occurred_at"]),
            )
        )

    for p in pax_doc["passengers"]:
        session.add(Passenger(**p))
    session.flush()

    base = datetime.combine(service_date, time(9, 0), tzinfo=KST).astimezone(UTC)
    for i, r in enumerate(pax_doc["reservations"]):
        flight = by_no[r["flight_no"]]
        session.add(
            Reservation(
                id=f"R{i + 1:04d}",
                pnr=r["pnr"],
                passenger_id=r["passenger_id"],
                flight_id=flight.id,
                cabin=r["cabin"],
                fare_class=r.get("fare_class", "Y"),
                status="DISRUPTED" if flight.status == "CANCELLED" else "CONFIRMED",
                booked_at=base - timedelta(days=r["booked_days_before"]),
                onward_flight_no=r.get("onward_flight_no"),
                onward_departure_time=_at(service_date, r["onward_departure"]) if r.get("onward_departure") else None,
                onward_destination=r.get("onward_destination"),
            )
        )
    session.commit()
    return {"seeded": True, "flights": len(by_no), "passengers": len(pax_doc["passengers"])}
