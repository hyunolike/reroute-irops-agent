# Fare Rules for Involuntary Changes

> Demo document for the ReRoute hackathon project. Fictional fare rules.

## FARE-001 — No charge for involuntary changes

Policy ID: FARE-001
Category: fare-rule

Fare difference, change fees and re-issue fees are waived for involuntary changes caused by
irregular operations. The original fare basis is retained on the new flight.

## FARE-002 — Cabin preservation and involuntary downgrade

Policy ID: FARE-002
Category: fare-rule

Business class passengers must be re-accommodated in business class whenever a business seat is
available on an eligible flight. If no business seat is available, the passenger may be downgraded
to economy only as a last resort; the fare difference between the booked and flown cabin is refunded
automatically and the passenger receives a downgrade compensation voucher.

```policy-params
rule: preserve_cabin
preserve_cabin: true
```

## FARE-003 — No automatic upgrades

Policy ID: FARE-003
Category: fare-rule

Economy passengers are not upgraded to business class during automatic re-accommodation. Upgrades
may only be granted manually by the duty manager.

```policy-params
rule: upgrade
allow_upgrade: false
```
