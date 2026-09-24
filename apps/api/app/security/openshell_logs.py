"""Parser for NVIDIA OpenShell sandbox log lines (OCSF shorthand), e.g.

[1775014132.690] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED /usr/bin/curl(64) -> httpbin.org:443 [policy:- engine:opa]
[1775014132.190] [sandbox] [OCSF ] [ocsf] HTTP:GET [INFO] ALLOWED GET http://api.github.com/zen [policy:github_api]

Used by POST /api/audit/openshell so decisions made by the real OpenShell proxy land in ReRoute's audit log.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

_LINE = re.compile(
    r"^\[(?P<ts>[\d.]+)\]\s+\[(?P<src>[^\]]+)\]\s+\[OCSF\s*\]\s+\[ocsf\]\s+(?P<cls>[A-Z]+):(?P<act>[A-Z_]+)\s+"
    r"\[(?P<sev>[A-Z]+)\]\s+(?P<result>ALLOWED|DENIED|BLOCKED)\s+(?P<rest>.*?)\s*\[policy:(?P<policy>[^\s\]]+)[^\]]*\]\s*$"
)


def parse_openshell_line(line: str) -> dict | None:
    m = _LINE.match(line.strip())
    if not m:
        return None
    rest = m.group("rest")
    target = rest
    binary = None
    if "->" in rest:  # NET:OPEN form: "<binary>(pid) -> host:port"
        left, target = (s.strip() for s in rest.split("->", 1))
        binary = left.split("(")[0]
    else:  # HTTP form: "GET http://host/path"
        parts = rest.split(" ", 1)
        target = parts[1] if len(parts) > 1 else rest
    return {
        "timestamp": datetime.fromtimestamp(float(m.group("ts")), tz=UTC),
        "action": f"{m.group('cls')}:{m.group('act')}",
        "target": target,
        "binary": binary,
        "policy": m.group("policy"),
        "result": "ALLOW" if m.group("result") == "ALLOWED" else "DENY",
        "severity": m.group("sev"),
        "raw": line.strip(),
    }
