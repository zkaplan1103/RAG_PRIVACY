# Infra notes — tag: `infra` (infra-engineer)

Append-only, dated. Lambda handler, container image, Terraform, API Gateway, GitHub Actions CI + regression gate.

---

2026-06-11 | orchestrator | File created in Phase 0 of the production upgrade. Contracts: docs/CONTRACTS.md §9 (gate), §10 (API), §11 (env vars). No work yet.

2026-06-13 | infra-engineer | P5 + P6 complete (Lambda handler, Terraform, CI, gate). Files: api/handler.py, api/Dockerfile, infra/{main,variables,outputs}.tf, infra/terraform.tfvars.example, infra/SETUP_NOTES.md, eval/gate.py, .github/workflows/ci.yml, tests/test_handler.py. Key decisions: policy_id allowlist via KNOWN_POLICY_IDS env var (falls back to built-in OPP-115 115-policy set); secrets via Secrets Manager (boto3, cold-start); ANTHROPIC_API_KEY_SECRET_ARN + SUPABASE_DB_URL_SECRET_ARN env vars in Lambda. Lambda reserved_concurrent_executions=5 aligned with PgVectorRetriever pool max_size=5. HTTP API v2 used (cheaper than REST); API key auth flagged as SETUP step (WAF or Lambda authorizer). `terraform validate` NOT run locally (terraform not installed); YAML validates in CI. 34 handler+gate unit tests pass; full fast suite (149 tests) green. Docker available locally but build not run (bakes ~710 MB HF models — flagged in SETUP_NOTES). `uv run ruff check api/ && uv run pyright api/` clean.
