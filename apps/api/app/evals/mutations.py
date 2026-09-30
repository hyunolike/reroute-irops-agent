"""Declarative edits applied to the seeded demo data, so each golden case is a variation of the same airline day."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Flight, Passenger, Reservation
from app.domain.enums import Cabin
from app.services.airline import AirlineService


def apply_mutations(s: Session, mutations: list[dict[str, Any]]) -> None:
    """Supported ops (one key each):
    - {sell_out: {flight_no, cabin}}               no seats left in that cabin
    - {capacity: {flight_no, cabin, delta}}        add / remove seats
    - {flight_status: {flight_no, status}}         e.g. CANCELLED, DELAYED
    - {special_assistance: {passenger_id, code}}   e.g. WCHC; null clears it
    - {shift_onward: {passenger_id, minutes}}      move the passenger's onward departure
    """
    for m in mutations:
        (op, args), *rest = m.items()
        if rest:
            raise ValueError(f"one op per mutation: {m}")
        if op in ("sell_out", "capacity"):
            f = _flight(s, args["flight_no"])
            cabin = Cabin(args.get("cabin", "ECONOMY"))
            field = "business_capacity" if cabin == Cabin.BUSINESS else "economy_capacity"
            delta = -AirlineService(s).get_flight(f.flight_no).available(cabin) if op == "sell_out" else args["delta"]
            setattr(f, field, getattr(f, field) + delta)
        elif op == "flight_status":
            _flight(s, args["flight_no"]).status = args["status"]
        elif op == "special_assistance":
            s.get(Passenger, args["passenger_id"]).special_assistance = args["code"]
        elif op == "shift_onward":
            r = s.scalar(select(Reservation).where(Reservation.passenger_id == args["passenger_id"]))
            r.onward_departure_time = r.onward_departure_time + timedelta(minutes=args["minutes"])
        else:
            raise ValueError(f"unknown mutation op {op!r}")
    s.commit()


def _flight(s: Session, flight_no: str) -> Flight:
    f = s.scalar(select(Flight).where(Flight.flight_no == flight_no))
    if f is None:
        raise ValueError(f"no flight {flight_no} in the seed")
    return f
