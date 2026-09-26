resource "aws_lb" "main" {
  name                       = "${var.name}-${var.environment}"
  load_balancer_type         = "application"
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  idle_timeout               = 300 # Server-Sent Events (agent activity stream)
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "web" {
  name     = "${var.name}-${var.environment}-web"
  port     = 3000
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id
  health_check {
    path    = "/"
    matcher = "200"
  }
}

resource "aws_lb_target_group" "api" {
  name     = "${var.name}-${var.environment}-api"
  port     = 8000
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id
  health_check {
    path    = "/api/health"
    matcher = "200"
  }
}

resource "aws_lb_target_group_attachment" "web" {
  target_group_arn = aws_lb_target_group.web.arn
  target_id        = aws_instance.app.id
  port             = 3000
}

resource "aws_lb_target_group_attachment" "api" {
  target_group_arn = aws_lb_target_group.api.arn
  target_id        = aws_instance.app.id
  port             = 8000
}

locals {
  https = var.certificate_arn != ""
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = local.https ? [1] : []
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
  dynamic "default_action" {
    for_each = !local.https && local.cloudfront ? [1] : []
    content {
      type = "fixed-response"
      fixed_response {
        content_type = "text/plain"
        message_body = "use the CloudFront URL"
        status_code  = "403"
      }
    }
  }
  dynamic "default_action" {
    for_each = !local.https && !local.cloudfront ? [1] : []
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.web.arn
    }
  }
}

resource "aws_lb_listener" "https" {
  count             = local.https ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

locals {
  public_listener_arn = local.https ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
}

# The agent-worker internal API is never exposed publicly.
resource "aws_lb_listener_rule" "block_internal" {
  listener_arn = local.public_listener_arn
  priority     = 5
  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "not found"
      status_code  = "404"
    }
  }
  condition {
    path_pattern {
      values = ["/internal/*"]
    }
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = local.public_listener_arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
  condition {
    path_pattern {
      values = var.enable_mcp ? ["/api/*", "/docs", "/openapi.json", "/mcp"] : ["/api/*", "/docs", "/openapi.json"]
    }
  }
  dynamic "condition" {
    for_each = local.cloudfront ? [1] : []
    content {
      http_header {
        http_header_name = "X-Origin-Verify"
        values           = [random_password.origin_verify[0].result]
      }
    }
  }
}

# With CloudFront the default action is 403, so the dashboard needs its own rule (after /api/*).
resource "aws_lb_listener_rule" "web_via_cloudfront" {
  count        = local.cloudfront ? 1 : 0
  listener_arn = local.public_listener_arn
  priority     = 20
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [random_password.origin_verify[0].result]
    }
  }
}
