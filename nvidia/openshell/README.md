# NVIDIA OpenShell — agent sandbox

`policies/reroute-agent.yaml` is the **single source of truth** for what the ReRoute agent runtime may do.
It uses only keys documented in the OpenShell policy schema (`version`, `filesystem_policy`, `landlock`,
`process`, `network_policies` with `endpoints[].rules` / `deny_rules` and `binaries`).

| Capability | Policy | Why |
|---|---|---|
| `GET airline-service /api/flights/**` | `airline_api` | read flight status, manifest, inventory |
| `POST airline-service /api/bookings/rebookings` | `airline_api` | allowed on the wire, but the Booking API rejects it without a human-approved, signed token |
| `GET reroute-api /api/policies/search`, `POST /api/optimization/rebooking` | `reroute_services` | policy RAG (NeMo Retriever) and optimization (cuOpt) services |
| `GET/POST/PATCH reroute-api /internal/agent/**` | `reroute_services` | report events, create plans, request an execution grant |
| `POST reroute-api /api/rebooking/plans/**` | `deny_rules` | **the agent can never approve, reject or execute its own plan** |
| `POST integrate.api.nvidia.com /v1/chat/completions` | `nvidia_inference` | Nemotron via NIM — nothing else on that host |
| everything else | — | deny by default (e.g. `unknown-external-api.com`, cloud metadata `169.254.169.254`) |
| filesystem | `filesystem_policy` | `/app` read-only (code, policies), writes only to `/sandbox/work`, `/tmp`; `~/.ssh` is unreachable |
| process | `process` | runs as `sandbox`, never root |

## Two ways the policy is enforced

1. **OpenShell mode (real)** – `scripts/create-sandbox.sh` starts `python -m app.agent.worker` inside an
   OpenShell sandbox with this policy (`SECURITY_RUNTIME=openshell`). The worker has **no database
   credentials** and **no approval-signing key**; the NVIDIA key is injected by an OpenShell provider.
   `scripts/probes.sh` demonstrates ALLOW/DENY from inside the sandbox and `openshell term` shows live
   decisions. `scripts/ingest-logs.sh` imports OpenShell's OCSF decisions into ReRoute's audit log
   (`enforced_by=openshell`).
2. **Policy-mirror mode (demo/dev)** – when OpenShell is not available (e.g. on a presenter laptop), ReRoute
   evaluates *the same YAML* in-process before every agent egress (`app/security/policy.py`) and records each
   decision in the audit log with `enforced_by=policy-mirror`. The UI labels this mode explicitly — it is a
   pre-check, not kernel/proxy enforcement.

Tests: `apps/api/tests/test_security.py` validates the file against the documented schema rules
(`access`/`rules` exclusivity, non-root, no `/` read-write, unknown keys rejected) and checks every
decision in the table above.
