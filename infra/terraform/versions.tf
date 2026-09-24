terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.70" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Recommended: remote state. Uncomment and fill in (bucket must exist).
  # backend "s3" {
  #   bucket         = "my-tf-state"
  #   key            = "reroute/terraform.tfstate"
  #   region         = "ap-northeast-2"
  #   dynamodb_table = "tf-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "reroute"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
