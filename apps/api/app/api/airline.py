"""Mock Airline System HTTP API (flight status, manifest, inventory, booking)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_container, get_session
from app.approval.tokens import ApprovalTokenError, verify_token
from app.container import Container
from app.domain.enums import Cabin
from app.domain.models import FlightDTO
from app.services.airline import AirlineService, NotFoundError

router = APIRouter(prefix="/api", tags=["airline (mock)"])


@router.get("/flights/alternatives")
def search_alternatives(
    origin: str,
    destination: str,
    departure_date: date,
    window_hours: int = Query(36, ge=1, le=72),
    include_coterminal: bool = True,
    exclude: str | None = None,
    s: Session = Depends(get_session),
) -> dict:
    flights = AirlineService(s).search_alternatives(
        origin, destination, departure_date, window_hours, include_coterminal, exclude_flight_no=exclude
    )
    return {"count": len(flights), "flights": [f.model_dump(mode="json") for f in flights]}


@router.get("/flights/{flight_no}", response_model=FlightDTO)
def get_flight(flight_no: str, on: date | None = None, s: Session = Depends(get_session)) -> FlightDTO:
    try:
        return AirlineService(s).get_flight(flight_no, on)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/flights/{flight_no}/passengers")
def get_passengers(flight_no: str, on: date | None = None, s: Session = Depends(get_session)) -> dict:
    try:
        pax = AirlineService(s).affected_passengers(flight_no, on)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return {"flight_no": flight_no.upper(), "count": len(pax), "passengers": [p.model_dump(mode="json") for p in pax]}


class RebookItem(BaseModel):
    reservation_id: str
    flight_no: str
    cabin: Cabin


class RebookRequest(BaseModel):
    plan_id: str
    items: list[RebookItem] = Field(min_length=1)


@router.post("/bookings/rebookings")
def rebook(
    body: RebookRequest,
    x_approval_token: str | None = Header(default=None),
    s: Session = Depends(get_session),
    c: Container = Depends(get_container),
) -> dict:
    """State-changing booking write. Requires a signed approval token bound to the exact item set."""
    items = [i.model_dump(mode="json") for i in body.items]
    target = f"POST /api/bookings/rebookings plan:{body.plan_id}"
    if not x_approval_token:
        c.audit.record(
            agent="unknown",
            tool="booking-api",
            target=target,
            action="booking.rebook",
            policy="approval-token",
            result="DENY",
            enforced_by="airline-booking-api",
            details={"reason": "missing X-Approval-Token"},
        )
        raise HTTPException(401, "operator approval token required")
    try:
        claims = verify_token(
            c.settings.approval_signing_secret.get_secret_value(), x_approval_token, plan_id=body.plan_id, items=items
        )
    except ApprovalTokenError as e:
        c.audit.record(
            agent="unknown",
            tool="booking-api",
            target=target,
            action="booking.rebook",
            policy="approval-token",
            result="DENY",
            enforced_by="airline-booking-api",
            details={"reason": str(e)},
        )
        raise HTTPException(403, str(e)) from e
    results = AirlineService(s).rebook(items)
    return {"plan_id": body.plan_id, "approved_by": claims.approved_by, "results": results}
