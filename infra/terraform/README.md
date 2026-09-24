# ReRoute on AWS (Terraform)

Single-host production-style deployment, sized for a hackathon demo but built with production habits
(private subnets, no SSH, IMDSv2, encrypted storage, secrets in Secrets Manager, least-privilege IAM).

```
Internet ──► ALB (80/443) ──┬─ /api/*      ──► app host :8000  reroute-api (control plane)
                            ├─ /internal/* ──► 404 (agent-worker API is never public)
                            └─ /*          ──► app host :3000  web (Next.js)
                 private subnet: EC2 app host (g6.xlarge GPU or t3.large)
                   docker compose: airline-service · reroute-api · web · cuopt (GPU)
                 private subnet: RDS PostgreSQL 16 (encrypted, not public)
                 Secrets Manager: DB password · approval signing key · worker token · NVIDIA API key
                 CloudWatch Logs: all containers (awslogs driver)
```

| File | What |
|---|---|
| `network.tf` | VPC, 2 public + 2 private subnets, IGW, single NAT |
| `security_groups.tf` | ALB ← internet; app ← ALB only; DB ← app only |
| `alb.tf` | ALB with SSE-friendly idle timeout, `/api/*` routing, `/internal/*` blocked, optional HTTPS |
| `compute.tf` | EC2 host: AWS Deep Learning Base GPU AMI (driver + Docker + NVIDIA Container Toolkit) or AL2023 |
| `rds.tf` | PostgreSQL 16 |
| `ecr.tf` | `reroute/reroute-api`, `reroute/reroute-web` repositories (scan on push) |
| `secrets.tf` | generated secrets + empty NVIDIA key secret (set out-of-band) |
| `iam.tf` | instance role: SSM, ECR read, *only* its own secrets, its own log group |
| `templates/` | host bootstrap (no secrets inside) and production compose file |

## Deploy

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars      # git-ignored
terraform init
terraform apply -target=aws_ecr_repository.repo   # 1) create registries first

# 2) build & push images (from repo root)
make push AWS_REGION=ap-northeast-2

# 3) set the NVIDIA key (never in tfvars / git)
aws secretsmanager put-secret-value \
  --secret-id "$(terraform output -raw nvidia_api_key_secret_arn)" --secret-string 'nvapi-...'

# 4) everything else
terraform apply
terraform output url
```

Updating the app after pushing new images: `make redeploy` (runs `docker compose pull && up -d` on the
host through SSM Run Command). Shell access: `aws ssm start-session --target $(terraform output -raw instance_id)`.

Cost note: `g6.xlarge` + NAT + ALB + RDS micro costs roughly a few USD per hour of uptime; `terraform destroy`
after the demo, or use `enable_gpu = false` (CPU fallback solver, clearly labelled in the UI).

Validated with `terraform fmt -check` and `terraform validate` (AWS provider 5.80). Not applied from CI.

## Production next steps

ECS/EKS with GPU node groups for cuOpt, Multi-AZ RDS, one NAT per AZ, WAF on the ALB, OpenShell-sandboxed
agent workers (`AGENT_EXECUTION=remote`), and OIDC/SSO for operator identity instead of the `X-Operator-Id` header.
