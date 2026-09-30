"""Exception-resolution quality metrics, shared by the API (shadow review) and the evaluation harness."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

REJECTED = "REJECTED"


def resolution_metrics(exception_ids: Iterable[str], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """`attempts`: persisted ResolutionAttempt rows as dicts (passenger_id, attempt, action, verdict, final, violations)."""
    exceptions = sorted(set(exception_ids))
    final = {a["passenger_id"]: a for a in attempts if a["final"]}
    firsts = [a for a in attempts if a["attempt"] == 1]
    return {
        "exceptions": len(exceptions),
        "recommended": len(final),
        "coverage": round(len(final) / len(exceptions), 3) if exceptions else None,
        "first_pass_accept_rate": round(sum(a["verdict"] != REJECTED for a in firsts) / len(firsts), 3) if firsts else None,
        "attempts": len(attempts),
        "rejected_attempts": sum(a["verdict"] == REJECTED for a in attempts),
        "violations": dict(Counter(v["code"] for a in attempts for v in a["violations"])),
        "actions": {pid: final[pid]["action"] for pid in exceptions if pid in final},
        "without_recommendation": [pid for pid in exceptions if pid not in final],
        "waivers_requested": sorted(pid for pid, a in final.items() if a["verdict"] == "PASS_REQUIRES_WAIVER"),
    }
