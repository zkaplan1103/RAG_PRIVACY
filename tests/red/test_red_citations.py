"""Regression guards for citation-integrity bugs found in the red-team pass.

Finding #2 (Critical): _build_citations fallback fabricated a citation when the
LLM produced no valid [N] markers (out-of-range, [0], or absent entirely).
Fixed: answer() now abstains (answerable=False, citations=[]) instead.

Finding #3 (Low): raw.upper().startswith("UNANSWERABLE") wrongly abstained on
answers beginning "Unanswerable? No — ...".
Fixed: exact match raw.strip().upper() == "UNANSWERABLE" only.

These tests previously ASSERTED the buggy behavior.  After the fix they assert
the CORRECT behavior and serve as regression guards.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.policylens.config import Config
from src.policylens.generate import ABSTENTION_TEXT, answer
from src.policylens.ingest import Chunk
from src.policylens.retrieve import RetrievedChunk

# ---------------------------------------------------------------------------
# Helpers (duplicated locally so this file is self-contained)
# ---------------------------------------------------------------------------


def _make_chunk(
    chunk_id: str,
    text: str,
    score: float,
    section: str = "Data Sharing",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=chunk_id,
            policy_id="red_policy",
            policy_name="Red Policy",
            section=section,
            text=text,
            char_start=0,
            char_end=len(text),
            source_url=None,
        ),
        score=score,
    )


class _FakeRetriever:
    def __init__(self, hits: list[RetrievedChunk]):
        self._hits = hits

    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]:
        return self._hits[:k]


def _mock_llm(response_text: str):
    """Patch anthropic.Anthropic so no real API call is made."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=response_text)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    return patch("src.policylens.generate.anthropic.Anthropic", return_value=mock_client)


_CFG = Config(score_floor=0.30)

_HITS = [
    _make_chunk(
        "red_policy::sec::c001",
        "We collect email addresses for account creation.",
        score=0.80,
    ),
    _make_chunk(
        "red_policy::sec::c002",
        "Data is shared with advertising partners.",
        score=0.75,
    ),
]


# ---------------------------------------------------------------------------
# Finding #2 — out-of-range marker → was: top-hit citation fabricated
#              now: abstain (answerable=False, citations=[])
# ---------------------------------------------------------------------------


def test_marker_out_of_range_causes_abstention() -> None:
    """[99] is out of range for a 2-hit context — must abstain, not fabricate."""
    with _mock_llm("We collect email addresses [99]."):
        result = answer(
            "Does the policy collect emails?",
            "red_policy",
            _FakeRetriever(_HITS),
            _CFG,
        )

    assert result["answerable"] is False, (
        "Out-of-range marker must cause abstention, not a fabricated citation"
    )
    assert result["citations"] == []
    assert result["text"] == ABSTENTION_TEXT


def test_marker_zero_causes_abstention() -> None:
    """[0] is not a valid 1-indexed marker — must abstain, not fabricate."""
    with _mock_llm("Data is shared [0]."):
        result = answer(
            "Who gets my data?",
            "red_policy",
            _FakeRetriever(_HITS),
            _CFG,
        )

    assert result["answerable"] is False, (
        "[0] marker must cause abstention, not a fabricated citation"
    )
    assert result["citations"] == []
    assert result["text"] == ABSTENTION_TEXT


def test_no_marker_answer_causes_abstention() -> None:
    """Model answers in prose with zero [N] markers — must abstain, not fabricate."""
    with _mock_llm("The policy collects email addresses for account registration."):
        result = answer(
            "What data is collected?",
            "red_policy",
            _FakeRetriever(_HITS),
            _CFG,
        )

    assert result["answerable"] is False, (
        "No [N] marker must cause abstention, not a fabricated top-hit citation"
    )
    assert result["citations"] == []
    assert result["text"] == ABSTENTION_TEXT


def test_fabrication_does_not_cite_unreferenced_chunk() -> None:
    """Even with multiple good hits, an answer with no valid marker must abstain."""
    many_hits = [
        _make_chunk(
            f"red_policy::sec::c{i:03d}",
            f"Clause {i} text about privacy.",
            score=0.90 - i * 0.05,
        )
        for i in range(5)
    ]
    with _mock_llm("The policy has some privacy protections."):
        result = answer(
            "Tell me about privacy.",
            "red_policy",
            _FakeRetriever(many_hits),
            _CFG,
        )

    assert result["answerable"] is False
    assert result["citations"] == []


# ---------------------------------------------------------------------------
# Finding #2 — valid in-range markers STILL work (non-regression)
# ---------------------------------------------------------------------------


def test_valid_marker_still_produces_citation() -> None:
    """[1] and [2] are in-range — the happy path must be unaffected by the fix."""
    with _mock_llm("The policy collects emails [1] and shares with partners [2]."):
        result = answer(
            "Describe data practices.",
            "red_policy",
            _FakeRetriever(_HITS),
            _CFG,
        )

    assert result["answerable"] is True
    assert len(result["citations"]) == 2
    chunk_ids = {c["chunk_id"] for c in result["citations"]}
    assert "red_policy::sec::c001" in chunk_ids
    assert "red_policy::sec::c002" in chunk_ids


def test_single_valid_marker_still_answerable() -> None:
    """Only [1] referenced — exactly one citation, not a fabricated second."""
    with _mock_llm("Emails are collected [1]."):
        result = answer(
            "What is collected?",
            "red_policy",
            _FakeRetriever(_HITS),
            _CFG,
        )

    assert result["answerable"] is True
    assert len(result["citations"]) == 1
    assert result["citations"][0]["chunk_id"] == "red_policy::sec::c001"


# ---------------------------------------------------------------------------
# Finding #3 — UNANSWERABLE must be exact, not startswith
# ---------------------------------------------------------------------------


def test_unanswerable_exact_sentinel_abstains() -> None:
    """'UNANSWERABLE' (exact) must still trigger abstention."""
    with _mock_llm("UNANSWERABLE"):
        result = answer(
            "Does the policy cover biometric data?",
            "red_policy",
            _FakeRetriever(_HITS),
            _CFG,
        )

    assert result["answerable"] is False
    assert result["citations"] == []
    assert result["text"] == ABSTENTION_TEXT


def test_unanswerable_case_insensitive_abstains() -> None:
    """'unanswerable' (lowercase exact) still triggers abstention."""
    with _mock_llm("unanswerable"):
        result = answer(
            "Does the policy cover biometric data?",
            "red_policy",
            _FakeRetriever(_HITS),
            _CFG,
        )

    assert result["answerable"] is False
    assert result["citations"] == []


def test_unanswerable_with_whitespace_abstains() -> None:
    """'  UNANSWERABLE  ' (extra whitespace) still abstains — strip() handles it."""
    with _mock_llm("  UNANSWERABLE  "):
        result = answer(
            "Does the policy cover biometric data?",
            "red_policy",
            _FakeRetriever(_HITS),
            _CFG,
        )

    assert result["answerable"] is False
    assert result["citations"] == []


def test_unanswerable_prefix_does_not_abstain() -> None:
    """Finding #3: 'Unanswerable? No — ...' with a valid [1] must NOT abstain.

    Previously raw.upper().startswith('UNANSWERABLE') wrongly caught this.
    The fix (exact match only) means this answer reaches _build_citations.
    Since the response contains [1], we expect an answerable result.
    """
    with _mock_llm("Unanswerable? No — the policy does collect emails [1]."):
        result = answer(
            "Does the policy collect emails?",
            "red_policy",
            _FakeRetriever(_HITS),
            _CFG,
        )

    # The answer starts with "Unanswerable?" but must NOT abstain
    assert result["answerable"] is True
    assert len(result["citations"]) >= 1


def test_unanswerable_prefix_no_marker_abstains_via_citation_path() -> None:
    """'Unanswerable? No — ...' without any [N] marker: not a sentinel abstention,
    but _build_citations returns [] so the no_valid_citation path abstains.
    """
    with _mock_llm("Unanswerable? No — the policy has some rules."):
        result = answer(
            "Does the policy collect emails?",
            "red_policy",
            _FakeRetriever(_HITS),
            _CFG,
        )

    # Not the UNANSWERABLE sentinel, but no valid marker → abstain via no_valid_citation
    assert result["answerable"] is False
    assert result["citations"] == []
    assert result["text"] == ABSTENTION_TEXT
