"""Read-only tools against the Mock Airline API (HTTP)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.domain.enums import AgentState, Cabin, Component
from app.domain.models import AffectedPassengerDTO, FlightDTO
from app.tools.base import Tool, ToolContext, ToolError, ToolResult


class FlightArgs(BaseModel):
    flight_no: str = Field(description="Flight number, e.g. KE123", pattern=r"^[A-Za-z0-9]{2}\d{1,4}$")


class GetDisruptedFlight(Tool):
    name = "get_disrupted_flight"
    description = (
        "Look up a flight's operational status (SCHEDULED / DELAYED / CANCELLED), schedule and disruption "
        "details from the airline operations system. Always call this first."
    )
    Args = FlightArgs
    state = AgentState.ANALYZING_DISRUPTION
    component = Component.AIRLINE_API

    async def run(self, args: FlightArgs, ctx: ToolContext) -> ToolResult:
        r = await ctx.http.request(
            "GET", f"/api/flights/{args.flight_no.upper()}", service="airline-service", tool=self.name, task_id=ctx.task_id
        )
        if r.status_code == 404:
            ctx.memory.flight_lookup_failed = True
            raise ToolError(f"flight {args.flight_no} not found in the operations system")
        r.raise_for_status()
        f = FlightDTO.model_validate(r.json())
        ctx.memory.flight = f
        d = f.disruption
        if f.status == "CANCELLED":
            ctx.memory.flight_assessment = "recover"
            assessment = "cancelled - all passengers require re-accommodation"
        elif f.status == "DELAYED":
            ctx.memory.flight_assessment = "check_policy"
            assessment = f"delayed {d.delay_minutes if d else '?'} min - check the rebooking threshold policy before acting"
        else:
            ctx.memory.flight_assessment = "no_recovery"
            assessment = "operating normally"
        view = {
            "flight_no": f.flight_no,
            "status": f.status,
            "origin": f.origin,
            "destination": f.destination,
            "departure": f.departure_time.isoformat(),
            "arrival": f.arrival_time.isoformat(),
            "departure_date": f.departure_time.date().isoformat(),
            "reason": d.reason if d else None,
            "delay_minutes": d.delay_minutes if d else 0,
            "airline_fault": d.airline_fault if d else None,
            "requires_recovery": {"recover": True, "no_recovery": False}.get(ctx.memory.flight_assessment),
            "assessment": assessment,
        }
        title = f"{f.flight_no} {f.origin}→{f.destination} {f.status}" + (f" · {d.reason}" if d else "")
        return ToolResult(title=title, llm_view=view, detail={"flight": f.model_dump(mode="json")})


class GetAffectedPassengers(Tool):
    name = "get_affected_passengers"
    description = "Load the passenger manifest (reservations) of a disrupted flight."
    Args = FlightArgs
    state = AgentState.FETCHING_PASSENGERS
    component = Component.AIRLINE_API

    def precondition(self, ctx: ToolContext) -> str | None:
        if ctx.memory.flight is None:
            return "call get_disrupted_flight first to confirm the disruption"
        return None

    async def run(self, args: FlightArgs, ctx: ToolContext) -> ToolResult:
        r = await ctx.http.request(
            "GET",
            f"/api/flights/{args.flight_no.upper()}/passengers",
            service="airline-service",
            tool=self.name,
            task_id=ctx.task_id,
        )
        r.raise_for_status()
        pax = [AffectedPassengerDTO.model_validate(p) for p in r.json()["passengers"]]
        ctx.memory.passengers = pax
        view = {
            "count": len(pax),
            "business": sum(p.cabin == Cabin.BUSINESS for p in pax),
            "economy": sum(p.cabin == Cabin.ECONOMY for p in pax),
            "vip": sum(p.vip for p in pax),
            "connections": [
                {
                    "passenger_id": p.passenger_id,
                    "onward": p.onward_flight_no,
                    "onward_departure": p.onward_departure_time.isoformat(),
                }
                for p in pax
                if p.onward_departure_time
            ],
            "special_assistance": [
                {"passenger_id": p.passenger_id, "ssr": p.special_assistance} for p in pax if p.special_assistance
            ],
            "tiers": {t: sum(p.tier == t for p in pax) for t in sorted({p.tier for p in pax})},
        }
        title = (
            f"{len(pax)} affected passengers loaded · {view['business']} business · {view['vip']} VIP · "
            f"{len(view['connections'])} connections · {len(view['special_assistance'])} SSR"
        )
        return ToolResult(title=title, llm_view=view, detail={"passengers": [p.model_dump(mode="json") for p in pax]})


class SearchAlternativesArgs(BaseModel):
    origin: str = Field(pattern=r"^[A-Za-z]{3}$")
    destination: str = Field(pattern=r"^[A-Za-z]{3}$")
    departure_date: date


class SearchAlternativeFlights(Tool):
    name = "search_alternative_flights"
    description = (
        "Search alternative flights (all carriers, incl. co-terminal airports) from origin to destination "
        "departing on/after the given date, with remaining seat inventory per cabin."
    )
    Args = SearchAlternativesArgs
    state = AgentState.SEARCHING_ALTERNATIVES
    component = Component.AIRLINE_API

    def precondition(self, ctx: ToolContext) -> str | None:
        return None if ctx.memory.flight else "call get_disrupted_flight first"

    async def run(self, args: SearchAlternativesArgs, ctx: ToolContext) -> ToolResult:
        exclude = ctx.memory.flight.flight_no if ctx.memory.flight else ""
        r = await ctx.http.request(
            "GET",
            "/api/flights/alternatives",
            service="airline-service",
            tool=self.name,
            task_id=ctx.task_id,
            params={
                "origin": args.origin.upper(),
                "destination": args.destination.upper(),
                "departure_date": args.departure_date.isoformat(),
                "exclude": exclude,
            },
        )
        r.raise_for_status()
        flights = [FlightDTO.model_validate(f) for f in r.json()["flights"]]
        ctx.memory.alternatives = flights
        view = {
            "count": len(flights),
            "flights": [
                {
                    "flight_no": f.flight_no,
                    "carrier": f.carrier,
                    "destination": f.destination,
                    "status": f.status,
                    "departure": f.departure_time.isoformat(),
                    "arrival": f.arrival_time.isoformat(),
                    "business_available": f.business_available,
                    "economy_available": f.economy_available,
                }
                for f in flights
            ],
        }
        seats = sum(f.economy_available + f.business_available for f in flights)
        return ToolResult(
            title=f"{len(flights)} alternative flights discovered · {seats} open seats",
            llm_view=view,
            detail={"flights": [f.model_dump(mode="json") for f in flights]},
        )
