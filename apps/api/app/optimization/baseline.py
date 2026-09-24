"""First-come-first-served desk process, used only as a comparison baseline in the UI.

It uses the same policy-eligible flights as the optimizer (so the comparison is fair), but rebooks
passengers one by one in booking order onto the earliest flight with a seat in their cabin
(downgrading business if needed). It does not reason about onward connections, SSR handling or VIP
priority - which is what a manual desk process under time pressure typically misses.
"""

from __future__ import annotations

from app.domain.enums import Cabin
from app.domain.models import BaselineComparison, OptimizationRequest
from app.optimization.formulation import screen_flights


def fcfs_baseline(req: OptimizationRequest) -> BaselineComparison:
    rules = req.rules
    orig = req.disrupted_flight
    eligible, _ = screen_flights(req)
    flights = sorted((req.alternatives[i] for i in eligible), key=lambda f: f.departure_time)
    seats = {(f.flight_no, c): f.available(c) for f in flights for c in Cabin}
    delays, vip_delays = [], []
    accommodated = violations = missed = ssr_viol = downgrades = 0
    for p in sorted(req.passengers, key=lambda p: p.booked_at):
        chosen = None
        for f in flights:
            for cabin in [p.cabin] if p.cabin == Cabin.ECONOMY else [Cabin.BUSINESS, Cabin.ECONOMY]:
                if seats[(f.flight_no, cabin)] > 0:
                    chosen = (f, cabin)
                    break
            if chosen:
                break
        if not chosen:
            continue
        f, cabin = chosen
        seats[(f.flight_no, cabin)] -= 1
        accommodated += 1
        delay = max(0, int((f.arrival_time - orig.arrival_time).total_seconds() // 60))
        delays.append(delay)
        if p.vip:
            vip_delays.append(delay)
        if cabin != p.cabin:
            downgrades += 1
        if f.carrier != rules.own_carrier and f.carrier not in rules.interline_partners:
            violations += 1
        if p.special_assistance in rules.own_carrier_only_ssr and f.carrier != rules.own_carrier:
            ssr_viol += 1
        if p.onward_departure_time is not None:
            slack = (p.onward_departure_time - f.arrival_time).total_seconds() / 60
            if slack < (rules.mct_minutes or 0):
                missed += 1
    return BaselineComparison(
        method="First-come-first-served desk process (same eligible flights)",
        accommodated=accommodated,
        policy_violations=violations,
        missed_connections=missed,
        ssr_violations=ssr_viol,
        business_downgrades=downgrades,
        avg_delay_minutes=round(sum(delays) / len(delays), 1) if delays else 0.0,
        vip_avg_delay_minutes=round(sum(vip_delays) / len(vip_delays), 1) if vip_delays else 0.0,
    )
