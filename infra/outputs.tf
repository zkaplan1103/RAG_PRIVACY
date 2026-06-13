# PolicyLens — Terraform outputs

output "api_endpoint" {
  description = "Base URL for the deployed API (POST /ask at <api_endpoint>/ask)."
  value       = "${aws_apigatewayv2_stage.prod.invoke_url}/ask"
}

output "lambda_function_name" {
  description = "Lambda function name (for logs, invoke, and CLI updates)."
  value       = aws_lambda_function.ask.function_name
}

output "ecr_repository_url" {
  description = "ECR repository URL. Push images here; tag format: <sha>."
  value       = aws_ecr_repository.lambda_repo.repository_url
}

output "anthropic_api_key_secret_arn" {
  description = "ARN of the Secrets Manager secret for ANTHROPIC_API_KEY. Fill value after apply."
  value       = aws_secretsmanager_secret.anthropic_api_key.arn
}

output "supabase_db_url_secret_arn" {
  description = "ARN of the Secrets Manager secret for SUPABASE_DB_URL. Fill value after apply."
  value       = aws_secretsmanager_secret.supabase_db_url.arn
}

output "lambda_exec_role_arn" {
  description = "ARN of the least-privilege Lambda execution role."
  value       = aws_iam_role.lambda_exec.arn
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group for Lambda output."
  value       = aws_cloudwatch_log_group.lambda_logs.name
}
