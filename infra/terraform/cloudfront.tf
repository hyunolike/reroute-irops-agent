# HTTPS without a domain: CloudFront in front of the ALB (https://<id>.cloudfront.net, default certificate).
# The ALB then accepts only CloudFront: its security group allows only the CloudFront origin-facing prefix list,
# and every listener rule requires a secret header that only this distribution adds - so neither direct hits nor
# someone else's CloudFront distribution can reach it.
locals {
  cloudfront = var.enable_cloudfront
}

resource "random_password" "origin_verify" {
  count   = local.cloudfront ? 1 : 0
  length  = 48
  special = false
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  count = local.cloudfront ? 1 : 0
  name  = "com.amazonaws.global.cloudfront.origin-facing"
}

data "aws_cloudfront_cache_policy" "disabled" {
  count = local.cloudfront ? 1 : 0
  name  = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "optimized" {
  count = local.cloudfront ? 1 : 0
  name  = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  count = local.cloudfront ? 1 : 0
  name  = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "main" {
  count        = local.cloudfront ? 1 : 0
  enabled      = true
  comment      = "${var.name}-${var.environment}" # infra/deploy/deploy.sh finds the distribution by this
  price_class  = "PriceClass_200"                 # includes edge locations in Korea
  http_version = "http2and3"

  origin {
    domain_name = aws_lb.main.dns_name
    origin_id   = "alb"
    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60 # SSE sends a keep-alive every 15s, well inside this
      origin_keepalive_timeout = 60
    }
    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_verify[0].result
    }
  }

  # Everything dynamic (API, approvals, SSE, MCP): no caching, all viewer headers/cookies/query strings forwarded.
  # compress=false so the event stream is never buffered for compression.
  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled[0].id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host[0].id
    compress                 = false
  }

  # Next.js build assets are content-hashed, so they are safe to cache at the edge.
  ordered_cache_behavior {
    path_pattern           = "/_next/static/*"
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized[0].id
    compress               = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  lifecycle {
    precondition {
      condition     = var.certificate_arn == ""
      error_message = "enable_cloudfront and certificate_arn are alternatives: CloudFront terminates HTTPS and talks HTTP to the ALB."
    }
  }
}
