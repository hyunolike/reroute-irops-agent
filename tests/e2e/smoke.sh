#!/usr/bin/env bash
# End-to-end smoke test of the MVP Definition of Done against a running stack.
#   ./tests/e2e/smoke.sh [API_BASE]     (default http://localhost:8000)
set -euo pipefail
API="${1:-${API:-http://localhost:8000}}"
j() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }
step() { printf "\033[32m✓\033[0m %s\n" "$*"; }
fail() { printf "\033[31m✗ %s\033[0m\n" "$*"; exit 1; }

curl -fsS "$API/api/health" >/dev/null || fail "API not reachable at $API"
curl -fsS -X POST "$API/api/demo/reset" >/dev/null && step "demo data reset"

TASK=$(curl -fsS -X POST "$API/api/agent/tasks" -H 'content-type: application/json' \
  -d '{"command":"KE123편이 결항됐어. 영향 승객을 확인하고 최적 재배정안을 만들어줘."}' | j "d['id']")
step "agent task $TASK created"

for _ in $(seq 1 90); do
  STATE=$(curl -fsS "$API/api/agent/tasks/$TASK" | j "d['state']")
  [[ "$STATE" == WAITING_APPROVAL || "$STATE" == FAILED ]] && break; sleep 1
done
[[ "$STATE" == WAITING_APPROVAL ]] || fail "expected WAITING_APPROVAL, got $STATE"
PLAN=$(curl -fsS "$API/api/agent/tasks/$TASK" | j "d['plan_id']")
TOOLS=$(curl -fsS "$API/api/agent/tasks/$TASK/events?stream=false" | j "' '.join(dict.fromkeys(e['detail']['tool'] for e in d['events'] if e['type']=='TOOL_CALL'))")
step "tools used: $TOOLS"
SUMMARY=$(curl -fsS "$API/api/rebooking/plans/$PLAN" | j "'affected=%(affected)s auto=%(auto_assigned)s review=%(manual_review)s none=%(no_feasible)s solver=%(provider)s' % d['summary']")
step "plan $PLAN: $SUMMARY"

CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/rebooking/plans/$PLAN/execute")
[[ "$CODE" == 403 ]] || fail "execution without approval returned $CODE (expected 403)"
step "execution without approval blocked (HTTP 403)"

curl -fsS -X POST "$API/api/rebooking/plans/$PLAN/approve" -H 'content-type: application/json' \
  -H 'X-Operator-Id: smoke.operator' -d '{"comment":"smoke test"}' >/dev/null && step "operator approved"
for _ in $(seq 1 60); do
  STATE=$(curl -fsS "$API/api/agent/tasks/$TASK" | j "d['state']")
  [[ "$STATE" == COMPLETED || "$STATE" == FAILED ]] && break; sleep 1
done
[[ "$STATE" == COMPLETED ]] || fail "expected COMPLETED, got $STATE"
step "report: $(curl -fsS "$API/api/agent/tasks/$TASK" | j "'rebooked=%(rebooked)s held=%(held_for_operator)s no_feasible=%(no_feasible)s' % d['report']")"

DENY=$(curl -fsS -X POST "$API/api/security/probes" -H 'content-type: application/json' \
  -d '{"kind":"network","method":"GET","url":"https://unknown-external-api.com/passengers"}' | j "d['result']")
[[ "$DENY" == DENY ]] || fail "egress probe was $DENY"
step "unknown external egress denied by sandbox policy"
step "audit entries: $(curl -fsS "$API/api/audit?limit=1000" | j "len(d['entries'])")"
echo "SMOKE TEST PASSED"
