# PolicyLens — Build Plan (v1 — SHIPPED, historical)

> **Status 2026-06-11:** the demo described here is complete (Phases 0–3 done).
> The project is now being upgraded to a production system — see
> `docs/UPGRADE_PLAN.md` for the current plan and `.claude/agents/` for the v2
> agent roster. This file is kept as the v1 record; don't execute it again.

> A RAG system that answers plain-English questions about real privacy policies
> and **cites the exact clause** it used. Built on the OPP-115 / PrivacyQA /
> PolicyQA corpora. Designed to be built fast by a small team of Claude Code
> subagents working in parallel.

This file is the human-readable plan. The machine-readable rules live in
`CLAUDE.md` (loaded every session) and `docs/CONTRACTS.md` (the interfaces every
agent builds against). Read all three before kicking off.

---

## 1. Goal & scope (keep it small)

Ship a working demo, not production. Definition of done:

1. Ask a natural-language question ("Does this app share my data with advertisers?")
2. Retrieve the relevant clauses from a chosen policy.
3. Answer in plain English **with inline citations** to the source clause.
4. **Abstain** ("the policy doesn't say") when the answer isn't in the document — PrivacyQA explicitly contains unanswerable questions, so handling this is a feature, not an edge case.
5. A one-screen Streamlit UI to demo it.
6. A clean eval seam so Project C (the eval harness) can plug in later. Do **not** build the full eval here.

Out of scope for v1: auth, multi-user, fancy styling, training/fine-tuning anything.

---

## 2. Why contract-first (this is what makes parallelism real)

The naive plan — "one agent does data, one does retrieval, one does generation" — fails because retrieval depends on the chunk format, generation depends on the retriever, and the UI depends on the answer format. Run those in parallel and they collide.

The fix: **freeze the interfaces before any parallel work starts.** `docs/CONTRACTS.md` defines the chunk schema, the retriever interface, the answer schema, and the config object. Once those are frozen, every builder agent codes against the *interface* plus a tiny *fixture/stub*, so none of them has to wait for another to finish. Integration is then just swapping stubs for real implementations.

This is also a strong portfolio signal — "I designed the seams so four workstreams could run concurrently" is exactly the engineering-judgment story the AI-implementation roles want.

---

## 3. Component architecture

```
            ┌─────────────┐   chunks.jsonl   ┌──────────────┐
 raw policies│  ingest +   │ ───────────────▶ │  embed +     │
 (OPP-115)   │  chunk      │   (chunk schema) │  index       │
            └─────────────┘                   └──────┬───────┘
                                          retriever interface
                                                     │
   answer schema  ┌──────────────┐   retrieve(q)     ▼
 ◀──────────────  │  generate +  │ ◀───────────  Retriever
   Streamlit UI   │  cite/abstain│
                  └──────────────┘
```

Default stack (chosen for speed + zero required API keys during dev):

| Layer | Default | Why |
|---|---|---|
| Lang | Python 3.11 + `uv` | fast installs, lockfile |
| Embeddings | local `bge-small-en-v1.5` (sentence-transformers) | free, no key, good enough; swappable to OpenAI via config |
| Vector store | Chroma (persistent) | trivial setup, on-disk cache |
| LLM (generation) | Claude API (`claude-haiku` for dev, `claude-sonnet` for final) | matches the "Claude integration" line on the résumé; cheap to iterate |
| UI | Streamlit | one file, instantly demoable |

All of these are config-switchable — see the `Config` object in `docs/CONTRACTS.md`.

---

## 4. The agent roster

The **main session is the orchestrator.** It owns Phase 0 and integration; it delegates the heavy isolated work to subagents so their context never pollutes the main thread. Models are routed to cost: recon on Haiku, building on Sonnet, orchestration on the main model.

| Agent | File | Model | Does | Memory tag |
|---|---|---|---|---|
| `Explore` (built-in) | — | haiku | read-only recon: locate dataset formats, sanity-check downloads | — |
| `data-engineer` | `.claude/agents/data-engineer.md` | sonnet | download + parse OPP-115 → `chunks.jsonl` per chunk schema | `data` |
| `index-engineer` | `.claude/agents/index-engineer.md` | sonnet | embed + build Chroma index + implement `Retriever` | `retrieval` |
| `rag-engineer` | `.claude/agents/rag-engineer.md` | sonnet | prompt + generate + cite + abstain, against `Retriever` | `generation` |
| `ui-engineer` | `.claude/agents/ui-engineer.md` | sonnet | Streamlit app against the answer schema | `ui` |

Each builder runs with `isolation: worktree` so four agents can write files at once without merge conflicts; the orchestrator merges the clean worktrees during integration.

---

## 5. Phases

### Phase 0 — Scaffold + contracts + data (orchestrator, serial — do NOT parallelize)
1. `uv init`, create the directory layout, `.gitignore` (ignore `data/raw/`, `data/index/`, `.venv/`).
2. Confirm `docs/CONTRACTS.md` is filled in and frozen. **Nothing parallel starts until this is true.**
3. Use the `Explore` agent (Haiku, read-only) to fetch and inspect the dataset READMEs and confirm file formats before downloading anything large.
4. Acquire data into `data/raw/` (see §7). Verify checksums/counts. Record the exact source + license in `docs/memory/00-decisions.md`.

### Phase 1 — Parallel build (4 subagents, worktree-isolated)
Dispatch all four with the Task tool in one batch. Each builds against the contract + a stub, writes tests, and returns **only a short report** (files touched, how to run, open questions). Do not let their full logs back into main context.
- `data-engineer` → `chunks.jsonl` + loader
- `index-engineer` → index builder + `Retriever` (uses a 10-row fixture of chunks)
- `rag-engineer` → `answer()` (uses a fake retriever returning canned chunks)
- `ui-engineer` → `app.py` (uses a fake `answer()` returning a canned answer)

### Phase 2 — Integration (orchestrator, serial)
1. Merge worktrees. Swap every stub for the real implementation.
2. Build the real index from real chunks. Run the end-to-end smoke test (`make smoke`): 5 hand-picked questions incl. one unanswerable.
3. Fix the seams. Use `/rewind` rather than stacking corrections if a step goes sideways.

### Phase 3 — Eval seam + docs (orchestrator)
1. Wire the **eval seam only**: a `golden.jsonl` loader that maps PrivacyQA/PolicyQA Q&A onto the answer schema, plus an empty `eval/` package with a documented interface. (Project C fills this in.)
2. Write `README.md` framed for a **non-technical reader**: problem → approach → demo gif → "what worked / what didn't / cost per query." This README is half the deliverable for hiring.

---

## 6. Token & memory optimization playbook

These are the rules; `CLAUDE.md` enforces the load-bearing ones every session.

- **Delegate heavy work to subagents.** Their final report is the *only* thing that returns to the main context — this is the highest-leverage token move available. Keep the orchestrator's context for orchestration.
- **Route models to cost.** Haiku/`Explore` for read-only search and recon. Sonnet for implementation. Don't burn the main model reading datasets.
- **Never load big artifacts into context.** Never `cat chunks.jsonl` or open the 25k-row PolicyQA file — sample with `head`, query with `jq`/`grep`, or read row counts. Cache embeddings to `data/index/` so they're built once.
- **Context hygiene.** `/clear` between phases (genuinely new work). `/compact` *manually before* auto-compact fires (auto-compact triggers at the worst moment in a long run). `/rewind` on a wrong turn instead of arguing with a polluted context.
- **Worktree isolation** for the parallel builders, so concurrent writes don't corrupt each other.
- **Keep `CLAUDE.md` under ~200 lines.** It loads every session; bloat there taxes every turn. Detail goes in `docs/`, loaded on demand.

---

## 7. Data sources (verify before bulk download)

| Dataset | What | Use | Note |
|---|---|---|---|
| OPP-115 | 115 real website privacy policies, annotated by law students | source documents to chunk + index | `usableprivacy.org/data` — **research/teaching license only (CC-NC spirit).** Record this. |
| PrivacyQA | ~1,750 questions + ~3,500 expert answer annotations over app policies; includes unanswerable Qs | abstention behavior + eval golden set | GitHub: `AbhilashaRavichander/PrivacyQA_EMNLP` |
| PolicyQA | ~25k question-passage-answer triples curated from OPP-115 | eval golden set (Project C) | GitHub: `wasiahmad/PolicyQA` — large; sample, never load whole |

License compliance matters here and it's on-brand: this whole project is about privacy transparency. Keep raw data out of git and cite sources in the README.

---

## 8. Tag-based memory (how it works here)

Claude Code gives you two real persistence primitives: per-agent `memory: project` stores (in `.claude/agent-memory/<name>/`) and the auto-memory file. On top of those we add a lightweight **tag convention** so an agent loads *only* the notes relevant to its job instead of the whole project history:

- `docs/memory/INDEX.md` maps **tags → files**.
- Each note file has front-matter `tags: [...]` and is append-only and dated.
- Every subagent prompt says: *"read `docs/memory/INDEX.md`, then read only the files matching your tag(s); when done, append a dated entry to your tagged file and one line to `00-decisions.md`."*

So the `index-engineer` reads only `02-retrieval.md`, never the data or UI history. Token cost stays bounded as the project grows. This is a convention layered on Claude Code's primitives, not a built-in feature — but it behaves like the "tag-based memory" you wanted.

---

## 9. Kickoff (paste into Claude Code at the repo root)

```
Read CLAUDE.md, BUILD_PLAN.md, and docs/CONTRACTS.md in full.
Then execute Phase 0 yourself (scaffold + freeze contracts + acquire data),
serially. Do NOT start Phase 1 until docs/CONTRACTS.md is frozen and the data
is verified in data/raw/. Report back when Phase 0 is done and wait for my go.
```

After Phase 0 looks right:

```
Dispatch Phase 1: spawn data-engineer, index-engineer, rag-engineer, and
ui-engineer in parallel via the Task tool, each in a worktree, each building
against docs/CONTRACTS.md plus a stub. Collect only their final reports.
Then do Phase 2 integration yourself.
```

---

## 10. Version note

Subagent `memory:` scopes require Claude Code ≥ v2.1.33; `isolation: worktree`
and the `claude agents` command are recent additions too. If you're on an older
build, drop the `memory:` and `isolation:` frontmatter lines — the plan still
works, you just lose per-agent persistence and have to run the builders
sequentially instead of in parallel worktrees. Check with `claude --version`.
