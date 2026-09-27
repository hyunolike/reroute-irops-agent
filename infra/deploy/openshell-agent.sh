#!/usr/bin/env bash
# Runs the ReRoute agent worker inside an NVIDIA OpenShell sandbox on the AWS app host (enable_openshell = true).
# Installed to /opt/openshell/reroute/ by the Terraform bootstrap, which also places the policy and provider
# profiles next to it. Idempotent: the bootstrap runs `install` then `start`; infra/deploy/deploy.sh runs `start`
# after every rollout so the sandbox follows the new image.
#
#   openshell-agent.sh install          Docker >= 28, OpenShell gateway (systemd), CLI registration, providers
#   openshell-agent.sh start [IMAGE]    (re)create the worker sandbox; IMAGE defaults to the compose reroute-api image
#
# Credentials never enter the sandbox: NVIDIA_API_KEY and AGENT_WORKER_TOKEN are OpenShell providers read from
# /opt/reroute/.env (written from Secrets Manager at boot); the sandbox sees placeholders the proxy swaps on egress.
set -euo pipefail

OPENSHELL_VERSION="${OPENSHELL_VERSION:-0.1.1}"
DOCKER_VERSION="${DOCKER_VERSION:-29.8.1}" # >= 28.0: copying into containers with a numeric User (moby#34143)
DIR=/opt/openshell/reroute
BIN=/opt/openshell/bin
APP_DIR=/opt/reroute
SANDBOX=reroute-agent
export HOME=/root

log() { printf '[openshell-agent] %s\n' "$*"; }

control_plane_host() {
  # The host's VPC DNS name: resolvable by the OpenShell supervisor (which uses upstream DNS, not /etc/hosts)
  # and a private address, which OpenShell permits for an exact hostname. Loopback is always refused.
  local t name=""
  if t=$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null); then
    name=$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $t" http://169.254.169.254/latest/meta-data/local-hostname 2>/dev/null || true)
  fi
  echo "${name:-$(hostname -f)}"
}

ensure_docker() {
  local major
  major=$(docker version --format '{{.Server.Version}}' | cut -d. -f1)
  if ((major >= 28)); then return; fi
  log "Docker engine major $major < 28: installing static Docker $DOCKER_VERSION (moby#34143)"
  local tmp backup=/root/docker-pre-openshell
  tmp=$(mktemp -d)
  curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-${DOCKER_VERSION}.tgz" | tar -xz -C "$tmp"
  mkdir -p "$backup"
  systemctl stop docker.socket docker containerd 2>/dev/null || systemctl stop docker
  for f in "$tmp"/docker/*; do
    local n p
    n=$(basename "$f")
    p=$(command -v "$n" || echo "/usr/bin/$n")
    [[ -f "$p" ]] && cp -a "$p" "$backup/"
    install -m 0755 "$f" "$p"
  done
  rm -rf "$tmp"
  # Keep the distro package manager from rolling the engine back to the packaged version.
  if [[ -f /etc/dnf/dnf.conf ]] && ! grep -q '^excludepkgs=.*docker' /etc/dnf/dnf.conf; then
    echo 'excludepkgs=docker*,containerd*,runc*' >>/etc/dnf/dnf.conf
  fi
  systemctl start containerd 2>/dev/null || true
  systemctl start docker
  log "Docker $(docker version --format '{{.Server.Version}}')"
}

install_openshell() {
  if [[ ! -x "$BIN/openshell-gateway" ]] || ! "$BIN/openshell" --version | grep -q "$OPENSHELL_VERSION"; then
    # The release RPM depends on podman (absent on Amazon Linux 2023), so use the standalone binaries.
    local arch base a
    arch=$(uname -m)
    base="https://github.com/NVIDIA/OpenShell/releases/download/v${OPENSHELL_VERSION}"
    mkdir -p "$BIN"
    for a in "openshell-${arch}-unknown-linux-musl" "openshell-gateway-${arch}-unknown-linux-gnu" \
      "openshell-supervisor-${arch}-unknown-linux-gnu" "openshell-sandbox-${arch}-unknown-linux-musl"; do
      curl -fsSL "$base/$a.tar.gz" | tar -xz -C "$BIN"
    done
    ln -sf "$BIN/openshell" /usr/local/bin/openshell
  fi

  mkdir -p /root/.config/openshell /root/.local/state/openshell/tls
  cat >/root/.config/openshell/gateway.toml <<'EOF'
[openshell]
version = 2

[openshell.gateway]
compute_driver = "docker"
EOF
  local tls=/root/.local/state/openshell/tls
  [[ -f "$tls/ca.crt" ]] || "$BIN/openshell-gateway" generate-certs --output-dir "$tls" --server-san host.openshell.internal

  cat >/etc/systemd/system/openshell-gateway.service <<EOF
[Unit]
Description=OpenShell gateway (ReRoute agent sandbox)
After=docker.service
Requires=docker.service

[Service]
Type=exec
Environment=HOME=/root
Environment=OPENSHELL_LOCAL_TLS_DIR=$tls
ExecStart=$BIN/openshell-gateway --config /root/.config/openshell/gateway.toml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  # Recreates the worker sandbox after a reboot (and whenever the gateway restarts with Docker).
  cat >/etc/systemd/system/reroute-agent-sandbox.service <<EOF
[Unit]
Description=ReRoute agent worker in an OpenShell sandbox
After=openshell-gateway.service
BindsTo=openshell-gateway.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$DIR/openshell-agent.sh start

[Install]
WantedBy=openshell-gateway.service
EOF
  systemctl daemon-reload
  systemctl enable --now openshell-gateway
  systemctl enable reroute-agent-sandbox

  local mtls=/root/.config/openshell/gateways/openshell/mtls
  for _ in $(seq 1 30); do "$BIN/openshell" gateway add https://localhost:17670 --local --name openshell >/dev/null 2>&1 && break; sleep 2; done
  mkdir -p "$mtls"
  install -m 0644 "$tls/ca.crt" "$mtls/ca.crt"
  install -m 0644 "$tls/client/tls.crt" "$mtls/tls.crt"
  install -m 0600 "$tls/client/tls.key" "$mtls/tls.key"
  for _ in $(seq 1 30); do openshell status 2>/dev/null | grep -q Connected && break; sleep 2; done
}

install_providers() {
  local host
  host=$(control_plane_host)
  sed "s/__CONTROL_PLANE_HOST__/$host/" "$DIR/reroute-worker.yaml" >"$DIR/reroute-worker.rendered.yaml"
  openshell provider profile import -f "$DIR/nvidia-reroute.yaml" >/dev/null 2>&1 || true
  openshell provider profile import -f "$DIR/reroute-worker.rendered.yaml" >/dev/null 2>&1 || true
  (
    set -a
    # shellcheck disable=SC1091
    . "$APP_DIR/.env"
    set +a
    [[ -n "${AGENT_WORKER_TOKEN:-}" ]] || { log "AGENT_WORKER_TOKEN missing in $APP_DIR/.env"; exit 1; }
    for p in "nvidia-prod:nvidia-reroute" "reroute-worker:reroute-worker"; do
      name=${p%%:*} type=${p##*:}
      if [[ "$name" == nvidia-prod && -z "${NVIDIA_API_KEY:-}" ]]; then continue; fi
      openshell provider update "$name" --from-existing >/dev/null 2>&1 ||
        openshell provider create --name "$name" --type "$type" --from-existing >/dev/null
    done
  )
  log "providers: $(openshell provider list 2>/dev/null | awk 'NR>1{print $1}' | xargs)"
}

render_policy() {
  # Same rules as nvidia/openshell/policies/reroute-agent.yaml; only the logical compose hostnames change to the
  # host's VPC name (airline API published on host port 8001, control plane on 8000).
  python3 - "$1" "$DIR/reroute-agent.yaml" "$DIR/reroute-agent.rendered.yaml" <<'EOF'
import sys
host, src, dst = sys.argv[1:]
s = open(src).read()
for old, new in (("host: airline-service\n        port: 8000", f"host: {host}\n        port: 8001"),
                 ("host: reroute-api\n        port: 8000", f"host: {host}\n        port: 8000")):
    assert s.count(old) == 1, old
    s = s.replace(old, new)
open(dst, "w").write(s)
EOF
}

start_sandbox() {
  local image=${1:-} host providers
  for _ in $(seq 1 60); do openshell status 2>/dev/null | grep -q Connected && break; sleep 2; done
  [[ -n "$image" ]] || image=$(grep -oE 'image: [^ ]+/reroute-api:[^ ]+' "$APP_DIR/docker-compose.yml" | head -1 | cut -d' ' -f2)
  host=$(control_plane_host)
  render_policy "$host"
  providers=(--provider reroute-worker)
  openshell provider get nvidia-prod >/dev/null 2>&1 && providers+=(--provider nvidia-prod)
  openshell sandbox delete "$SANDBOX" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do openshell sandbox list 2>/dev/null | grep -q "^$SANDBOX " || break; sleep 2; done
  openshell sandbox create --name "$SANDBOX" --from "$image" --policy "$DIR/reroute-agent.rendered.yaml" \
    "${providers[@]}" --detach --no-tty -- sh -c \
    "cd /app && exec env PROJECT_ROOT=/app SECURITY_RUNTIME=openshell LLM_PROVIDER=auto \
AIRLINE_API_BASE_URL=http://$host:8001 REROUTE_API_BASE_URL=http://$host:8000 /app/.venv/bin/python -m app.agent.worker" >/dev/null
  for _ in $(seq 1 60); do
    if openshell sandbox list 2>/dev/null | grep -qE "^$SANDBOX .*Ready"; then
      log "sandbox $SANDBOX Ready ($image)"
      return 0
    fi
    openshell sandbox list 2>/dev/null | grep -qE "^$SANDBOX .*Error" && break
    sleep 3
  done
  log "sandbox $SANDBOX did not become Ready"
  openshell logs "$SANDBOX" --since 5m 2>&1 | tail -20
  return 1
}

case "${1:-}" in
  install)
    ensure_docker
    install_openshell
    install_providers
    ;;
  start) start_sandbox "${2:-}" ;;
  *)
    echo "usage: $0 install | start [IMAGE]" >&2
    exit 2
    ;;
esac
