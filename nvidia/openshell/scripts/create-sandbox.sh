#!/usr/bin/env bash
# Run the ReRoute agent worker inside an NVIDIA OpenShell sandbox on a workstation (`make sandbox`).
# On the AWS host the same thing is automated by infra/deploy/openshell-agent.sh (enable_openshell = true).
#
# Prerequisites (verified with OpenShell 0.1.1)
#   - OpenShell CLI + gateway:  curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
#   - Docker Engine >= 28 for the Docker driver (older engines fail with `getent unable to find entry "1000:1000"`,
#     moby#34143). On macOS with an older Docker Desktop, use the MicroVM driver instead: compute_driver = "vm" in the
#     gateway TOML and `brew install e2fsprogs`.
#   - Image built:  docker compose build   (produces reroute-api:local)
#   - Control plane running with AGENT_EXECUTION=remote and AGENT_WORKER_TOKEN set, serving both roles on one port
#     (APP_ROLE=all) or with the airline API published separately.
#   - CONTROL_PLANE_HOST: a name the sandbox can reach. OpenShell resolves DNS on the host through upstream DNS
#     (/etc/hosts is ignored) and always refuses loopback, so use a private-IP name such as
#     reroute-api.<your-lan-ip-dashed>.sslip.io, or the VPC DNS name on a cloud host.
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${SANDBOX_NAME:-reroute-agent}"
IMAGE="${SANDBOX_IMAGE:-reroute-api:local}"
: "${CONTROL_PLANE_HOST:?set CONTROL_PLANE_HOST (see the header)}"
: "${AGENT_WORKER_TOKEN:?set AGENT_WORKER_TOKEN (same value as the control plane)}"
AIRLINE_PORT="${AIRLINE_PORT:-8000}"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Same rules as policies/reroute-agent.yaml; only the logical compose hostnames change.
python3 - "$CONTROL_PLANE_HOST" "$AIRLINE_PORT" policies/reroute-agent.yaml "$WORK/policy.yaml" <<'EOF'
import sys
host, airline_port, src, dst = sys.argv[1:]
s = open(src).read()
s = s.replace("host: airline-service\n        port: 8000", f"host: {host}\n        port: {airline_port}")
s = s.replace("host: reroute-api\n        port: 8000", f"host: {host}\n        port: 8000")
open(dst, "w").write(s)
EOF
sed "s/__CONTROL_PLANE_HOST__/$CONTROL_PLANE_HOST/" providers/reroute-worker.yaml >"$WORK/reroute-worker.yaml"

# Credentials never enter the sandbox: providers hold them and the OpenShell proxy substitutes the placeholders
# the sandbox sees - the NVIDIA key only on /v1/chat/completions, the worker token only on control-plane paths.
openshell provider profile import -f providers/nvidia-reroute.yaml >/dev/null 2>&1 || true
openshell provider profile import -f "$WORK/reroute-worker.yaml" >/dev/null 2>&1 || true
providers=(--provider reroute-worker)
openshell provider update reroute-worker --from-existing >/dev/null 2>&1 ||
  openshell provider create --name reroute-worker --type reroute-worker --from-existing
if [[ -n "${NVIDIA_API_KEY:-}" ]]; then
  openshell provider update nvidia-prod --from-existing >/dev/null 2>&1 ||
    openshell provider create --name nvidia-prod --type nvidia-reroute --from-existing
  providers+=(--provider nvidia-prod)
fi

openshell sandbox delete "$NAME" >/dev/null 2>&1 || true
# The sandbox workdir is /sandbox, so start from /app where the package lives.
openshell sandbox create \
  --name "$NAME" \
  --from "$IMAGE" \
  --policy "$WORK/policy.yaml" \
  "${providers[@]}" \
  --detach --no-tty \
  -- sh -c "cd /app && exec env PROJECT_ROOT=/app SECURITY_RUNTIME=openshell LLM_PROVIDER=auto \
AIRLINE_API_BASE_URL=http://$CONTROL_PLANE_HOST:$AIRLINE_PORT REROUTE_API_BASE_URL=http://$CONTROL_PLANE_HOST:8000 \
/app/.venv/bin/python -m app.agent.worker"

echo "Sandbox '$NAME' started. Decisions:  openshell logs $NAME --since 5m    Shell:  openshell sandbox exec --name $NAME -- sh"
