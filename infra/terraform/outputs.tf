output "url" {
  description = "ReRoute dashboard"
  value       = "${local.https ? "https" : "http"}://${aws_lb.main.dns_name}"
}

output "ecr_api_repository" {
  value = aws_ecr_repository.repo["reroute-api"].repository_url
}

output "ecr_web_repository" {
  value = aws_ecr_repository.repo["reroute-web"].repository_url
}

output "instance_id" {
  description = "Use with: aws ssm start-session --target <id>"
  value       = aws_instance.app.id
}

output "nvidia_api_key_secret_arn" {
  description = "Set the NVIDIA key: aws secretsmanager put-secret-value --secret-id <arn> --secret-string nvapi-..."
  value       = aws_secretsmanager_secret.nvidia_api_key.arn
}

output "mcp_url" {
  description = "Register with NemoClaw: nemoclaw <sandbox> mcp add reroute --url <this> --env REROUTE_MCP_TOKEN (HTTPS required)"
  value       = var.enable_mcp ? (var.public_hostname != "" && local.https ? "https://${var.public_hostname}/mcp" : "(needs public_hostname + certificate_arn for HTTPS) http://${aws_lb.main.dns_name}/mcp") : "disabled"
}

output "mcp_token_command" {
  description = "Prints the MCP bearer token from Secrets Manager (never stored in state outputs)"
  value       = "aws secretsmanager get-secret-value --secret-id ${aws_secretsmanager_secret.app.arn} --query SecretString --output text | python3 -c \"import sys,json;print(json.load(sys.stdin)['REROUTE_MCP_TOKEN'])\""
}

output "optimizer" {
  value = var.enable_gpu ? "NVIDIA cuOpt on ${var.gpu_instance_type}" : "CPU fallback (enable_gpu=false)"
}
