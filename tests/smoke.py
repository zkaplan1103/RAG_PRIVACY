"""Smoke test: 5 questions including one unanswerable.

Run with:
  make smoke        — stub mode (no API key, no index required)
  make smoke-real   — real mode (requires ANTHROPIC_API_KEY + built index)
"""
import os
import sys

sys.path.insert(0, ".")

# Stub questions use fixture_policy (the 10-row fixture)
SMOKE_QUESTIONS_STUB = [
    # (query, policy_id, expect_answerable)
    ("Does this app share my data with advertisers?", "fixture_policy", True),
    ("How long is my data retained?", "fixture_policy", True),
    ("Can I delete my account data?", "fixture_policy", True),
    ("What encryption does the service use?", "fixture_policy", True),
    ("Does this policy cover biometric data collection?", "fixture_policy", False),
]

# Real questions use actual OPP-115 policies from the index
SMOKE_QUESTIONS_REAL = [
    # (query, policy_id, expect_answerable)
    ("Does this policy share data with advertisers?", "1017_sci_news_com", True),
    ("How long is user data retained?", "1028_redorbit_com", True),
    ("Can I delete my personal data?", "1034_aol_com", True),
    ("What security measures protect my data?", "1028_redorbit_com", True),
    # Unanswerable — OPP-115 policies don't mention biometric data
    ("Does this policy cover biometric data or facial recognition?", "1017_sci_news_com", False),
]


def run_smoke(use_real: bool = False) -> None:
    if use_real:
        from src.policylens.config import DEFAULT_CONFIG
        from src.policylens.generate import answer
        from src.policylens.retrieve import make_retriever

        retriever = make_retriever(DEFAULT_CONFIG)

        def ask(q: str, pid: str) -> object:
            return answer(q, pid, retriever, DEFAULT_CONFIG)

        questions = SMOKE_QUESTIONS_REAL
    else:
        from src.policylens.generate import canned_answer

        def ask(q: str, pid: str) -> object:  # type: ignore[misc]
            return canned_answer(policy_id=pid)

        questions = SMOKE_QUESTIONS_STUB

    passed = 0
    failed = 0
    for query, policy_id, expect_answerable in questions:
        result = ask(query, policy_id)
        ok = True
        issues = []

        if use_real:
            if result["answerable"] != expect_answerable:
                ok = False
                issues.append(
                    f"answerable={result['answerable']}, expected={expect_answerable}"
                )
            if result["answerable"] and not result["citations"]:
                ok = False
                issues.append("answerable=True but citations is empty")
        else:
            if "answerable" not in result:
                ok = False
                issues.append("missing 'answerable' key")

        status = "PASS" if ok else "FAIL"
        print(f"[{status}] [{policy_id}] {query[:55]}")
        if use_real and result.get("answerable"):
            print(f"       text: {result['text'][:120]}")
            for c in result.get("citations", []):
                print(f"       cite: {c['chunk_id']} — {c['quote'][:60]}")
        for issue in issues:
            print(f"       ^ {issue}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n{passed}/{passed+failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    use_real = os.environ.get("SMOKE_REAL", "0") == "1"
    run_smoke(use_real=use_real)
