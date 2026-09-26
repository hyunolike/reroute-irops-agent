#!/usr/bin/env bash
# Roll the AWS app host to an image tag that is already in ECR, using only the AWS CLI (SSM, no SSH).
# Used by .github/workflows/deploy.yml and by `make deploy TAG=...` - rolling back is deploying an older tag.
#
#   IMAGE_TAG=<git sha> [AWS_REGION=ap-northeast-2] [NAME=reroute] [ENVIRONMENT=demo] infra/deploy/deploy.sh
#
# Resources are found by the names Terraform gives them (infra/terraform), so no Terraform state is needed:
#   instance  tag Name=<name>-<environment>-app     ECR  <name>/reroute-api, <name>/reroute-web
#   ALB       <name>-<environment>
set -euo pipefail

: "${IMAGE_TAG:?IMAGE_TAG is required (a tag already pushed to ECR, e.g. the git commit SHA)}"
REGION="${AWS_REGION:-ap-northeast-2}"
NAME="${NAME:-reroute}"
ENVIRONMENT="${ENVIRONMENT:-demo}"
TIMEOUT_SECONDS="${DEPLOY_TIMEOUT_SECONDS:-900}"
export AWS_REGION="$REGION" AWS_PAGER=""

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$IMAGE_TAG" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || fail "invalid image tag: $IMAGE_TAG"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
log "account $ACCOUNT · $REGION · $NAME-$ENVIRONMENT · tag $IMAGE_TAG"

for repo in reroute-api reroute-web; do
  aws ecr describe-images --repository-name "$NAME/$repo" --image-ids imageTag="$IMAGE_TAG" >/dev/null 2>&1 ||
    fail "$NAME/$repo:$IMAGE_TAG is not in ECR - push it first (make push TAG=$IMAGE_TAG)"
done
log "both images found in ECR"

INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$NAME-$ENVIRONMENT-app" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)
[[ -n "$INSTANCE_ID" && "$INSTANCE_ID" != None ]] || fail "no running instance tagged Name=$NAME-$ENVIRONMENT-app (terraform apply first)"
[[ "$INSTANCE_ID" != *[[:space:]]* ]] || fail "more than one running instance tagged $NAME-$ENVIRONMENT-app: $INSTANCE_ID"
log "instance $INSTANCE_ID"

# Runs on the host as root. The compose file was rendered by Terraform at boot; only the image tags are rewritten,
# so re-running with an older tag is a rollback. The API is healthy only when its container healthcheck passes.
HOST_SCRIPT=$(cat <<EOF
set -euo pipefail
cd /opt/reroute
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REGISTRY
cp docker-compose.yml docker-compose.yml.prev
sed -i -E 's#(/$NAME/reroute-(api|web)):[A-Za-z0-9._-]+#\1:$IMAGE_TAG#g' docker-compose.yml
grep -E 'image: .*/$NAME/reroute-(api|web):' docker-compose.yml
docker compose pull --quiet
docker compose up -d --remove-orphans
for i in \$(seq 1 60); do
  if curl -fs -o /dev/null http://127.0.0.1:8000/api/health && curl -fs -o /dev/null http://127.0.0.1:3000/; then
    echo "healthy after \$((i * 5))s"; docker compose ps; docker image prune -f >/dev/null; exit 0
  fi
  sleep 5
done
docker compose ps; docker compose logs --tail=80 reroute-api web
echo "not healthy after 300s"; exit 1
EOF
)

# base64 + bash: no quoting through SSM JSON, and bash even where the SSM agent's shell is dash
PARAMS=$(python3 -c 'import base64,json,sys; s=base64.b64encode(sys.stdin.buffer.read()).decode(); print(json.dumps({"commands": [f"echo {s} | base64 -d | bash"], "executionTimeout": [sys.argv[1]]}))' \
  "$TIMEOUT_SECONDS" <<<"$HOST_SCRIPT")
COMMAND_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "reroute deploy $IMAGE_TAG" \
  --timeout-seconds 600 \
  --parameters "$PARAMS" \
  --query Command.CommandId --output text)
log "SSM command $COMMAND_ID sent - waiting (up to ${TIMEOUT_SECONDS}s)"

STATUS=Pending
deadline=$((SECONDS + TIMEOUT_SECONDS + 60))
while ((SECONDS < deadline)); do
  sleep 10
  STATUS=$(aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" \
    --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in Pending | InProgress | Delayed) continue ;; *) break ;; esac
done

aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" \
  --query '[StandardOutputContent, StandardErrorContent]' --output text || true
[[ "$STATUS" == Success ]] || fail "deploy on host ended with status $STATUS (previous compose file kept as docker-compose.yml.prev)"

# Through the ALB, as users reach it (-L follows the HTTP->HTTPS redirect when a certificate is configured).
ALB=$(aws elbv2 describe-load-balancers --names "$NAME-$ENVIRONMENT" --query 'LoadBalancers[0].DNSName' --output text)
for _ in $(seq 1 30); do
  if curl -fsSL -o /dev/null "http://$ALB/api/health"; then
    log "deployed $IMAGE_TAG - http://$ALB"
    [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] && printf '### Deployed %s\n\nhttp://%s\n' "$IMAGE_TAG" "$ALB" >>"$GITHUB_STEP_SUMMARY"
    exit 0
  fi
  sleep 5
done
fail "host is healthy but http://$ALB/api/health did not answer within 150s (check ALB target health)"
