"""Mock Airline System (flight inventory, passenger manifest, booking). Owned by the Airline API."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Disruption, Flight, Passenger, Reservation
from app.domain.enums import Cabin, ReservationStatus
from app.domain.models import AffectedPassengerDTO, DisruptionDTO, FlightDTO
from app.seed.loader import KST, as_kst

METRO = {"NRT": "TYO", "HND": "TYO", "ICN": "SEL", "GMP": "SEL"}
_HOLDING = (ReservationStatus.CONFIRMED.value,)


class NotFoundError(LookupError):
    pass


class BookingConflict(RuntimeError):
    pass


class AirlineService:
    def __init__(self, session: Session) -> None:
        self.s = session

    # ------------------------------------------------------------------ flights
    def _flight_row(self, flight_no: str, on: date | None = None) -> Flight:
        q = select(Flight).where(Flight.flight_no == flight_no.upper()).order_by(Flight.departure_time)
        rows = list(self.s.scalars(q))
        if not rows:
            raise NotFoundError(f"flight {flight_no} not found")
        if on:
            rows = [r for r in rows if as_kst(r.departure_time).date() == on] or rows
        return rows[0]

    def _sold(self, flight_id: str) -> dict[str, int]:
        q = (
            select(Reservation.cabin, func.count())
            .where(Reservation.flight_id == flight_id, Reservation.status.in_(_HOLDING))
            .group_by(Reservation.cabin)
        )
        return {cabin: n for cabin, n in self.s.execute(q)}

    def _to_dto(self, f: Flight) -> FlightDTO:
        sold = self._sold(f.id)
        disruption = self.s.scalar(select(Disruption).where(Disruption.flight_id == f.id).order_by(Disruption.occurred_at.desc()))
        dto = FlightDTO.model_validate(f)
        dto.departure_time = as_kst(f.departure_time)
        dto.arrival_time = as_kst(f.arrival_time)
        dto.economy_available = max(0, f.economy_capacity - sold.get(Cabin.ECONOMY.value, 0))
        dto.business_available = max(0, f.business_capacity - sold.get(Cabin.BUSINESS.value, 0))
        if disruption:
            dto.disruption = DisruptionDTO.model_validate(disruption)
            dto.disruption.occurred_at = as_kst(disruption.occurred_at)
        if f.status == "CANCELLED":
            dto.economy_available = dto.business_available = 0
        return dto

    def get_flight(self, flight_no: str, on: date | None = None) -> FlightDTO:
        return self._to_dto(self._flight_row(flight_no, on))

    def affected_passengers(self, flight_no: str, on: date | None = None) -> list[AffectedPassengerDTO]:
        f = self._flight_row(flight_no, on)
        q = (
            select(Reservation, Passenger)
            .join(Passenger, Passenger.id == Reservation.passenger_id)
            .where(
                Reservation.flight_id == f.id,
                Reservation.status.in_([ReservationStatus.DISRUPTED.value, ReservationStatus.CONFIRMED.value]),
            )
            .order_by(Reservation.id)
        )
        out = []
        for r, p in self.s.execute(q):
            out.append(
                AffectedPassengerDTO(
                    passenger_id=p.id,
                    name=p.name,
                    tier=p.tier,
                    vip=p.vip,
                    special_assistance=p.special_assistance,
                    reservation_id=r.id,
                    pnr=r.pnr,
                    cabin=Cabin(r.cabin),
                    fare_class=r.fare_class,
                    booked_at=as_kst(r.booked_at),
                    onward_flight_no=r.onward_flight_no,
                    onward_departure_time=as_kst(r.onward_departure_time),
                    onward_destination=r.onward_destination,
                )
            )
        return out

    def search_alternatives(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        window_hours: int = 36,
        include_coterminal: bool = True,
        exclude_flight_no: str | None = None,
    ) -> list[FlightDTO]:
        start = datetime.combine(departure_date, time(0, 0), tzinfo=KST).astimezone(UTC)
        end = start + timedelta(hours=window_hours)
        dests = {destination.upper()}
        if include_coterminal and METRO.get(destination.upper()):
            dests |= {a for a, m in METRO.items() if m == METRO[destination.upper()]}
        q = (
            select(Flight)
            .where(
                Flight.origin == origin.upper(),
                Flight.destination.in_(sorted(dests)),
                Flight.departure_time >= start,
                Flight.departure_time < end,
                Flight.status != "CANCELLED",
            )
            .order_by(Flight.departure_time)
        )
        rows = [f for f in self.s.scalars(q) if f.flight_no != (exclude_flight_no or "").upper()]
        return [self._to_dto(f) for f in rows]

    # ------------------------------------------------------------------ booking (write path)
    def rebook(self, items: list[dict]) -> list[dict]:
        """Move reservations onto new flights. Caller MUST have verified an approval token."""
        results = []
        for item in items:
            res = self.s.get(Reservation, item["reservation_id"])
            if res is None:
                results.append({**item, "status": "FAILED", "error": "reservation not found"})
                continue
            if res.status == ReservationStatus.REBOOKED.value:
                results.append({**item, "status": "FAILED", "error": "reservation already rebooked"})
                continue
            target = self._flight_row(item["flight_no"])
            dto = self._to_dto(target)
            if dto.available(item["cabin"]) <= 0:
                results.append({**item, "status": "FAILED", "error": f"no {item['cabin']} seat left on {target.flight_no}"})
                continue
            new = Reservation(
                pnr=res.pnr,
                passenger_id=res.passenger_id,
                flight_id=target.id,
                cabin=item["cabin"],
                fare_class=res.fare_class,
                status=ReservationStatus.CONFIRMED.value,
                booked_at=res.booked_at,
                onward_flight_no=res.onward_flight_no,
                onward_departure_time=res.onward_departure_time,
                onward_destination=res.onward_destination,
                rebooked_from_id=res.id,
            )
            res.status = ReservationStatus.REBOOKED.value
            self.s.add(new)
            self.s.flush()
            results.append({**item, "status": "CONFIRMED", "new_reservation_id": new.id, "pnr": res.pnr})
        self.s.commit()
        return results
