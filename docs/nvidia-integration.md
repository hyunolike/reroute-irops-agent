# NVIDIA integration notes

[🇰🇷 국문 README의 NVIDIA 절](../README.md#왜-nvidia인가) · **🇺🇸 English**

All interfaces below were taken from NVIDIA's own sources (repositories' `docs/` folders, which build
docs.nvidia.com, the NVIDIA skills catalog, and PyPI) — not guessed. Status legend:
**Verified** = exercised by this repo's tests/runs · **Contract-tested** = request/response shape tested against
a mock of the documented API · **Documented** = written from official docs, not executed here.

| Technology | Role in ReRoute | Interface used | Status |
|---|---|---|---|
| **Nemotron via NIM** | goal interpretation, planning, tool selection, operator briefing, exception narrative | `POST https://integrate.api.nvidia.com/v1/chat/completions`, `tools` + `tool_choice: "auto"`, `message.tool_calls`; Nemotron 3: `chat_template_kwargs.enable_thinking`; Llama-Nemotron v1.5: `/think`·`/no_think` | Contract-tested (`test_agent.py`), incl. fallback when NIM fails. Needs `NVIDIA_API_KEY` for live runs |
| **NeMo Retriever** | policy search with provenance | `POST /v1/embeddings` (`input_type` passage/query) with `nvidia/llama-nemotron-embed-1b-v2`; `POST https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-nemotron-rerank-1b-v2/reranking` → `rankings[{index, logit}]` | Documented + implemented (`rag/nvidia.py`); lexical fallback verified |
| **cuOpt** | constrained passenger allocation (MILP) | cuOpt server REST: `POST /cuopt/request` (`CLIENT-VERSION: custom`) → `reqId`, poll `GET /cuopt/solution/{reqId}` → `response.solver_response.{status, solution.primal_solution, primal_objective}`; CSR LP/MILP data model, `variable_types: "I"` | Contract-tested (`test_optimization.py`); exported payload cross-checked to the same optimum. Needs a GPU host for live runs |
| **OpenShell** | agent sandbox: egress, L7 rules, filesystem, process | Policy schema `version: 1`, `filesystem_policy`, `landlock`, `process`, `network_policies.*.{endpoints[].rules/deny_rules, binaries}`; CLI `openshell sandbox create --policy`, `openshell logs`, `openshell term`; OCSF log lines | Policy validated against documented rules + decision tests; OCSF log parser tested with documented lines; sandbox scripts Documented |
| **NemoClaw** | governed runtime for a conversational operator copilot that drives ReRoute | policy preset (`preset:` + `network_policies`), `nemoclaw <name> policy add --from-file`, `nemoclaw <name> skill install <dir>` (`SKILL.md` with `name`) | Documented |
| **NVIDIA Skills** | reference implementations for cuOpt formulation/server API and RAG | `cuopt-numerical-optimization-formulation`, `cuopt-server-api-python`, `nemo-retriever`, `rag-blueprint` | Used as design references |

## Enabling real NVIDIA mode

```bash
cp .env.example .env
# NVIDIA_API_KEY=nvapi-...            (https://build.nvidia.com)
# LLM_PROVIDER=nvidia
# RETRIEVER_PROVIDER=nvidia
# OPTIMIZATION_PROVIDER=cuopt          (GPU host)
docker compose --profile gpu up --build
```

`GET /api/system/runtime` and the dashboard's runtime chips show what is actually active. If a provider is
configured but unreachable, ReRoute falls back **visibly**: the event log records a `GUARDRAIL` event
("Nemotron unavailable → deterministic planner fallback", "NVIDIA cuOpt unavailable; solved with CPU fallback"),
and badges switch to amber. Nothing is labelled NVIDIA unless it ran on NVIDIA.

## Why each technology is necessary (not decorative)

- **Nemotron/NIM** – the workflow branches (cancelled vs. delayed below threshold vs. unknown flight vs.
  incomplete policy coverage). A function-calling model chooses the path; the orchestrator keeps it safe.
- **NeMo Retriever** – rules change per airline and market. Constraints are *compiled from retrieved text*
  (`policy-params`), so updating a policy document changes solver behaviour without touching code, and every
  allocation cites its policy IDs.
- **cuOpt** – allocation is combinatorial (148 binary variables for 35 passengers here; thousands in a hub-wide
  IROPS event with many flights). A MILP solver gives optimal, constraint-satisfying answers that an LLM cannot
  guarantee; GPU acceleration matters when many disrupted flights are re-optimized together.
- **OpenShell** – the agent holds a model API key and can call booking endpoints; its blast radius must be
  bounded by policy outside the agent's own code.
- **NemoClaw** – packages the governed agent for operations teams (onboarding, policy presets, skills, lifecycle).

## Known limits / next steps

- NemoClaw and OpenShell are alpha; command flags may change between versions (scripts note this).
- Remote worker hostnames (`airline-service`, `reroute-api`) must resolve inside the sandbox.
- Operator identity is a header in the demo; production would use OIDC and per-role approval limits.
- Multi-flight (hub-wide) IROPS optimization in a single cuOpt model is the natural extension.
