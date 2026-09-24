"""Compiles retrieved policy chunks into solver constraints (PolicyRules).

Only policies that were actually retrieved are applied. If a required rule has no retrieved policy,
a conservative default is used and the gap is reported - the LLM never fills it from memory.
"""

from __future__ import annotations

from app.domain.models import PolicyHit, PolicyRules

REQUIRED_RULES = ("own_carrier_first", "interline", "max_delay", "mct", "connection_risk", "ssr", "coterminal")

# Conservative defaults when a required policy was not retrieved.
_CONSERVATIVE = {
    "interline": {"allow_interline": False, "interline_partners": []},
    "max_delay": {"max_delay_hours": 6},
    "mct": {"mct_minutes": 120},
    "connection_risk": {"connection_risk_buffer_minutes": 60},
    "ssr": {"manual_confirmation_ssr": ["WCHR", "WCHC", "UMNR", "MEDA"], "own_carrier_only_ssr": ["WCHC", "UMNR", "MEDA"]},
    "coterminal": {"allow_coterminal": False},
}

_FIELDS = set(PolicyRules.model_fields) - {"applied", "missing"}


def compile_policy_rules(hits: list[PolicyHit]) -> PolicyRules:
    values: dict = {}
    applied: dict[str, str] = {}
    for hit in sorted(hits, key=lambda h: -h.score):
        rule = hit.params.get("rule")
        if not rule or rule in applied:
            continue
        applied[rule] = hit.policy_id
        values.update({k: v for k, v in hit.params.items() if k in _FIELDS})
    missing = [r for r in REQUIRED_RULES if r not in applied]
    for r in missing:
        for k, v in _CONSERVATIVE.get(r, {}).items():
            values.setdefault(k, v)
    return PolicyRules(**values, applied=applied, missing=missing)
