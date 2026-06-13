terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
  # SETUP: add a backend block here for remote state (S3 + DynamoDB locking).
  # Example:
  #   backend "s3" {
  #     bucket         = "your-tf-state-bucket"
  #     key            = "policylens/terraform.tfstate"
  #     region         = "us-east-1"
  #     dynamodb_table = "your-tf-lock-table"
  #     encrypt        = true
  #   }
  # Never commit a local terraform.tfstate to version control.
}

provider "aws" {
  region = var.aws_region
  # Credentials: set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION env vars
  # or configure ~/.aws/credentials with a least-privilege IAM user.
  # SECURITY: the deploying IAM user/role should NOT be AdministratorAccess —
  # see infra/SETUP_NOTES.md §deploy-iam for the minimal policy set.
}

locals {
  name_prefix = var.app_name
  common_tags = {
    Project     = "policylens"
    ManagedBy   = "terraform"
    Environment = "production"
  }
}

# ---------------------------------------------------------------------------
# ECR Repository
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "lambda_repo" {
  name                 = "${local.name_prefix}-lambda"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "lambda_repo_lifecycle" {
  repository = aws_ecr_repository.lambda_repo.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only last 5 tagged images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v", "sha-"]
          countType     = "imageCountMoreThan"
          countNumber   = 5
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# CloudWatch Log Group (created before Lambda so we own retention)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${local.name_prefix}-ask"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

# ---------------------------------------------------------------------------
# Secrets Manager — ANTHROPIC_API_KEY and SUPABASE_DB_URL
#
# Terraform creates the Secret resources as placeholders; the user fills in
# the actual values after apply (see infra/SETUP_NOTES.md §secrets).
# Lambda reads these at cold start via the execution role (see IAM below).
# Plain env vars are NOT used for these secrets in production.
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "${local.name_prefix}/anthropic_api_key"
  description = "Anthropic API key for PolicyLens Lambda"
  # SETUP: after terraform apply, set the secret value:
  #   aws secretsmanager put-secret-value \
  #     --secret-id policylens/anthropic_api_key \
  #     --secret-string "sk-ant-..."
  tags = local.common_tags
}

resource "aws_secretsmanager_secret" "supabase_db_url" {
  name        = "${local.name_prefix}/supabase_db_url"
  description = "Supabase PostgreSQL DSN for PolicyLens Lambda (pgvector backend)"
  # SETUP: after terraform apply, set the secret value:
  #   aws secretsmanager put-secret-value \
  #     --secret-id policylens/supabase_db_url \
  #     --secret-string "postgresql://user:pass@host:5432/db?sslmode=require"
  tags = local.common_tags
}

# The API key the Lambda authorizer checks every caller's x-api-key header
# against. This is the credential that gates all access to POST /ask.
resource "aws_secretsmanager_secret" "api_key" {
  name        = "${local.name_prefix}/api_key"
  description = "Shared API key required on the x-api-key header for POST /ask"
  # SETUP: after terraform apply, generate a strong key and store it, e.g.:
  #   aws secretsmanager put-secret-value \
  #     --secret-id policylens/api_key \
  #     --secret-string "$(openssl rand -hex 32)"
  # Distribute that value to legitimate clients; rotate by re-running put-secret-value.
  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# IAM — Lambda execution role (least privilege)
#
# Permissions:
#   - logs:CreateLogStream + logs:PutLogEvents on its own log group ONLY
#   - secretsmanager:GetSecretValue on its two specific secret ARNs ONLY
# No wildcards. No other permissions.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "lambda_exec" {
  name = "${local.name_prefix}-lambda-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "lambda_exec_policy" {
  name = "${local.name_prefix}-lambda-exec-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # CloudWatch Logs: only this function's log group, only write operations.
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.lambda_logs.arn}:*"
      },
      # Secrets Manager: only the two specific secrets, read-only.
      {
        Effect = "Allow"
        Action = "secretsmanager:GetSecretValue"
        Resource = [
          aws_secretsmanager_secret.anthropic_api_key.arn,
          aws_secretsmanager_secret.supabase_db_url.arn,
        ]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Lambda Function (container image)
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "ask" {
  function_name = "${local.name_prefix}-ask"
  description   = "PolicyLens POST /ask handler — RAG over privacy policies"
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = var.ecr_image_uri
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  # Hard ceiling on simultaneous containers.
  # ALIGNED with PgVectorRetriever pool max_size=5 (src/policylens/pgvector.py).
  # If you raise the pool size, raise this variable to match.
  reserved_concurrent_executions = var.lambda_reserved_concurrency

  environment {
    variables = {
      # Secrets Manager ARNs — the handler reads secrets at cold start via boto3.
      ANTHROPIC_API_KEY_SECRET_ARN = aws_secretsmanager_secret.anthropic_api_key.arn
      SUPABASE_DB_URL_SECRET_ARN   = aws_secretsmanager_secret.supabase_db_url.arn

      # Retrieval backend
      POLICYLENS_RETRIEVAL_BACKEND = var.retrieval_backend

      # Policy allowlist (operator-controlled; see api/handler.py _build_policy_allowlist)
      KNOWN_POLICY_IDS = var.known_policy_ids

      # LangFuse tracing (absent → no-op in observability.py)
      LANGFUSE_HOST       = var.langfuse_host
      LANGFUSE_PUBLIC_KEY = var.langfuse_public_key
      LANGFUSE_SECRET_KEY = var.langfuse_secret_key
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda_logs]

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Lambda authorizer — enforces the x-api-key header in code.
#
# This is the primary anti-abuse control: API Gateway will NOT invoke the main
# handler unless this authorizer returns isAuthorized=true. It is dependency-light
# (boto3 only) so it ships as a zip and stays cheap on the auth hot path.
# Source: api/authorizer.py. Fails closed on any error.
# ---------------------------------------------------------------------------

data "archive_file" "authorizer_zip" {
  type        = "zip"
  source_file = "${path.module}/../api/authorizer.py"
  output_path = "${path.module}/.build/authorizer.zip"
}

resource "aws_iam_role" "authorizer_exec" {
  name = "${local.name_prefix}-authorizer-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "authorizer_logs" {
  name              = "/aws/lambda/${local.name_prefix}-authorizer"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_iam_role_policy" "authorizer_exec_policy" {
  name = "${local.name_prefix}-authorizer-exec-policy"
  role = aws_iam_role.authorizer_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.authorizer_logs.arn}:*"
      },
      # Read ONLY the API-key secret — nothing else.
      {
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_secretsmanager_secret.api_key.arn
      },
    ]
  })
}

resource "aws_lambda_function" "authorizer" {
  function_name    = "${local.name_prefix}-authorizer"
  description      = "x-api-key authorizer for PolicyLens POST /ask"
  role             = aws_iam_role.authorizer_exec.arn
  handler          = "authorizer.handler"
  runtime          = "python3.11"
  filename         = data.archive_file.authorizer_zip.output_path
  source_code_hash = data.archive_file.authorizer_zip.output_base64sha256
  timeout          = 5
  memory_size      = 128

  environment {
    variables = {
      API_KEY_SECRET_ARN = aws_secretsmanager_secret.api_key.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.authorizer_logs]
  tags       = local.common_tags
}

# ---------------------------------------------------------------------------
# API Gateway — HTTP API (payload format 2.0)
# More cost-effective than REST API; supports throttling via usage plans
# configured below.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "api" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
  description   = "PolicyLens HTTP API"

  # Restrictive CORS — no wildcards.
  # Set allowed_cors_origin to "" to disable entirely.
  dynamic "cors_configuration" {
    for_each = var.allowed_cors_origin != "" ? [1] : []
    content {
      allow_origins = [var.allowed_cors_origin]
      allow_methods = ["POST", "OPTIONS"]
      allow_headers = ["content-type", "x-api-key"]
      max_age       = 86400
    }
  }

  tags = local.common_tags
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ask.invoke_arn
  payload_format_version = "2.0"
}

# Lambda (REQUEST) authorizer on the HTTP API — enforces x-api-key in code.
resource "aws_apigatewayv2_authorizer" "api_key" {
  api_id                            = aws_apigatewayv2_api.api.id
  authorizer_type                   = "REQUEST"
  authorizer_uri                    = aws_lambda_function.authorizer.invoke_arn
  identity_sources                  = ["$request.header.x-api-key"]
  name                              = "${local.name_prefix}-apikey-authorizer"
  authorizer_payload_format_version = "2.0"
  enable_simple_responses           = true
  authorizer_result_ttl_in_seconds  = var.authorizer_result_ttl_seconds
}

# Allow API Gateway to invoke the authorizer Lambda.
resource "aws_lambda_permission" "apigw_invoke_authorizer" {
  statement_id  = "AllowAPIGatewayInvokeAuthorizer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.authorizer.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/authorizers/*"
}

resource "aws_apigatewayv2_route" "post_ask" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "POST /ask"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"

  # Auth is enforced HERE, in Terraform — no manual post-deploy step. Every
  # request must pass the x-api-key authorizer before the handler is invoked.
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.api_key.id
}

resource "aws_apigatewayv2_stage" "prod" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "prod"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.api_throttle_rate
    throttling_burst_limit = var.api_throttle_burst
    logging_level          = "ERROR"
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access_logs.arn
    format = jsonencode({
      requestId        = "$context.requestId"
      ip               = "$context.identity.sourceIp"
      requestTime      = "$context.requestTime"
      httpMethod       = "$context.httpMethod"
      routeKey         = "$context.routeKey"
      status           = "$context.status"
      protocol         = "$context.protocol"
      responseLength   = "$context.responseLength"
      integrationError = "$context.integrationErrorMessage"
      authorizerError  = "$context.authorizer.error"
    })
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "api_access_logs" {
  name              = "/aws/apigateway/${local.name_prefix}-api/prod"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

# Lambda invoke permission for API Gateway
resource "aws_lambda_permission" "apigw_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ask.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

# ---------------------------------------------------------------------------
# Abuse controls — what is enforced in Terraform vs. what remains a SETUP step
#
# Enforced HERE, automatically, on every deploy:
#   - API-KEY AUTH via the Lambda authorizer above (authorization_type=CUSTOM on
#     POST /ask). No anonymous access; nothing to remember post-deploy.
#   - Stage-level throttling: rate (var.api_throttle_rate) + burst
#     (var.api_throttle_burst) on the HTTP API stage.
#   - Lambda reserved concurrency: hard ceiling on simultaneous containers.
#
# NOT natively supported by HTTP API v2, so flagged in SETUP_NOTES.md:
#   - Per-key DAILY QUOTA (var.api_quota_per_day). Rate+burst+concurrency already
#     bound spend; a hard daily cap needs WAF rate-based rules or a REST API.
#     The AWS Budget hard-stop (SETUP_NOTES §financial-backstops) is the
#     authoritative dollar ceiling regardless.
# ---------------------------------------------------------------------------
