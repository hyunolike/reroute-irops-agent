# Credentials never live in code, tfvars or state-visible outputs:
#  - generated secrets are random and stored in Secrets Manager
#  - the NVIDIA API key secret is created EMPTY; set it out-of-band:
#      aws secretsmanager put-secret-value --secret-id <nvidia_api_key_secret_arn> --secret-string 'nvapi-...'
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "approval_signing" {
  length  = 48
  special = false
}

resource "random_password" "worker_token" {
  length  = 40
  special = false
}

resource "random_password" "mcp_token" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/${var.environment}/app"
  description             = "ReRoute generated secrets (DB password, approval signing key, worker token)"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    POSTGRES_PASSWORD       = random_password.db.result
    APPROVAL_SIGNING_SECRET = random_password.approval_signing.result
    AGENT_WORKER_TOKEN      = random_password.worker_token.result
    REROUTE_MCP_TOKEN       = random_password.mcp_token.result
  })
}

resource "aws_secretsmanager_secret" "nvidia_api_key" {
  name                    = "${var.name}/${var.environment}/nvidia-api-key"
  description             = "build.nvidia.com API key (nvapi-...). Set the value manually."
  recovery_window_in_days = 0
}
