#!/usr/bin/env bash
# Security demo INSIDE the running OpenShell sandbox: each line should print ALLOW or DENY.
set -uo pipefail
NAME="${SANDBOX_NAME:-reroute-agent}"
py() { openshell sandbox exec -n "$NAME" -- python -c "$1"; }

HTTP='import sys,urllib.request as u
try:
    r=u.urlopen(u.Request(sys.argv[1],method=sys.argv[2],data=b"{}" if sys.argv[2]=="POST" else None,headers={"content-type":"application/json"}),timeout=5); print("ALLOW",r.status,sys.argv[1])
except u.HTTPError as e: print("ALLOW (HTTP %s from upstream)"%e.code if e.code not in (403,) else "DENY 403", sys.argv[1])
except Exception as e: print("DENY",type(e).__name__,sys.argv[1])'

run() { openshell sandbox exec -n "$NAME" -- python -c "$HTTP" "$1" "$2"; }

echo "--- network ---"
run http://airline-service:8000/api/flights/KE123 GET                 # expect ALLOW
run http://reroute-api:8000/api/optimization/rebooking POST           # expect ALLOW (422 from API is fine)
run https://unknown-external-api.com/passengers GET                   # expect DENY (no policy)
run http://reroute-api:8000/api/rebooking/plans/any/approve POST      # expect DENY (deny_rule)
echo "--- filesystem ---"
openshell sandbox exec -n "$NAME" -- cat /root/.ssh/id_rsa >/dev/null 2>&1 && echo "ALLOW cat ~/.ssh/id_rsa" || echo "DENY  cat ~/.ssh/id_rsa"
openshell sandbox exec -n "$NAME" -- sh -c 'echo x > /app/documents/pwned.md' 2>/dev/null && echo "ALLOW write /app" || echo "DENY  write /app (read-only)"
