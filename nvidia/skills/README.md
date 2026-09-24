# NVIDIA Agent Skills

ReRoute was built with the official NVIDIA skills catalog (https://github.com/NVIDIA/skills,
`SKILL.md` format) as the reference for API usage, instead of guessing interfaces:

| NVIDIA skill | How it shaped ReRoute |
|---|---|
| `cuopt-numerical-optimization-formulation` | Decision variables / constraints / objective of the re-accommodation 0-1 ILP (`apps/api/app/optimization/formulation.py`) |
| `cuopt-server-api-python` | REST protocol used by `CuOptOptimizationProvider`: `POST /cuopt/request` with `CLIENT-VERSION: custom`, poll `GET /cuopt/solution/{reqId}`, CSR `csr_constraint_matrix`, `variable_types`, `solver_config.time_limit` |
| `nemo-retriever`, `rag-blueprint` | Retrieval design: embed (passage/query `input_type`) + rerank NIM, provenance per hit (`apps/api/app/rag/nvidia.py`) |
| `nemoclaw-user-guide` (NemoClaw repo) | NemoClaw preset / skill packaging in `nvidia/nemoclaw/` |
| `generate-sandbox-policy`, `openshell-cli` (OpenShell repo) | Policy structure and CLI flow in `nvidia/openshell/` |

Install them into your coding agent to review or extend this project:

```bash
npx skills add nvidia/skills --skill cuopt-numerical-optimization-formulation --yes
npx skills add nvidia/skills --skill cuopt-server-api-python --yes
npx skills add nvidia/skills --skill nemo-retriever --yes
```

`reroute-irops/` is ReRoute's own skill (NemoClaw-installable) that teaches an assistant to operate ReRoute
safely: start tasks, read plans and evidence, never approve.
