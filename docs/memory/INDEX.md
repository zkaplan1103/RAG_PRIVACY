# Memory INDEX — read this first, then load only your tagged file(s)

Append-only, dated notes. Each subagent reads only the files matching its tag,
then appends what it learned. Keeps context small as the project grows.

| Tag | File | Owner |
|------|------|-------|
| `decisions` | `00-decisions.md` | everyone (append one line per meaningful decision) |
| `data` | `01-data.md` | data-engineer |
| `retrieval` | `02-retrieval.md` | index-engineer |
| `generation` | `03-generation.md` | rag-engineer |
| `ui` | `04-ui.md` | ui-engineer |
| `eval` | `05-eval.md` | (Project C) |

Protocol: before work → read INDEX + your tagged file. After work → append a
dated entry to your tagged file + one line to `00-decisions.md`. Never edit
another agent's file; never load a file whose tag isn't yours.
