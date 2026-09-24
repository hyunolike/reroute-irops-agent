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
  type    = string
  default = "t3.large"
}

variable "root_volume_gb" {
  type    = number
  default = 100
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
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

variable "allowed_ingress_cidrs" {
  description = "Who may reach the ALB. Restrict to your judges/office IPs for a private demo."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
