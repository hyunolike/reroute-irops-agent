# VIP and Premium Passenger Handling

> Demo document for the ReRoute hackathon project. Fictional policy.

## VIP-001 — Priority order during re-accommodation

Policy ID: VIP-001
Category: vip

When seats on alternative flights are scarce, re-accommodation priority is: (1) VIP passengers,
(2) passengers with tight onward connections, (3) Platinum and Gold tier members, (4) all other
passengers. Under equal conditions a VIP passenger must be re-accommodated before an ordinary
passenger. Delay experienced by VIP passengers carries a higher operational cost.

```policy-params
rule: vip_priority
vip_priority: true
```

## VIP-002 — Proactive VIP contact

Policy ID: VIP-002
Category: vip

VIP passengers must be contacted personally by the premium service desk once a re-accommodation plan
has been approved, and escorted to lounge access where available.
