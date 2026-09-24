#!/usr/bin/env bash
# Forward OpenShell's own ALLOW/DENY decisions (OCSF log lines) into ReRoute's audit log, where they
# appear with enforced_by=openshell next to the Approval Gateway and booking entries.
set -euo pipefail
NAME="${SANDBOX_NAME:-reroute-agent}"
API="${REROUTE_API:-http://localhost:8000}"
openshell logs "$NAME" --source sandbox 2>/dev/null \
  | grep -E "\[OCSF *\].*(ALLOWED|DENIED|BLOCKED)" \
  | python3 -c 'import json,sys; print(json.dumps({"sandbox": "'"$NAME"'", "lines": [l.rstrip() for l in sys.stdin][-5000:]}))' \
  | curl -sS -X POST "$API/api/audit/openshell" -H 'content-type: application/json' -d @-
echo
