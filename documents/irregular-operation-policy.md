# Irregular Operations (IROPS) Policy

> Demo document for the ReRoute hackathon project. Fictional carrier policy ("KE" is used as the
> operating carrier code for demonstration only). Not an actual airline's policy.

## IROP-001 — Airline-fault cancellation: re-protection priority

Policy ID: IROP-001
Category: irregular-operations

When a flight is cancelled for reasons within the airline's control (technical, crew, late inbound
aircraft), affected passengers must be re-protected proactively, without waiting for the passenger to
contact the airline. Re-protection is made first onto the carrier's own flights (own metal) to the
same destination airport, in order of the earliest arrival. The passenger is not charged any fare
difference, change fee or re-issue fee for involuntary re-accommodation.

```policy-params
rule: own_carrier_first
own_carrier: KE
```

## IROP-002 — Interline re-accommodation

Policy ID: IROP-002
Category: irregular-operations

If own-carrier capacity is insufficient, passengers may be re-accommodated on a partner carrier with
which an interline (IET) agreement is in force. The current interline partner for the ICN-TYO market
is OZ. Carriers without an interline agreement (for example low-cost carriers such as 7C) must not be
used for automatic re-accommodation; such cases require a manual ad-hoc endorsement approved by the
duty manager. Interline re-accommodation incurs settlement cost and should only be used when it
reduces passenger delay.

```policy-params
rule: interline
allow_interline: true
interline_partners: [OZ]
```

## IROP-003 — Maximum re-accommodation window

Policy ID: IROP-003
Category: irregular-operations

Automatic re-accommodation may only use alternative flights departing within 12 hours of the
original scheduled departure time. Alternatives departing later than 12 hours (typically next-day
flights) must be offered by an agent together with hotel accommodation and cannot be booked
automatically by the recovery system.

```policy-params
rule: max_delay
max_delay_hours: 12
```

## IROP-004 — Duty of care during disruption

Policy ID: IROP-004
Category: irregular-operations

For cancellations with an expected delay of more than 3 hours, passengers are entitled to meal
vouchers. If re-accommodation requires an overnight stay, hotel accommodation and ground
transportation are provided. Passengers who cannot be re-accommodated within the policy window must
be offered a full refund of the unused portion of the ticket or re-routing at a later date of their
choice.

```policy-params
rule: duty_of_care
meal_voucher_delay_minutes: 180
```

## IROP-005 — Co-terminal airports

Policy ID: IROP-005
Category: irregular-operations

Tokyo Narita (NRT) and Tokyo Haneda (HND) are co-terminal airports. Re-accommodation to a different
co-terminal airport changes the passenger's destination and therefore requires explicit passenger
consent. The recovery system must not automatically re-book a passenger to a co-terminal airport.

```policy-params
rule: coterminal
allow_coterminal: false
```
