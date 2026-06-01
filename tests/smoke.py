"""Smoke test: 5 questions including one unanswerable.

Run with: make smoke
Uses FixtureRetriever + canned stubs until Phase 2 integration is done.
After integration, set SMOKE_REAL=1 to use the real Chroma index + LLM.
"""
import os
import sys

sys.path.insert(0, ".")


SMOKE_QUESTIONS = [
    # (query, policy_id, expect_answerable)
    ("Does this app share my data with advertisers?", "fixture_policy", True),
    ("How long is my data retained?", "fixture_policy", True),
    ("Can I delete my account data?", "fixture_policy", True),
    ("What encryption does the service use?", "fixture_policy", True),
    # Unanswerable — no clause covers biometric data in the fixture
    ("Does this policy cover biometric data collection?", "fixture_policy", False),
]


def run_smoke(use_real: bool = False) -> None:
    if use_real:
        from src.policylens.config import DEFAULT_CONFIG
        from src.policylens.retrieve import ChromaRetriever
        from src.policylens.generate import answer

        retriever = ChromaRetriever(DEFAULT_CONFIG)
        ask = lambda q, pid: answer(q, pid, retriever, DEFAULT_CONFIG)
    else:
        from src.policylens.generate import canned_answer
        ask = lambda q, pid: canned_answer(policy_id=pid)

    passed = 0
    failed = 0
    for query, policy_id, expect_answerable in SMOKE_QUESTIONS:
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
        # In stub mode we just verify the schema is correct
        else:
            if "answerable" not in result:
                ok = False
                issues.append("missing 'answerable' key")

        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {query[:60]}")
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
