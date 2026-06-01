# PolicyLens — Claude Code starter kit

This folder is the **orchestration layer** for building the privacy-policy RAG
project with Claude Code. It does not contain the app code — Claude Code writes
that. It contains the plan, the rules, the interface contracts, the subagents,
and the tagged-memory scaffold that make the build fast and token-cheap.

## What's here
```
BUILD_PLAN.md          ← read this first: phases, parallelization, kickoff prompts
CLAUDE.md              ← loaded by Claude Code every session (the rules)
docs/CONTRACTS.md      ← frozen interfaces; the seam that lets agents run in parallel
docs/memory/           ← tag-based memory (INDEX + append-only dated notes)
.claude/agents/        ← 4 builder subagents (data, index, rag, ui)
```

## How to use
1. Create an empty git repo and copy this whole folder's contents into its root
   (so `.claude/`, `CLAUDE.md`, `BUILD_PLAN.md`, `docs/` sit at the top level).
2. Open Claude Code in that repo.
3. Paste the Phase 0 kickoff prompt from `BUILD_PLAN.md` §9.
4. After Phase 0 looks right, paste the Phase 1 dispatch prompt.

## The three ideas that make this efficient
- **Contract-first** (`docs/CONTRACTS.md`): freeze the schemas, then four agents
  build against interfaces + stubs in parallel instead of blocking each other.
- **Delegate to subagents**: only their final report returns to the main
  context — the biggest token lever in Claude Code.
- **Tag-based memory**: agents read only the memory file matching their tag, so
  context stays small as the project grows.

## Pattern source
Subagent frontmatter and memory-scope conventions follow current Claude Code
docs and the community reference repo `shanraisshan/claude-code-best-practice`.
Requires Claude Code ≥ v2.1.33 for per-agent `memory:` scopes (see BUILD_PLAN §10).
