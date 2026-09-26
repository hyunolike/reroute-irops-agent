# GitHub Actions deploy role (.github/workflows/deploy.yml). OIDC: no long-lived AWS keys in GitHub.
# Only jobs of `github_repository` that run in the GitHub environment `github_environment` can assume it,
# and it can only push the two ECR repositories and run a shell command on the app host via SSM.
locals {
  github_deploy = var.github_repository != ""
  # GitHub issues either the name-based subject (repo:owner/repo) or, for repos on immutable subjects,
  # repo:owner@<id>/repo@<id>. Both are pinned to the one environment.
  github_subjects = compact([
    "repo:${var.github_repository}:environment:${var.github_environment}",
    var.github_sub_claim_prefix != "" ? "${var.github_sub_claim_prefix}:environment:${var.github_environment}" : "",
  ])
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = local.github_deploy && var.create_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS verifies GitHub's OIDC certificate itself; the thumbprints are kept for older provider versions.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = local.github_deploy && !var.create_github_oidc_provider ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "github_assume" {
  count = local.github_deploy ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.github_subjects
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count                = local.github_deploy ? 1 : 0
  name                 = "${var.name}-${var.environment}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_assume[0].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_deploy" {
  count = local.github_deploy ? 1 : 0
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPushOwnRepos"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages",
      "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
    ]
    resources = [for r in aws_ecr_repository.repo : r.arn]
  }
  statement {
    sid       = "FindHostAndAlb"
    actions   = ["ec2:DescribeInstances", "elasticloadbalancing:DescribeLoadBalancers"]
    resources = ["*"] # Describe* calls do not support resource-level permissions
  }
  statement {
    # Tag-scoped rather than instance-scoped: the host is replaced when its bootstrap changes.
    sid       = "RunCommandOnAppHost"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Name"
      values   = ["${var.name}-${var.environment}-app"]
    }
  }
  statement {
    sid       = "RunShellScriptDocumentOnly"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ssm:${var.region}::document/AWS-RunShellScript"]
  }
  statement {
    sid       = "ReadCommandResult"
    actions   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count  = local.github_deploy ? 1 : 0
  name   = "reroute-github-deploy"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy[0].json
}

data "aws_caller_identity" "current" {}
