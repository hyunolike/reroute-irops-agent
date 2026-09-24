# NVIDIA NemoClaw + OpenClaw ↔ ReRoute (MCP)

**🇰🇷 요약:** NemoClaw 샌드박스 안의 **OpenClaw**가 두뇌(계획·도구 선택)가 되고, ReRoute가 제공하는 **MCP 도구**로 결항 복구
업무를 수행합니다. 모든 호출은 ReRoute의 가드레일·상태 머신·감사 로그를 거치고, 예약 변경은 사람이 ReRoute 콘솔에서 승인해야 합니다.

```
운영자 ─대화─▶ OpenClaw  (NemoClaw 샌드박스 · OpenShell · Nemotron 추론)
                  │  MCP (Streamable HTTP, HTTPS, Bearer)  ← OpenShell이 protocol: mcp 정책으로 검사
                  ▼
            ReRoute /mcp ── 같은 오케스트레이터(검증·사전조건·상태·이벤트) ── 항공사 API · RAG · cuOpt
                  │
            승인 게이트웨이 ── 사람만 승인 (MCP에는 승인/실행 도구가 없음)
```

## What NemoClaw and OpenClaw are

- **OpenClaw** – an open-source, always-on assistant agent (chat, skills, MCP tools). It is NemoClaw's default agent.
- **NemoClaw** (github.com/NVIDIA/NemoClaw, alpha) – NVIDIA's reference stack that runs such agents **inside NVIDIA
  OpenShell sandboxes** with managed inference (Nemotron), policy presets, skills and MCP registration.

## ReRoute MCP server

Enabled when `REROUTE_MCP_TOKEN` is set on the control plane; served at `POST /mcp` (Streamable HTTP, JSON responses,
stateless). Implementation: `apps/api/app/integrations/mcp_server.py` (official MCP Python SDK 2.x).

| MCP tool | Purpose |
|---|---|
| `open_recovery_task(instruction)` | start a task the external agent will plan; appears live on the dashboard |
| `log_reasoning(task_id, note)` | show the agent's reasoning on the dashboard timeline |
| `get_disrupted_flight`, `get_affected_passengers`, `search_alternative_flights` | facts from the airline system |
| `search_rebooking_policy(task_id, queries)` | policy RAG with `coverage` (missing rules + suggested queries) |
| `optimize_rebooking` | solver allocation (cuOpt); cannot be changed by the agent |
| `explore_exception_options` | options and blocking constraints for exception passengers |
| `propose_rebooking` | plan + human approval request (closes the planning session) |
| `finish_without_action(task_id, reason)` | refused unless facts and policies justify it |
| `delegate_recovery(instruction)` | hand the whole task to ReRoute's own agent instead |
| `get_recovery_status(task_id)` | state, plan summary, exceptions, approval status |

There is **no** approve / reject / execute tool. Calls are authenticated with `Authorization: Bearer <REROUTE_MCP_TOKEN>`,
audited (`action = mcp.tool_call`), and go through the same validation, preconditions and state machine as ReRoute's agent.

## Register ReRoute with a NemoClaw sandbox

Requirements from NemoClaw's `mcp add` (docs: `docs/manage-sandboxes/add-mcp-server.mdx`): the endpoint must be
**HTTPS** and take **one bearer credential**; private addresses need `--trusted-private-host`; loopback is rejected.
The AWS deployment (`infra/terraform`, with `public_hostname` + `certificate_arn`) provides such an endpoint.

```bash
# 1. NemoClaw with an OpenClaw sandbox (Nemotron inference)
curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash
nemoclaw onboard --name ops-copilot

# 2. Register ReRoute's MCP server - the token is read from the environment, never passed inline
export REROUTE_MCP_TOKEN="$(cd infra/terraform && eval "$(terraform output -raw mcp_token_command)")"
nemoclaw ops-copilot mcp add reroute --url "$(cd infra/terraform && terraform output -raw mcp_url)" --env REROUTE_MCP_TOKEN
unset REROUTE_MCP_TOKEN
#    optional: forbid delegating to ReRoute's own agent, enforced at the OpenShell MCP proxy
#    nemoclaw ops-copilot mcp update reroute --deny-tool delegate_recovery

# 3. Check policy readiness and in-sandbox reachability
nemoclaw ops-copilot mcp status reroute

# 4. Teach OpenClaw the workflow
nemoclaw ops-copilot skill install nvidia/skills/reroute-irops

# 5. Talk to it
nemoclaw ops-copilot connect
#   > "KE123편이 결항됐어. 재배정안 만들어서 승인 요청까지 해줘"
#   The ReRoute dashboard shows "외부 에이전트가 시작한 작업" - click it to watch OpenClaw's tool calls live,
#   then approve or reject as the operator.
```

NemoClaw generates a narrow OpenShell `protocol: mcp` policy for that endpoint (exact host, path, MCP methods, request-size
limit) and injects the bearer token at egress, so the raw token never sits in the sandbox.

## Verify the endpoint before connecting NemoClaw

```bash
REROUTE_MCP_TOKEN=... make mcp-smoke MCP_URL=https://reroute.example.com/mcp
```

`app.integrations.mcp_smoke` behaves like an external agent (list tools → open task → … → propose) and never approves.

## What is verified vs. documented

| Item | Status |
|---|---|
| ReRoute MCP server: tools, bearer auth, guardrails, no approval power, human approval afterwards | **Verified** – `apps/api/tests/test_mcp.py` over real Streamable HTTP (legacy handshake and modern protocol) + `mcp_smoke` against a live server |
| Dashboard following an MCP-started task live | **Verified** in the browser (`docs/screenshots/09-*`, `10-*`) |
| `nemoclaw … mcp add / status / skill install` with a real OpenClaw sandbox | **Documented only** – written from NemoClaw's docs (alpha); not executed here (no NemoClaw/Docker/HTTPS endpoint in this environment) |
