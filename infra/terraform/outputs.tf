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

output "optimizer" {
  value = var.enable_gpu ? "NVIDIA cuOpt on ${var.gpu_instance_type}" : "CPU fallback (enable_gpu=false)"
}
