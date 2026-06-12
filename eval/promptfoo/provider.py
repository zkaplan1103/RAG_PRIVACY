"""promptfoo Python script provider for PolicyLens.

Wraps answer() so promptfoo can call it as an LLM provider.
The provider receives a prompt string in the format:
  <query>|||<policy_id>
and returns the Answer JSON as the output.

See eval/promptfoo/promptfooconfig.yaml for usage.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path when called from promptfoo
_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))


def call_api(  # noqa: ANN201 — promptfoo signature
    prompt: str,
    options: dict,
    context: dict,
) -> dict:
    """promptfoo provider entry point.

    Returns {"output": <str>} on success, {"error": <str>} on failure.
    The output is the Answer JSON string so assertions can inspect fields.
    """
    # Parse the compound prompt
    if "|||" in prompt:
        query, policy_id = prompt.split("|||", 1)
        query = query.strip()
        policy_id = policy_id.strip()
    else:
        return {"error": f"Invalid prompt format (expected query|||policy_id): {prompt!r}"}

    try:
        from src.policylens.config import Config
        from src.policylens.generate import answer

        # Use FixtureRetriever if no API key or index — safe for CI
        if not os.environ.get("ANTHROPIC_API_KEY"):
            from src.policylens.generate import canned_answer
            ans = canned_answer(policy_id=policy_id)
        elif not Path(Config().index_dir).exists():
            from src.policylens.generate import canned_answer
            ans = canned_answer(policy_id=policy_id)
        else:
            from src.policylens.retrieve import ChromaRetriever
            cfg = Config()
            retriever = ChromaRetriever(cfg)
            ans = answer(query, policy_id, retriever, cfg)

        return {"output": json.dumps(dict(ans))}

    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
