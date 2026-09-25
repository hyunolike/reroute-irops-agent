# ReRoute - common tasks
SHELL := /bin/bash
API := apps/api
WEB := apps/web
AWS_REGION ?= ap-northeast-2
TAG ?= latest
TF := infra/terraform

.PHONY: mcp-smoke eval-llm help up up-gpu up-worker down logs reset smoke test lint fmt dev-api dev-web install export-cuopt push deploy redeploy tf-validate sandbox probes

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[32m%-14s\033[0m %s\n", $$1, $$2}'

up: ## Demo stack (mock LLM, BM25, CPU solver, policy mirror) -> http://localhost:3000
	docker compose up --build -d
	@echo "Dashboard: http://localhost:3000   API docs: http://localhost:8000/docs"

up-gpu: ## Stack + NVIDIA cuOpt server (GPU host); set OPTIMIZATION_PROVIDER=cuopt in .env
	docker compose --profile gpu up --build -d

up-worker: ## Agent as a separate DB-less worker (AGENT_EXECUTION=remote + AGENT_WORKER_TOKEN in .env)
	docker compose --profile worker up --build -d

down: ## Stop everything
	docker compose --profile gpu --profile worker down

logs: ## Follow logs
	docker compose logs -f --tail=100

reset: ## Reseed demo data and clear agent history
	curl -s -X POST http://localhost:8000/api/demo/reset && echo

smoke: ## End-to-end smoke test against a running stack
	./tests/e2e/smoke.sh

install: ## Local dev dependencies
	cd $(API) && uv venv -p 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
	cd $(WEB) && npm ci

test: ## Backend tests
	cd $(API) && .venv/bin/python -m pytest -q

lint: ## Lint + typecheck
	cd $(API) && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests
	cd $(WEB) && npx tsc --noEmit

fmt: ## Format
	cd $(API) && .venv/bin/ruff format app tests && .venv/bin/ruff check --fix app tests

dev-api: ## Run API locally (needs PostgreSQL or DATABASE_URL=sqlite:///./reroute.db)
	cd $(API) && .venv/bin/uvicorn app.asgi:app --reload --port 8000

dev-web: ## Run web locally (proxies /api to :8000)
	cd $(WEB) && npm run dev

eval-llm: ## Score the agent on 4 scenarios with the configured LLM (NVIDIA_API_KEY from the shell or root .env)
	cd $(API) && .venv/bin/python -m app.agent.evaluate

mcp-smoke: ## Exercise an MCP endpoint like an external agent: REROUTE_MCP_TOKEN=... make mcp-smoke MCP_URL=https://host/mcp
	cd $(API) && .venv/bin/python -m app.integrations.mcp_smoke $(or $(MCP_URL),http://localhost:8000/mcp)

export-cuopt: ## Regenerate nvidia/cuopt/ke123-milp.json
	cd $(API) && .venv/bin/python -m app.optimization.export_payload > ../../nvidia/cuopt/ke123-milp.json

sandbox: ## Run the agent worker inside an NVIDIA OpenShell sandbox
	./nvidia/openshell/scripts/create-sandbox.sh

probes: ## OpenShell ALLOW/DENY demo inside the sandbox
	./nvidia/openshell/scripts/probes.sh

push: ## Build and push images to ECR (after `terraform apply -target=aws_ecr_repository.repo`)
	$(eval API_REPO := $(shell cd $(TF) && terraform output -raw ecr_api_repository))
	$(eval WEB_REPO := $(shell cd $(TF) && terraform output -raw ecr_web_repository))
	aws ecr get-login-password --region $(AWS_REGION) | docker login --username AWS --password-stdin $(firstword $(subst /, ,$(API_REPO)))
	docker build --platform linux/amd64 -f $(API)/Dockerfile -t $(API_REPO):$(TAG) .
	docker build --platform linux/amd64 --build-arg API_INTERNAL_URL=http://reroute-api:8000 -t $(WEB_REPO):$(TAG) $(WEB)
	docker push $(API_REPO):$(TAG) && docker push $(WEB_REPO):$(TAG)

deploy: ## Roll the AWS host to TAG via SSM + health check (TAG must be in ECR; an older TAG = rollback)
	IMAGE_TAG=$(TAG) AWS_REGION=$(AWS_REGION) infra/deploy/deploy.sh

redeploy: deploy ## Alias of deploy (kept for older docs)

tf-validate: ## terraform fmt + validate
	cd $(TF) && terraform fmt -check -recursive && terraform init -backend=false -input=false >/dev/null && terraform validate
