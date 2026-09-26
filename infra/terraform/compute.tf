# GPU: AWS Deep Learning Base AMI (NVIDIA driver + Docker + NVIDIA Container Toolkit) for the cuOpt server.
data "aws_ssm_parameter" "gpu_ami" {
  name = "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
}

data "aws_ssm_parameter" "cpu_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  public_url = (var.public_hostname != "" ? "https://${var.public_hostname}"
    : local.cloudfront ? "https://${aws_cloudfront_distribution.main[0].domain_name}"
  : "http://${aws_lb.main.dns_name}")
  registry = split("/", aws_ecr_repository.repo["reroute-api"].repository_url)[0]
  compose = templatefile("${path.module}/templates/docker-compose.prod.yml.tftpl", {
    api_image   = "${aws_ecr_repository.repo["reroute-api"].repository_url}:${var.image_tag}"
    web_image   = "${aws_ecr_repository.repo["reroute-web"].repository_url}:${var.image_tag}"
    cuopt_image = var.cuopt_image
    enable_gpu  = var.enable_gpu
    log_group   = aws_cloudwatch_log_group.app.name
    region      = var.region
  })
  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    region             = var.region
    registry           = local.registry
    compose_b64        = base64encode(local.compose)
    app_secret_arn     = aws_secretsmanager_secret.app.arn
    nvidia_secret_arn  = aws_secretsmanager_secret.nvidia_api_key.arn
    db_host            = aws_db_instance.main.address
    demo_mode          = var.demo_mode
    llm_provider       = var.llm_provider
    retriever_provider = var.retriever_provider
    nim_model          = var.nim_model
    optimization       = var.enable_gpu ? "cuopt" : "fallback"
    enable_mcp         = var.enable_mcp
    mcp_allowed_hosts  = join(",", compact([var.public_hostname, local.cloudfront ? aws_cloudfront_distribution.main[0].domain_name : "", aws_lb.main.dns_name, "reroute-api:*", "localhost:*"]))
    public_url         = local.public_url
  })
}

resource "aws_instance" "app" {
  ami                    = var.enable_gpu ? data.aws_ssm_parameter.gpu_ami.value : data.aws_ssm_parameter.cpu_ami.value
  instance_type          = var.enable_gpu ? var.gpu_instance_type : var.cpu_instance_type
  subnet_id              = aws_subnet.private[0].id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.app.name
  user_data_base64       = base64gzip(local.user_data)
  # Re-create the host when the rendered bootstrap changes (images are re-pulled by `make redeploy`).
  user_data_replace_on_change = true

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  tags = { Name = "${var.name}-${var.environment}-app" }

  lifecycle {
    ignore_changes = [ami]
  }
}
