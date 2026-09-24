# Connection and Minimum Connection Time (MCT) Policy

> Demo document for the ReRoute hackathon project. Fictional MCT values.

## MCT-001 — Protecting onward connections

Policy ID: MCT-001
Category: connection

Passengers holding a through-ticket with an onward connection must be re-accommodated on a flight
that allows them to make the onward flight within the published Minimum Connection Time (MCT). An
itinerary that breaks the minimum connection time must never be issued.

## MCT-002 — Tokyo Narita minimum connection time

Policy ID: MCT-002
Category: connection

The Minimum Connection Time at Tokyo Narita (NRT) for international-to-international transfers is
90 minutes, including transfers between Terminal 1 and Terminal 2.

```policy-params
rule: mct
airport: NRT
mct_minutes: 90
```

## MCT-003 — At-risk connections

Policy ID: MCT-003
Category: connection

A connection whose margin above the MCT is less than 30 minutes is classified as at-risk. At-risk
connections must be reviewed by an operations controller, who may arrange a meet-and-assist service
or re-protect the onward flight before the itinerary is confirmed.

```policy-params
rule: connection_risk
connection_risk_buffer_minutes: 30
```
