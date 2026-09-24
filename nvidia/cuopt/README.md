# NVIDIA cuOpt

ReRoute's final passenger allocation is computed by a MILP solver, never by the LLM.
Production/demo default is NVIDIA cuOpt (`OPTIMIZATION_PROVIDER=cuopt`); the HiGHS CPU solver is a labelled
development fallback that solves the **same** `MilpProblem` object.

## Run a cuOpt server

```bash
# GPU host with NVIDIA Container Toolkit
docker compose --profile gpu up -d cuopt          # nvidia/cuopt:latest-cu12, port 5000
curl -s localhost:5000/cuopt/health               # 200 when ready
# then in .env:  OPTIMIZATION_PROVIDER=cuopt  CUOPT_BASE_URL=http://cuopt:5000
```

Alternatives: `pip install --extra-index-url=https://pypi.nvidia.com 'cuopt-server-cu12==26.8.*'` and
`python -m cuopt_server.cuopt_service --ip 0.0.0.0 --port 5000`, or the NGC image `nvcr.io/nvidia/cuopt/cuopt`.

## Try the exact demo problem

`ke123-milp.json` is the KE123 re-accommodation model (148 binary variables, 41 constraints) exported with
`python -m app.optimization.export_payload` in the cuOpt server LP/MILP format:

```bash
REQ=$(curl -s -X POST localhost:5000/cuopt/request -H 'Content-Type: application/json' \
      -H 'CLIENT-VERSION: custom' -d @nvidia/cuopt/ke123-milp.json | python3 -c 'import sys,json;print(json.load(sys.stdin).get("reqId",""))')
curl -s localhost:5000/cuopt/solution/$REQ | python3 -m json.tool | head -30
# expected: status Optimal, primal_objective 115367.5 (only y[P010] = 1, i.e. one passenger with no feasible option)
```

The expected optimum was cross-checked with an independent solve of the same JSON (HiGHS).

## Model

- `x[p,f,c]` binary: passenger p on flight f in cabin c; `y[p]` binary: unassigned.
- C1 one outcome per passenger · C2 cabin capacity · C3 business kept when possible (penalty)
  · C4 minimum connection time · C5 same destination · C6 carrier/window allowed by policy.
- Objective weights: `config/optimization.yaml`. Constraint parameters come from *retrieved* policies.
