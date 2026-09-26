variable "region" {
  description = "AWS region (Seoul by default)."
  type        = string
  default     = "ap-northeast-2"
}

variable "environment" {
  type    = string
  default = "demo"
}

variable "name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "reroute"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "enable_gpu" {
  description = "Run NVIDIA cuOpt on a GPU instance (OPTIMIZATION_PROVIDER=cuopt). false = CPU host + HiGHS fallback."
  type        = bool
  default     = true
}

variable "gpu_instance_type" {
  description = "GPU instance for the app host + cuOpt server (NVIDIA L4 on g6)."
  type        = string
  default     = "g6.xlarge"
}

variable "cpu_instance_type" {
  description = "CPU app host (enable_gpu=false). AWS Free-plan accounts can only launch free-tier types, e.g. m7i-flex.large."
  type        = string
  default     = "t3.large"
}

variable "root_volume_gb" {
  type    = number
  default = 100
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_backup_retention_days" {
  description = "RDS automated backup retention. AWS Free-plan accounts reject values above their limit (use 1)."
  type        = number
  default     = 3
}

variable "image_tag" {
  description = "Tag of the reroute-api / reroute-web images pushed to ECR."
  type        = string
  default     = "latest"
}

variable "cuopt_image" {
  type    = string
  default = "nvidia/cuopt:latest-cu12"
}

variable "llm_provider" {
  description = "auto/nvidia = Nemotron via NIM (needs the NVIDIA API key secret; without it the labelled scripted planner is used), mock = scripted planner."
  type        = string
  default     = "auto"
  validation {
    condition     = contains(["auto", "nvidia", "mock"], var.llm_provider)
    error_message = "llm_provider must be auto, nvidia or mock."
  }
}

variable "retriever_provider" {
  type    = string
  default = "nvidia"
  validation {
    condition     = contains(["nvidia", "lexical"], var.retriever_provider)
    error_message = "retriever_provider must be nvidia or lexical."
  }
}

variable "nim_model" {
  type    = string
  default = "nvidia/nemotron-3-super-120b-a12b"
}

variable "demo_mode" {
  type    = bool
  default = true
}

variable "certificate_arn" {
  description = "ACM certificate for HTTPS on the ALB. Empty = HTTP only (demo)."
  type        = string
  default     = ""
}

variable "public_hostname" {
  description = "DNS name that points at the ALB (e.g. reroute.example.com). Needed with certificate_arn for the HTTPS MCP endpoint NemoClaw requires."
  type        = string
  default     = ""
}

variable "enable_mcp" {
  description = "Expose ReRoute tools as an MCP server at /mcp for external agents (OpenClaw in NemoClaw)."
  type        = bool
  default     = true
}

variable "allowed_ingress_cidrs" {
  description = "Who may reach the ALB. Restrict to your judges/office IPs for a private demo."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "github_repository" {
  description = "owner/repo allowed to deploy through GitHub Actions OIDC (e.g. hyunolike/reroute-irops-agent). Empty = no deploy role."
  type        = string
  default     = ""
}

variable "github_environment" {
  description = "GitHub environment the deploy job runs in; only jobs in this environment can assume the deploy role."
  type        = string
  default     = "demo"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set false if the AWS account already has token.actions.githubusercontent.com."
  type        = bool
  default     = true
}
