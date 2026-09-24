# Involuntary Rebooking Policy

> Demo document for the ReRoute hackathon project. Fictional policy.

## RBK-001 — Scope of involuntary rebooking

Policy ID: RBK-001
Category: rebooking

Involuntary rebooking applies when the airline cancels a flight, or delays it beyond the rebooking
threshold. The recovery plan must re-accommodate each affected passenger on exactly one alternative
flight to the same destination, or explicitly mark the passenger for manual handling. Every change
to a booking must be approved by an authorised operations controller before tickets are re-issued.

```policy-params
rule: human_approval
requires_human_approval: true
```

## RBK-002 — Rebooking threshold for delays

Policy ID: RBK-002
Category: rebooking

A delayed flight triggers proactive rebooking only when the expected delay is 180 minutes or more.
For shorter delays, passengers remain on the original flight and are informed of the new time.

```policy-params
rule: rebooking_threshold
rebooking_threshold_delay_minutes: 180
```

## SSR-001 — Passengers with special service requests

Policy ID: SSR-001
Category: rebooking / special assistance

Passengers with special service requests - wheelchair passengers who cannot climb stairs (WCHC),
unaccompanied minors (UMNR) and passengers with medical clearance (MEDA) - must remain on the
carrier's own flights so that ground handling and escort arrangements can be transferred. Their new
itinerary must be re-confirmed by a customer service agent with the station before the ticket is
re-issued; they must never be re-booked fully automatically.

```policy-params
rule: ssr
manual_confirmation_ssr: [WCHC, UMNR, MEDA]
own_carrier_only_ssr: [WCHC, UMNR, MEDA]
```
