#!/usr/bin/env bash
# Run the ReRoute agent worker inside an NVIDIA OpenShell sandbox.
# OpenShell is alpha software - verify flags with `openshell sandbox create --help` for your version.
#
# Prerequisites
#   - OpenShell CLI:  curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
#   - Image built:    docker compose build   (produces reroute-api:local)
#   - Control plane running with AGENT_EXECUTION=remote and AGENT_WORKER_TOKEN set
#   - Hostnames `airline-service` and `reroute-api` resolvable from the sandbox (compose network,
#     private DNS, or --add-host) - the policy is written against these logical names.
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${SANDBOX_NAME:-reroute-agent}"
IMAGE="${SANDBOX_IMAGE:-reroute-api:local}"
POLICY="policies/reroute-agent.yaml"
: "${AGENT_WORKER_TOKEN:?set AGENT_WORKER_TOKEN (same value as the control plane)}"

# NVIDIA inference credential lives in an OpenShell provider; the sandbox only sees a placeholder that
# the OpenShell proxy swaps for the real key on the allowed endpoint (integrate.api.nvidia.com).
if ! openshell provider list 2>/dev/null | grep -q nvidia-prod; then
  openshell provider create --name nvidia-prod --type nvidia --from-existing
fi

openshell sandbox create \
  --name "$NAME" \
  --from "$IMAGE" \
  --policy "$POLICY" \
  --provider nvidia-prod \
  -- env \
     SECURITY_RUNTIME=openshell \
     LLM_PROVIDER="${LLM_PROVIDER:-nvidia}" \
     AGENT_WORKER_TOKEN="$AGENT_WORKER_TOKEN" \
     AIRLINE_API_BASE_URL=http://airline-service:8000 \
     REROUTE_API_BASE_URL=http://reroute-api:8000 \
     python -m app.agent.worker

echo "Sandbox '$NAME' started. Live decisions:  openshell term    Logs:  openshell logs $NAME --tail"
