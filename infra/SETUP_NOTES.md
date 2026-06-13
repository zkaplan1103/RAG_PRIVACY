# infra/SETUP_NOTES.md — Flagged steps requiring live credentials

These are the manual steps the user must take that cannot be automated without
live AWS credentials. Grouped roughly in execution order.

---

## Financial backstops — DO THESE FIRST before any deploy

These are CONSOLE-ONLY steps (AWS doesn't support them via Terraform/CLI easily).
Set them up BEFORE you run `terraform apply` or push any image.

1. **AWS Budget hard-stop**: Create a Zero-Spend Budget or a monthly budget
   alert in the AWS Billing console → Budgets. Set an action to notify (or
   even block IAM) if spend exceeds your ceiling.

2. **Billing alarms**: In CloudWatch → Alarms, create a billing alarm for
   e.g. $10/month to get an SNS email before costs spike.

3. **Anthropic credit ceiling**: In the Anthropic console → Billing, set a
   hard spend limit on your API key. This is independent of AWS spend and
   limits LLM costs directly.

Set all three BEFORE routing real traffic. These are the primary financial
backstops against a scammer running up bills.

---

## Deploy IAM user (least-privilege)

SECURITY: do NOT deploy with an AdministratorAccess credential.

Create a dedicated IAM user (or role) for Terraform deployment with only the
permissions it needs. Minimal policy set:

- `ecr:*` on the ECR repo
- `lambda:*` on the Lambda function
- `apigateway:*` on the API
- `iam:CreateRole`, `iam:AttachRolePolicy`, `iam:PassRole` (scoped to the role)
- `secretsmanager:CreateSecret`, `secretsmanager:PutSecretValue`, `secretsmanager:DescribeSecret`
- `logs:CreateLogGroup`, `logs:PutRetentionPolicy`

Provide credentials via:
  export AWS_ACCESS_KEY_ID=...
  export AWS_SECRET_ACCESS_KEY=...
  export AWS_REGION=us-east-1

---

## Terraform setup

```bash
# 1. Install Terraform >= 1.6 (https://developer.hashicorp.com/terraform/install)
# 2. Copy the example tfvars
cp infra/terraform.tfvars.example infra/terraform.tfvars
# Edit infra/terraform.tfvars with your values

# 3. Initialize (downloads provider; no credentials needed for this step)
terraform -chdir=infra init -backend=false  # local validate only
# For real deploy:
terraform -chdir=infra init                  # with backend config set

# 4. Validate (no credentials needed)
terraform -chdir=infra validate

# 5. Plan (needs AWS credentials)
terraform -chdir=infra plan

# 6. Apply (needs AWS credentials)
terraform -chdir=infra apply
```

Verification: `terraform output api_endpoint` should print the /ask URL.

---

## Building and pushing the container image

```bash
# Prerequisites: Docker, AWS CLI, ECR repo created by terraform apply
ECR_REGISTRY=$(terraform -chdir=infra output -raw ecr_repository_url | cut -d/ -f1)
ECR_REPO=$(terraform -chdir=infra output -raw ecr_repository_url)
IMAGE_VERSION=$(git rev-parse --short HEAD)

# Build (CPU-only torch; bakes in HF models — takes ~10 min on first build)
docker build \
  --build-arg IMAGE_VERSION=$IMAGE_VERSION \
  -t policylens-lambda:$IMAGE_VERSION \
  api/

# Login and push
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY

docker tag policylens-lambda:$IMAGE_VERSION $ECR_REPO:$IMAGE_VERSION
docker push $ECR_REPO:$IMAGE_VERSION

# Update terraform.tfvars:
#   ecr_image_uri = "<full ECR repo URL>:<image_version>"
# Then: terraform -chdir=infra apply
```

Verification: `docker run --rm -e ANTHROPIC_API_KEY=... policylens-lambda:$IMAGE_VERSION` should start (will error on missing event, which is expected).

---

## Filling in Secrets Manager values

After `terraform apply`, two empty secrets exist. Fill them in:

```bash
# Anthropic API key
aws secretsmanager put-secret-value \
  --secret-id policylens/anthropic_api_key \
  --secret-string "sk-ant-..."

# Supabase PostgreSQL DSN
aws secretsmanager put-secret-value \
  --secret-id policylens/supabase_db_url \
  --secret-string "postgresql://user:pass@host:5432/db?sslmode=require"
```

Verification:
```bash
aws secretsmanager get-secret-value --secret-id policylens/anthropic_api_key \
  | jq -r .SecretString | cut -c1-6  # should print "sk-ant"
```

---

## API key authentication (§api-key-auth)

API Gateway HTTP API (v2) does not support native API key + usage plans.
Options:

### Option A: AWS WAF (recommended for production)
1. In the console: WAF → Web ACLs → Create web ACL, associate with the API.
2. Add a rule: block requests where header `x-api-key` != your secret key.
3. This enforces key auth before Lambda is ever invoked.

### Option B: Lambda authorizer
Deploy a Lambda authorizer that checks the `x-api-key` header. Wire it to
the API route. The authorizer runs before the main handler.

### Option C: Migrate to REST API
If you need native API key + usage plan support (per-key quotas, tracking),
replace `aws_apigatewayv2_api` with `aws_api_gateway_rest_api`. More setup;
see Terraform AWS provider docs.

Current enforcement regardless of choice:
- Stage-level throttle: 5 req/s sustained, burst 10 (main.tf)
- Lambda reserved concurrency: 5 (hard ceiling)

Verification (once API key is configured):
```bash
# Should return 403
curl -s https://<api_endpoint>/ask -d '{"query":"test","policy_id":"105_amazon_com"}'

# Should proceed to validation
curl -s https://<api_endpoint>/ask \
  -H "x-api-key: your-key" \
  -d '{"query":"test","policy_id":"105_amazon_com"}'
```

---

## Known policy IDs allowlist (§allowlist)

The handler builds its allowlist from the `KNOWN_POLICY_IDS` environment
variable (comma-separated policy IDs). This is set in Lambda → Configuration
→ Environment variables (or via Terraform's `known_policy_ids` variable).

To populate it with all IDs currently in your Chroma index:
```bash
# After building the Chroma index:
uv run python -c "
import chromadb, json
client = chromadb.PersistentClient(path='data/index')
col = client.get_collection('policylens')
results = col.get(include=['metadatas'])
ids = sorted(set(m['policy_id'] for m in results['metadatas']))
print(','.join(ids))
"
```

Set the output as `KNOWN_POLICY_IDS` in your Lambda env vars and in
`terraform.tfvars` under `known_policy_ids`.

---

## GitHub Actions secrets

For CI to run the eval job (faithfulness gate), add these repo secrets:
  Settings → Secrets and variables → Actions → New repository secret

Required for eval job:
  ANTHROPIC_API_KEY     — generation + Ragas judge

Optional (activate pgvector CI path):
  SUPABASE_DB_URL       — pgvector retrieval

Optional (activate tracing in CI):
  LANGFUSE_PUBLIC_KEY
  LANGFUSE_SECRET_KEY
  LANGFUSE_HOST

The `checks` job (lint/type/test) runs WITHOUT any secrets.

---

## Supabase / pgvector setup

1. Create a Supabase project at https://supabase.com
2. In the SQL editor, run: `CREATE EXTENSION IF NOT EXISTS vector;`
3. Run the migration: `uv run python src/policylens/migrate_pgvector.py`
   (requires SUPABASE_DB_URL set in env)
4. Set SUPABASE_DB_URL in Secrets Manager (see above)

Verification:
```bash
SUPABASE_DB_URL="..." uv run python -c "
from src.policylens.config import Config
from src.policylens.pgvector import PgVectorRetriever
cfg = Config(retrieval_backend='pgvector')
r = PgVectorRetriever(cfg)
hits = r.retrieve('data collection', '105_amazon_com', k=3)
print([h['chunk']['chunk_id'] for h in hits])
r.close()
"
```

---

## End-to-end smoke test (after all setup is complete)

```bash
curl -s -X POST https://<api_endpoint>/ask \
  -H "Content-Type: application/json" \
  -H "x-api-key: <your-api-key>" \
  -d '{"query": "What data does this policy collect?", "policy_id": "105_amazon_com"}' \
  | python3 -m json.tool
```

Expected: 200 with `answer.answerable=true`, `answer.citations` non-empty,
`request_id` and `latency_ms` in the envelope.
