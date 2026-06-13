# PolicyLens — Terraform variable definitions
# All credentials arrive via env vars or tfvars; nothing is hardcoded.
# See docs/CONTRACTS.md §11 for the env var registry.
# See infra/terraform.tfvars.example for placeholder values.
#
# SETUP: copy terraform.tfvars.example → terraform.tfvars, fill in values,
# then run: terraform -chdir=infra init && terraform -chdir=infra plan
#
# SECURITY: terraform.tfvars is git-ignored (see .gitignore).
# Never commit real values.

variable "aws_region" {
  description = "AWS region to deploy into (e.g. us-east-1)."
  type        = string
  default     = "us-east-1"
}

variable "app_name" {
  description = "Short name used as a prefix for all resource names."
  type        = string
  default     = "policylens"
}

variable "ecr_image_uri" {
  description = <<-EOT
    Full ECR image URI including tag, e.g.:
      123456789012.dkr.ecr.us-east-1.amazonaws.com/policylens-lambda:abc1234
    SETUP: build and push the image per api/Dockerfile comments, then set this.
  EOT
  type        = string
}

variable "lambda_memory_mb" {
  description = "Lambda function memory in MB. bge-reranker needs ≥2048 MB."
  type        = number
  default     = 3008
}

variable "lambda_timeout_seconds" {
  description = "Lambda function timeout in seconds. Must exceed worst-case RAG latency."
  type        = number
  default     = 30
}

# Lambda reserved concurrency is aligned with PgVectorRetriever pool max_size=5
# (src/policylens/pgvector.py ConnectionPool max_size). If you raise the pool
# size, raise this to match so you don't queue more requests than the pool allows.
variable "lambda_reserved_concurrency" {
  description = <<-EOT
    Hard cap on simultaneous Lambda containers (reserved concurrency).
    Aligned with PgVectorRetriever pool max_size=5. Raise both together.
    Set to -1 to use unreserved concurrency (not recommended for cost control).
  EOT
  type        = number
  default     = 5
}

variable "api_throttle_rate" {
  description = "API Gateway usage plan: sustained requests per second."
  type        = number
  default     = 5
}

variable "api_throttle_burst" {
  description = "API Gateway usage plan: burst limit (token bucket)."
  type        = number
  default     = 10
}

variable "api_quota_per_day" {
  description = "API Gateway usage plan: max requests per day."
  type        = number
  default     = 1000
}

variable "allowed_cors_origin" {
  description = <<-EOT
    Allowed CORS origin for the API. Use the actual frontend origin (no wildcards).
    Set to "" to disable CORS headers entirely (recommended if no browser cross-origin caller).
    Default is a placeholder; change to your real domain before production use.
  EOT
  type        = string
  default     = "https://your-demo-origin.example.com"
}

variable "known_policy_ids" {
  description = <<-EOT
    Comma-separated list of valid policy_id values passed to the Lambda as the
    KNOWN_POLICY_IDS env var. Controls the handler allowlist.
    SETUP: populate with the actual IDs loaded in your index (all 115 OPP-115 IDs,
    or a subset). If empty, the Lambda falls back to the built-in OPP-115 list.
  EOT
  type        = string
  default     = ""
}

variable "retrieval_backend" {
  description = "Retrieval backend: 'chroma' (local dev) or 'pgvector' (production)."
  type        = string
  default     = "pgvector"
  validation {
    condition     = contains(["chroma", "pgvector"], var.retrieval_backend)
    error_message = "retrieval_backend must be 'chroma' or 'pgvector'."
  }
}

variable "langfuse_host" {
  description = "LangFuse host URL. Leave empty to disable tracing."
  type        = string
  default     = ""
  sensitive   = true
}

variable "langfuse_public_key" {
  description = "LangFuse public key. Leave empty to disable tracing."
  type        = string
  default     = ""
  sensitive   = true
}

variable "langfuse_secret_key" {
  description = "LangFuse secret key. Leave empty to disable tracing."
  type        = string
  default     = ""
  sensitive   = true
}

variable "log_retention_days" {
  description = "CloudWatch log group retention in days."
  type        = number
  default     = 30
}
