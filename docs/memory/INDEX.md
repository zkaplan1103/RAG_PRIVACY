# Memory INDEX — read this first, then load only your tagged file(s)

Append-only, dated notes. Each subagent reads only the files matching its tag,
then appends what it learned. Keeps context small as the project grows.

| Tag | File | Owner |
|------|------|-------|
| `decisions` | `00-decisions.md` | everyone (append one line per meaningful decision) |
| `data` | `01-data.md` | data-engineer (v1, retired) |
| `retrieval` | `02-retrieval.md` | index-engineer (v1) → **vector-engineer** (v2: pgvector, hybrid, rerank) |
| `generation` | `03-generation.md` | rag-engineer (v1, retired) |
| `ui` | `04-ui.md` | ui-engineer (v1, retired) |
| `eval` | `05-eval.md` | **eval-engineer** (v2: golden set, Ragas, promptfoo, gate) |
| `observability` | `06-observability.md` | **observability-engineer** (v2: LangFuse) |
| `infra` | `07-infra.md` | **infra-engineer** (v2: Lambda, Terraform, CI) |

Protocol: before work → read INDEX + your tagged file. After work → append a
dated entry to your tagged file + one line to `00-decisions.md`. Never edit
another agent's file; never load a file whose tag isn't yours.
docs-engineer is the exception: read-all, write only `00-decisions.md`.
