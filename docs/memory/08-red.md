# Red-team notes — tag: `red` (red-team engineer)

Append-only, dated. Adversarial findings: what broke, what held. PoC tests live
in `tests/red/` (prefix `test_red_*`). Run: `uv run pytest tests/red/ -q`.

---

2026-06-13 | red-team | First adversarial pass on the production tier (handler,
authorizer, generate, pgvector, migrate, observability). 27 PoC tests added in
tests/red/, all green.

WHAT BROKE
- **HIGH — handler crashes on non-string `event["body"]`** (api/handler.py:368-382).
  `_parse_and_validate` is called OUTSIDE handler()'s try/except, and the size
  guard does `len(raw_body)` on a non-str while json.loads only catches
  JSONDecodeError/ValueError (not TypeError). body=dict/list/int → unhandled
  TypeError → Lambda 502 + full traceback in CloudWatch (info leak), no clean
  4xx. Repro: tests/red/test_red_handler_abuse.py::*_crashes_FINDING. Owner:
  infra-engineer. NOT a spend issue (crashes before answer()).
- **MED — citation fabrication on bad/absent [N] markers** (generate.py:132-139,
  _build_citations). If the LLM output's markers are all out-of-range ([99]),
  zero ([0]), or absent, the "fallback cite top hit" path attaches a citation to
  hits[0] that the model never referenced → answerable=True with a
  grounded-looking but fabricated linkage. Violates the "never fabricate a
  citation / every claim cites the chunk used" golden rule under an adversarial/
  injected model. Repro: tests/red/test_red_citations.py. Owner: eval-engineer
  (harden) / observability.
- **LOW — abstention prefix is brittle** (generate.py:246). `raw.upper().
  startswith("UNANSWERABLE")` makes "Unanswerable? No, they collect X [1]." a
  false-abstention (real answer suppressed). Owner: eval-engineer.
- **LOW — allowlist mislabeled count** (handler.py:143-263). Docstring/comment +
  memory say "115-policy set"; the literal frozenset has 117 unique entries (no
  dups; just a miscount). Spend-safe (bounded), but the claim is wrong. Owner:
  infra-engineer.
- **LOW/Suspicion — authorizer TypeError on list-valued x-api-key**
  (authorizer.py:76). hmac.compare_digest(list,str) raises, unhandled. NOT
  reachable from a real HTTP API v2 event (gateway comma-joins multi-values to a
  str) and a crash still fails closed at the gateway. Owner: infra-engineer.

WHAT HELD UP (good coverage)
- SQL injection: pgvector ANN/FTS + migrate upsert are fully parameterized; the
  payload `x'; DROP TABLE chunks; --` always travels as a bound param, never in
  SQL text (test_red_sqli.py).
- Authorizer fail-closed: wrong key, empty/missing header, null headers, wrong
  header case, empty secret value, transient Secrets-Manager failure (no poison
  cache) all DENY (test_red_authorizer.py). Constant-time compare; no env bypass.
- Handler spend gates: oversized body (8KB), >500-char query, bad/array/missing
  body, top_k out-of-range/float-string/huge all reject BEFORE answer(). top_k
  bool True → 1 (benign). 500s return only {error, request_id}, no trace/secret.
- Allowlist fails SAFE: unset/""→bounded builtin; ","→empty (deny-all); no value
  yields a wildcard (test_red_allowlist_fallback.py).
- Log injection: query never logged; policy_id is logged but only after exact
  allowlist match (attacker can't inject CRLF).

USER'S CORE FEAR (anonymous scammer running up spend): NOT currently exploitable
via the API surface as designed. Auth is enforced in Terraform (authorization_
type=CUSTOM, fail-closed authorizer) and every cost-bearing path is gated by
validation + allowlist before answer(). The residual spend risk is volumetric
(a holder of the single shared API key spamming valid requests) — bounded only
by Lambda reserved_concurrency=5 + the AWS Budget hard-stop, not by any per-key
rate limit (documented limitation, not a new finding). The body-type crash is an
availability/info-leak bug, not a spend bypass.

---

2026-06-13 | orchestrator (red→fix loop) | Routed the two real findings to owners.
- **#1 HIGH FIXED (infra)**: api/handler.py now type-guards event["body"] — a
  non-string body returns a clean 400 before any json.loads/len, and the
  _parse_and_validate call is wrapped in try/except so any future parse error
  emits the contractual 500 (never a 502+traceback). Red PoC tests flipped from
  asserting the crash to asserting clean 400 + no spend (tripwire). 51 handler/
  authorizer/abuse tests green; ruff+pyright clean.
- **#2 MED + #3 LOW**: delegated to eval-engineer (citation fabrication →
  abstain; tighten UNANSWERABLE prefix). Eval-gated behavior change (affects
  abstention rate / golden-set gates) — being validated in a worktree, not
  fixed inline. See 05-eval.md.
- #4/#5 LOW left as-is for now (cosmetic miscount; unreachable via real gateway).
