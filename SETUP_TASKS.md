# SETUP_TASKS.md — PolicyLens production wiring guide

Every step you must take, strictly ordered. Each step includes exact commands
or console clicks, the credential or env var it produces, and a verification
command so you know it worked.

**Who this is for:** the person operating the deployment — not a developer
reading code. You run these steps once; after that, CI and Terraform maintain
the system.

**Ordering is contractual (UPGRADE_PLAN decision 8).** Do not jump ahead.
The baseline eval (Group 1) runs on Chroma before any pgvector work; the
financial backstops (Group 0) run before any cloud provisioning.

---

## Group 0 — Financial backstops (DO THESE FIRST, before any `terraform apply`)

**Why this group is first:** once the public API endpoint is live, a malicious
caller can spam it and run up unbounded Anthropic API costs plus Lambda
invocation costs. These three backstops are your only dollar ceiling. Set them
before you route any real traffic. They are console-only; Terraform cannot
provision them.

---

### Step 0.1 — AWS Budget hard-stop

**Console:** AWS Billing and Cost Management → Budgets → Create budget

1. Choose "Cost budget" and set the period to Monthly.
2. Set the budgeted amount to your ceiling (e.g. $20/month).
3. Under "Alert thresholds", create an alert at 80% actual and 100% actual
   (SNS email to yourself).
4. Optional: under "Actions", attach an IAM action to deny `lambda:InvokeFunction`
   if the 100% threshold is breached — this is a hard block.

**Why:** Lambda and API Gateway charges are per-invocation. A flood of spam
requests can accumulate hundreds of dollars before you notice without this.

**How to verify this worked:**
```
AWS console → Budgets → confirm your budget appears with the correct monthly amount
```

---

### Step 0.2 — CloudWatch billing alarm

**Console:** CloudWatch → Alarms → Create alarm → Select metric

1. Under "Billing" → "Total Estimated Charge" → select USD metric.
2. Set threshold: alarm when estimated charge exceeds $10 (or your chosen
   early-warning amount, below your Step 0.1 ceiling).
3. Set notification: create or select an SNS topic, enter your email.
4. Confirm the SNS subscription email that arrives.

**Why:** The budget alert in Step 0.1 fires at end-of-month aggregates; the
CloudWatch alarm fires in near-real-time on estimated charges. Together they
give you early warning and a hard stop.

**How to verify this worked:**
```
CloudWatch → Alarms → confirm the billing alarm appears in OK state
```

---

### Step 0.3 — Anthropic credit ceiling

**Console:** https://console.anthropic.com → Settings → Billing → Usage limits

1. Set a hard monthly spend limit on your API key to your ceiling (e.g. $20).
   Once reached, the API returns 429 until the next billing cycle — your
   Lambda will 500 gracefully rather than continuing to spend.

**Why:** Anthropic charges per token. If the Lambda authorizer fails open for
any reason, or a caller obtains your API key, this limit is the backstop for
LLM costs independent of AWS.

**How to verify this worked:**
```
Anthropic console → Usage limits → confirm the hard limit is set
```

---

## Group 1 — Baseline eval on Chroma (before any pgvector cutover)

**Why this group is second:** UPGRADE_PLAN decision 8 requires that you capture
baseline eval scores on the current Chroma path (zero-cloud, only your Anthropic
key needed) before switching the retrieval backend to pgvector. This baseline
anchors the CI regression gate — all future runs are compared against it.
`Config.retrieval_backend` stays `"chroma"` until `baseline_v1.json` exists.

You need your Anthropic API key for this group (Step 3.1), but no cloud
accounts. You can complete this group right now, before any AWS or Supabase
setup.

---

### Step 1.1 — Install Python dependencies

```bash
# Requires Python 3.11+ and uv (https://github.com/astral-sh/uv)
uv sync --group eval
```

**How to verify this worked:**
```bash
uv run python -c "import ragas; print('ragas ok')"
# Expected: ragas ok
```

---

### Step 1.2 — Build the Chroma vector index

This embeds all 2,393 policy chunks locally using `bge-small-en-v1.5` (no API
key needed). Takes ~5 minutes on CPU; cached to `data/index/` afterward.

```bash
uv run python -m policylens.index build
```

**How to verify this worked:**
```bash
uv run python -c "
import chromadb
client = chromadb.PersistentClient(path='data/index')
col = client.get_collection('policylens')
print(f'chunks in index: {col.count()}')
"
# Expected: chunks in index: 2393
```

---

### Step 1.3 — Run the baseline eval on Chroma

**Produces:** `eval/baselines/baseline_v1.json`

**Cost estimate:** the Ragas judge (`claude-opus-4-8`) evaluates 167 golden
items. Approximate cost: $2–5 at current Opus pricing. Run this once; do not
re-run casually (it will overwrite the baseline).

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # your Anthropic key

# Optional: smoke-test the harness first (2 items, ~$0.05)
uv run python eval/ragas/run_ragas.py \
    --backend chroma \
    --out /tmp/ragas_smoke.json \
    --golden eval/golden/golden_v1.jsonl \
    --max-items 2

# Full baseline run (167 items)
uv run python eval/ragas/run_ragas.py \
    --backend chroma \
    --out eval/baselines/baseline_v1.json \
    --golden eval/golden/golden_v1.jsonl
```

**How to verify this worked:**
```bash
python3 -c "
import json
with open('eval/baselines/baseline_v1.json') as f:
    r = json.load(f)
print('n_items:', r.get('n_items'))
print('backend:', r.get('backend'))
print('faithfulness:', r.get('ragas', {}).get('faithfulness'))
print('abstention_accuracy:', r.get('house_metrics', {}).get('abstention_accuracy'))
"
# Expected: n_items: 167, backend: chroma, faithfulness: <a float>, abstention_accuracy: <a float>
```

After the run, commit the baseline and update the gate threshold:

```bash
# Record the actual faithfulness score from the output above,
# then edit eval/thresholds.yaml:
#   faithfulness: <observed value>       # recalibrated from baseline
#   abstention_accuracy: 0.90

git add eval/baselines/baseline_v1.json eval/thresholds.yaml
git commit -m "eval: add Chroma baseline_v1 (UPGRADE_PLAN decision 8)"
```

**STOP HERE until baseline_v1.json is committed.** Do not change
`Config.retrieval_backend` to `"pgvector"` until the baseline exists.

---

## Group 2 — Account setup

Create the four accounts/services. Each produces credentials you will enter in
Groups 3–5. You do not need to provision anything yet.

---

### Step 2.1 — AWS account and IAM deploy user

**Why a dedicated IAM user, not AdministratorAccess:** the deploy credentials
are stored in GitHub secrets (Step 6.2). Least-privilege limits blast radius if
a secret leaks.

1. Create (or reuse) an AWS account at https://aws.amazon.com
2. In IAM → Users → Create user, create a user named `policylens-deploy`
3. Attach a policy with exactly these permissions (do not use AdministratorAccess):
   - `ecr:*` on the ECR repo
   - `lambda:*` on the Lambda function
   - `apigateway:*` on the API
   - `iam:CreateRole`, `iam:AttachRolePolicy`, `iam:PassRole` (scoped to role)
   - `secretsmanager:CreateSecret`, `secretsmanager:PutSecretValue`, `secretsmanager:DescribeSecret`
   - `logs:CreateLogGroup`, `logs:PutRetentionPolicy`
4. Create an access key for this user and save the key pair.

**Produces:** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

**How to verify this worked:**
```bash
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_REGION=us-east-1 \
  aws sts get-caller-identity
# Expected: a JSON object with your account ID and the IAM user ARN
```

---

### Step 2.2 — Supabase project with pgvector

1. Create a project at https://supabase.com (free tier works for evaluation).
2. In your project, go to SQL Editor and run:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
3. In Project Settings → Database, copy the connection string (URI format).
   It looks like: `postgresql://postgres:[password]@[host]:5432/postgres`
   Add `?sslmode=require` if not already present.

**Produces:** `SUPABASE_DB_URL`

**How to verify this worked:**
```bash
SUPABASE_DB_URL="postgresql://..." \
  uv run python -c "
import psycopg
conn = psycopg.connect('$SUPABASE_DB_URL')
cur = conn.execute(\"SELECT extversion FROM pg_extension WHERE extname='vector'\")
row = cur.fetchone()
print('pgvector version:', row[0] if row else 'NOT INSTALLED')
conn.close()
"
# Expected: pgvector version: 0.x.x
```

---

### Step 2.3 — LangFuse project (observability — optional but recommended)

LangFuse traces every answer call: retrieve/rerank/generate spans with cost and
latency. If you skip this step, tracing is a silent no-op — the system works
fine without it.

1. Create an account at https://langfuse.com (cloud) or self-host.
2. Create a new project named "policylens".
3. In Settings → API keys, create a new key pair.
4. Note the host (e.g. `https://cloud.langfuse.com` for the cloud version).

**Produces:** `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`

**How to verify this worked:** see Step 3.3 (test requires the env vars set).

---

### Step 2.4 — Anthropic account (if not already done in Group 1)

If you have not already set up the Anthropic key (Step 1.3), do so at
https://console.anthropic.com. Set the credit ceiling per Step 0.3.

**Produces:** `ANTHROPIC_API_KEY`

---

## Group 3 — Local environment variables

Set these in your shell before running any commands in Groups 4–5. For
production, they arrive in Lambda via AWS Secrets Manager (provisioned by
Terraform) — you do not set them manually in Lambda.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export SUPABASE_DB_URL="postgresql://postgres:[pass]@[host]:5432/postgres?sslmode=require"
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1

# Optional: LangFuse tracing
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_HOST=https://cloud.langfuse.com
```

**How to verify this worked:**
```bash
echo $ANTHROPIC_API_KEY | cut -c1-6     # should print: sk-ant
echo $SUPABASE_DB_URL | cut -c1-14      # should print: postgresql://
aws sts get-caller-identity             # should return your account JSON
```

---

## Group 4 — Database: pgvector migration and backfill

---

### Step 4.1 — Apply the schema and backfill (one command)

The migration script does **both** in a single idempotent run: it applies the
schema (`chunks` table, HNSW + GIN indexes, `tsvector`) from `infra/sql/001_init.sql`,
then backfills all 2,393 chunk embeddings into pgvector. It reuses your local
Chroma embedding cache so it does **not** re-embed (avoids ~2,393 embeddings);
pass `--no-reuse-chroma` only if you want to force fresh embedding. Takes ~2 min.

The DSN is read from the `SUPABASE_DB_URL` env var. Prerequisite: Chroma index
built (Step 1.2) so the embedding cache exists.

```bash
SUPABASE_DB_URL="..." \
  uv run python -m policylens.migrate_pgvector
# Optional flags: --chunks <path>  --batch-size 100  --no-reuse-chroma
# On success it prints: "Migration complete: 2393 chunks in pgvector."
```

**How to verify this worked:**
```bash
SUPABASE_DB_URL="..." uv run python -c "
import psycopg
conn = psycopg.connect('$SUPABASE_DB_URL')
cur = conn.execute(\"SELECT COUNT(*) FROM chunks\")
print('rows in chunks table:', cur.fetchone()[0])
conn.close()
"
# Expected: rows in chunks table: 2393
```

> Safe to re-run: the upsert is idempotent, so a second run won't duplicate rows.

---

### Step 4.2 — Spot-check pgvector retrieval

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
# Expected: a list of 3 chunk IDs like ['105_amazon_com::...::c000', ...]
```

---

## Group 5 — Deploy (Terraform + Docker + Secrets Manager)

**Prerequisites:** Groups 0–4 complete. AWS credentials set in environment.

---

### Step 5.1 — Install Terraform >= 1.6

Download from https://developer.hashicorp.com/terraform/install (version 1.9.8
was tested). Verify:

```bash
terraform version
# Expected: Terraform v1.9.x or later
```

---

### Step 5.2 — Configure terraform.tfvars

```bash
cp infra/terraform.tfvars.example infra/terraform.tfvars
# Edit infra/terraform.tfvars:
#   aws_region = "us-east-1"
#   app_name   = "policylens"
#   retrieval_backend = "pgvector"
#   known_policy_ids  = ""          # leave empty for now; update in Step 5.8
#   langfuse_host / public_key / secret_key  # from Step 2.3 (leave "" to disable)
# Leave ecr_image_uri blank for now; you fill it after the Docker push (Step 5.6)
```

**Important:** `infra/terraform.tfvars` is git-ignored. Never commit it.

---

### Step 5.3 — Terraform init

```bash
terraform -chdir=infra init
```

For remote state (recommended before sharing with a team), add an S3 backend
block to `infra/main.tf` before `init`:

```hcl
backend "s3" {
  bucket         = "your-tf-state-bucket"
  key            = "policylens/terraform.tfstate"
  region         = "us-east-1"
  dynamodb_table = "your-tf-lock-table"
  encrypt        = true
}
```

**How to verify this worked:**
```bash
ls infra/.terraform/
# Expected: providers/ directory exists
```

---

### Step 5.4 — Terraform validate and plan

```bash
terraform -chdir=infra validate
# Expected: Success! The configuration is valid.

terraform -chdir=infra plan
# Expected: Plan output listing resources to create (ECR repo, Lambda, API
#            Gateway, CloudWatch log group, Secrets Manager secrets, IAM roles,
#            authorizer Lambda). No errors.
```

---

### Step 5.5 — Terraform apply

```bash
terraform -chdir=infra apply
# Review the plan, type "yes" to confirm.
```

This creates:
- ECR repository for the Lambda container image
- Three Secrets Manager secrets (empty values — you fill them in Step 5.7):
  `policylens/anthropic_api_key`, `policylens/supabase_db_url`, `policylens/api_key`
- Lambda function (container image — **will fail to initialize until the image
  is pushed in Step 5.6**)
- Lambda authorizer (`api/authorizer.py` as a zip) enforcing `x-api-key` header
- HTTP API Gateway with `POST /ask` route, `CUSTOM` authorization (never open)
- IAM roles with least-privilege policies
- CloudWatch log group

**How to verify this worked:**
```bash
terraform -chdir=infra output api_endpoint
# Expected: prints a URL like https://<id>.execute-api.us-east-1.amazonaws.com
# (endpoint will return 403 until image + secrets are set — that is correct behavior)
```

---

### Step 5.6 — Build and push the Lambda container image

**Prerequisites:** Docker installed and running. `terraform apply` complete.

The image bakes in `bge-small-en-v1.5` and `bge-reranker-base` (~1.8 GB).
First build takes ~10 minutes; subsequent builds are faster (layer cache).

```bash
ECR_REGISTRY=$(terraform -chdir=infra output -raw ecr_repository_url | cut -d/ -f1)
ECR_REPO=$(terraform -chdir=infra output -raw ecr_repository_url)
IMAGE_VERSION=$(git rev-parse --short HEAD)

# Build (CPU-only torch)
docker build \
  --build-arg IMAGE_VERSION=$IMAGE_VERSION \
  -t policylens-lambda:$IMAGE_VERSION \
  api/

# Login to ECR and push
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY

docker tag policylens-lambda:$IMAGE_VERSION $ECR_REPO:$IMAGE_VERSION
docker push $ECR_REPO:$IMAGE_VERSION
```

After the push, update `infra/terraform.tfvars` with the image URI and re-apply:

```bash
# In infra/terraform.tfvars, set:
#   ecr_image_uri = "<ECR_REPO>:<IMAGE_VERSION>"

terraform -chdir=infra apply
```

**How to verify this worked:**
```bash
# Image present in ECR
aws ecr describe-images \
  --repository-name policylens-lambda \
  --region $AWS_REGION \
  --query 'imageDetails[0].imageTags'
# Expected: ["<your-image-version>"]
```

---

### Step 5.7 — Fill in the three Secrets Manager secrets

After `terraform apply`, three empty secrets exist. Fill them in. Until you do,
every request returns 403 (the authorizer fails closed on an empty API key).

```bash
# 1. Anthropic API key
aws secretsmanager put-secret-value \
  --secret-id policylens/anthropic_api_key \
  --secret-string "sk-ant-..."

# 2. Supabase PostgreSQL DSN
aws secretsmanager put-secret-value \
  --secret-id policylens/supabase_db_url \
  --secret-string "postgresql://postgres:[pass]@[host]:5432/postgres?sslmode=require"

# 3. API key — the credential callers send in the x-api-key header.
#    Generate a strong random value and keep it safe (share with authorized clients only).
API_KEY=$(openssl rand -hex 32)
echo "Your API key: $API_KEY"   # SAVE THIS — you cannot recover it from Secrets Manager
aws secretsmanager put-secret-value \
  --secret-id policylens/api_key \
  --secret-string "$API_KEY"
```

**How to verify this worked:**
```bash
# Spot-check the Anthropic key (first 6 chars only)
aws secretsmanager get-secret-value --secret-id policylens/anthropic_api_key \
  | python3 -c "import sys,json; s=json.load(sys.stdin)['SecretString']; print(s[:6])"
# Expected: sk-ant

# Spot-check the API key is non-empty
aws secretsmanager get-secret-value --secret-id policylens/api_key \
  | python3 -c "import sys,json; s=json.load(sys.stdin)['SecretString']; print(len(s), 'chars')"
# Expected: 64 chars
```

---

### Step 5.8 — Populate the KNOWN_POLICY_IDS allowlist

The handler validates `policy_id` against an allowlist before any embedding or
LLM call. Set it to exactly the policy IDs in your index:

```bash
# Get the IDs from your Chroma index
KNOWN=$(uv run python -c "
import chromadb, json
client = chromadb.PersistentClient(path='data/index')
col = client.get_collection('policylens')
results = col.get(include=['metadatas'])
ids = sorted(set(m['policy_id'] for m in results['metadatas']))
print(','.join(ids))
")

echo "Policy count: $(echo $KNOWN | tr ',' '\n' | wc -l)"
```

Set the value in `infra/terraform.tfvars` under `known_policy_ids = "..."` and
re-apply Terraform. Also set it in Lambda → Configuration → Environment
variables if you want to update without a full Terraform apply.

**How to verify this worked:**
```bash
# After terraform apply, check the Lambda env var
aws lambda get-function-configuration \
  --function-name policylens-ask \
  --query "Environment.Variables.KNOWN_POLICY_IDS" \
  --output text | tr ',' '\n' | wc -l
# Expected: 115 (or however many policies you indexed)
```

---

## Group 6 — CI setup (GitHub Actions)

---

### Step 6.1 — Push the repo to GitHub

If not already done:

```bash
git remote add origin https://github.com/<your-username>/policylens.git
git push -u origin main
```

---

### Step 6.2 — Add GitHub Actions secrets

In the GitHub UI: Settings → Secrets and variables → Actions → New repository secret

**Required for the eval job (Ragas + faithfulness gate):**

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Anthropic API key (`sk-ant-...`) |

**Optional — activates pgvector retrieval in CI:**

| Secret name | Value |
|---|---|
| `SUPABASE_DB_URL` | your Supabase DSN |

**Optional — activates LangFuse tracing in CI:**

| Secret name | Value |
|---|---|
| `LANGFUSE_PUBLIC_KEY` | your LangFuse public key |
| `LANGFUSE_SECRET_KEY` | your LangFuse secret key |
| `LANGFUSE_HOST` | e.g. `https://cloud.langfuse.com` |

**Note:** the `checks` job (lint/type/test) runs on every push without any
secrets. The `eval` job is skipped in forks and keyless PRs — it only runs when
`ANTHROPIC_API_KEY` is present.

**How to verify this worked:**
```
GitHub → Actions → trigger a push → watch both jobs
- checks job should complete in ~5 min (green on first run with warm HF cache)
- eval job should appear and complete (if ANTHROPIC_API_KEY is set)
```

---

## Group 7 — Gate recalibration from baseline_v1

After your first CI eval run completes (Group 6), compare the CI faithfulness
score against `eval/baselines/baseline_v1.json`:

```bash
python3 -c "
import json
with open('eval/baselines/baseline_v1.json') as f:
    b = json.load(f)
print('Baseline faithfulness:', b.get('ragas', {}).get('faithfulness'))
print('Baseline abstention_accuracy:', b.get('house_metrics', {}).get('abstention_accuracy'))
"
```

If the observed baseline faithfulness differs significantly from the default 0.80
in `eval/thresholds.yaml`, update the file:

```bash
# Edit eval/thresholds.yaml:
#   faithfulness: <your observed baseline, e.g. 0.74>
#   abstention_accuracy: 0.90   # keep 0.90 unless baseline showed lower

git add eval/thresholds.yaml
git commit -m "eval: recalibrate gate thresholds from baseline_v1"
git push
```

You can also set a repo-level variable (not secret) in GitHub:
Settings → Variables → Actions → `FAITHFULNESS_THRESHOLD = <value>`. This
overrides `thresholds.yaml` without a code change.

**How to verify this worked:**
Push a commit and watch the CI eval job. The gate step should print:
```
faithfulness 0.74 >= 0.74 PASS
abstention_accuracy 0.92 >= 0.90 PASS
All gates passed.
```

---

## Summary: env vars produced by each group

| Variable | Group | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | 2.4 / 1.3 | generate.py, Ragas judge, eval |
| `SUPABASE_DB_URL` | 2.2 | PgVectorRetriever, migration |
| `AWS_ACCESS_KEY_ID` | 2.1 | Terraform, AWS CLI, ECR push |
| `AWS_SECRET_ACCESS_KEY` | 2.1 | Terraform, AWS CLI, ECR push |
| `AWS_REGION` | 2.1 | Terraform, AWS CLI |
| `LANGFUSE_PUBLIC_KEY` | 2.3 | observability.py (absent = no-op) |
| `LANGFUSE_SECRET_KEY` | 2.3 | observability.py (absent = no-op) |
| `LANGFUSE_HOST` | 2.3 | observability.py (absent = no-op) |
| `FAITHFULNESS_THRESHOLD` | 7 | eval/gate.py (optional, overrides yaml) |
| `EVAL_JUDGE_MODEL` | — | optional override of claude-opus-4-8 |
| Secrets Manager: `policylens/anthropic_api_key` | 5.7 | Lambda → generate.py |
| Secrets Manager: `policylens/supabase_db_url` | 5.7 | Lambda → PgVectorRetriever |
| Secrets Manager: `policylens/api_key` | 5.7 | Lambda authorizer (x-api-key) |

All names are per `docs/CONTRACTS.md` §11.
