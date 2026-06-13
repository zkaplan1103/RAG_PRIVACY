"""Unit tests for generate.py — mock Anthropic client, no API key needed."""
from unittest.mock import MagicMock, patch

from src.policylens.config import Config
from src.policylens.generate import (
    ABSTENTION_TEXT,
    MAX_QUERY_CHARS,
    _short_quote,
    answer,
    canned_answer,
)
from src.policylens.ingest import Chunk
from src.policylens.retrieve import RetrievedChunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunk(
    chunk_id: str, text: str, score: float, section: str = "Data Sharing"
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=chunk_id,
            policy_id="test_policy",
            policy_name="Test Policy",
            section=section,
            text=text,
            char_start=0,
            char_end=len(text),
            source_url=None,
        ),
        score=score,
    )


class FakeRetriever:
    def __init__(self, hits: list[RetrievedChunk]):
        self._hits = hits

    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]:
        return self._hits[:k]


def _mock_anthropic(response_text: str):
    """Return a context manager that patches anthropic.Anthropic."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=response_text)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    return patch("src.policylens.generate.anthropic.Anthropic", return_value=mock_client)


# ---------------------------------------------------------------------------
# Abstention: low scores
# ---------------------------------------------------------------------------

def test_abstain_low_scores():
    cfg = Config(score_floor=0.30)
    hits = [_make_chunk("p::sec::c000", "Some text about data.", score=0.10)]
    retriever = FakeRetriever(hits)
    result = answer("any question", "test_policy", retriever, cfg)
    assert result["answerable"] is False
    assert result["citations"] == []
    assert result["text"] == ABSTENTION_TEXT


def test_abstain_empty_results():
    cfg = Config(score_floor=0.30)
    retriever = FakeRetriever([])
    result = answer("any question", "test_policy", retriever, cfg)
    assert result["answerable"] is False
    assert result["citations"] == []


# ---------------------------------------------------------------------------
# Abstention: model says unanswerable
# ---------------------------------------------------------------------------

def test_abstain_model_says_unanswerable():
    cfg = Config(score_floor=0.30)
    hits = [_make_chunk("p::sec::c000", "We collect email addresses.", score=0.80)]
    retriever = FakeRetriever(hits)
    with _mock_anthropic("UNANSWERABLE"):
        result = answer("Does the policy cover biometric data?", "test_policy", retriever, cfg)
    assert result["answerable"] is False
    assert result["citations"] == []


# ---------------------------------------------------------------------------
# Answerable path
# ---------------------------------------------------------------------------

def test_answerable_has_citations():
    cfg = Config(score_floor=0.30)
    hits = [
        _make_chunk("p::data_sharing::c000", "We share data with advertising partners.",
                    score=0.85),
        _make_chunk("p::user_choice::c000", "You can opt out in account settings.", score=0.72,
                    section="User Choice/Control"),
    ]
    retriever = FakeRetriever(hits)
    with _mock_anthropic(
        "According to the policy, data is shared with advertising partners [1]. "
        "You can opt out [2]."
    ):
        result = answer("Does this app share data?", "test_policy", retriever, cfg)
    assert result["answerable"] is True
    assert len(result["citations"]) >= 1
    for c in result["citations"]:
        assert "chunk_id" in c
        assert "section" in c
        assert "quote" in c


def test_citations_reference_real_chunk_ids():
    cfg = Config(score_floor=0.30)
    hits = [
        _make_chunk("policy_abc::sharing::c000", "Data may be shared with partners.", score=0.80),
    ]
    retriever = FakeRetriever(hits)
    with _mock_anthropic("The policy shares data with partners [1]."):
        result = answer("Who gets my data?", "test_policy", retriever, cfg)
    chunk_ids_in_hits = {h["chunk"]["chunk_id"] for h in hits}
    for c in result["citations"]:
        assert c["chunk_id"] in chunk_ids_in_hits


def test_quote_max_25_words():
    cfg = Config(score_floor=0.30)
    long_text = "We collect " + " ".join([f"word{i}" for i in range(50)]) + "."
    hits = [_make_chunk("p::sec::c000", long_text, score=0.80)]
    retriever = FakeRetriever(hits)
    with _mock_anthropic("The policy collects data [1]."):
        result = answer("data collection", "test_policy", retriever, cfg)
    for c in result["citations"]:
        assert len(c["quote"].split()) <= 26  # 25 + possible "…"


def test_answer_has_model_field():
    cfg = Config(score_floor=0.30, gen_model="claude-haiku-4-5")
    hits = [_make_chunk("p::sec::c000", "We encrypt all data.", score=0.80)]
    retriever = FakeRetriever(hits)
    with _mock_anthropic("Data is encrypted [1]."):
        result = answer("Is my data secure?", "test_policy", retriever, cfg)
    assert result["model"] == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_short_quote_truncates():
    long_text = " ".join([f"word{i}" for i in range(50)])
    quote = _short_quote(long_text, "query")
    assert len(quote.split()) <= 26


def test_canned_answer_schema():
    a = canned_answer()
    assert a["answerable"] is True
    assert a["citations"]
    for c in a["citations"]:
        assert all(k in c for k in ("chunk_id", "section", "quote"))


# ---------------------------------------------------------------------------
# Input validation (defense-in-depth before any embed/LLM call; handler → 400)
# ---------------------------------------------------------------------------

class _ExplodingRetriever:
    """Fails if retrieve() is ever called — proves validation short-circuits first."""

    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]:
        raise AssertionError("retrieve() must not run when inputs are invalid")


def _assert_rejected_before_retrieval(query: object, policy_id: object) -> None:
    import pytest

    cfg = Config(score_floor=0.30)
    with pytest.raises(ValueError):
        answer(query, policy_id, _ExplodingRetriever(), cfg)  # type: ignore[arg-type]


def test_rejects_empty_query():
    _assert_rejected_before_retrieval("", "test_policy")


def test_rejects_whitespace_only_query():
    _assert_rejected_before_retrieval("   \n\t ", "test_policy")


def test_rejects_oversized_query():
    _assert_rejected_before_retrieval("x" * (MAX_QUERY_CHARS + 1), "test_policy")


def test_accepts_query_at_max_length():
    # Exactly MAX_QUERY_CHARS is valid → it must reach retrieval (which abstains here).
    cfg = Config(score_floor=0.30)
    retriever = FakeRetriever([])
    result = answer("x" * MAX_QUERY_CHARS, "test_policy", retriever, cfg)
    assert result["answerable"] is False


def test_rejects_empty_policy_id():
    _assert_rejected_before_retrieval("a real question", "")


def test_rejects_non_string_query():
    _assert_rejected_before_retrieval(12345, "test_policy")


def test_rejects_non_string_policy_id():
    _assert_rejected_before_retrieval("a real question", None)


# ---------------------------------------------------------------------------
# Citation-integrity fixes (red-team finding #2 and #3)
# ---------------------------------------------------------------------------


def test_no_valid_marker_abstains() -> None:
    """Finding #2: model answers with no [N] markers → abstain, not fabricate."""
    cfg = Config(score_floor=0.30)
    hits = [_make_chunk("p::sec::c000", "We collect email addresses.", score=0.80)]
    retriever = FakeRetriever(hits)
    with _mock_anthropic("The policy collects email addresses."):
        result = answer("What data is collected?", "test_policy", retriever, cfg)
    assert result["answerable"] is False
    assert result["citations"] == []
    assert result["text"] == ABSTENTION_TEXT


def test_out_of_range_marker_abstains() -> None:
    """Finding #2: [99] is out of range for a 1-hit context → abstain."""
    cfg = Config(score_floor=0.30)
    hits = [_make_chunk("p::sec::c000", "We collect email addresses.", score=0.80)]
    retriever = FakeRetriever(hits)
    with _mock_anthropic("The policy collects email addresses [99]."):
        result = answer("What data is collected?", "test_policy", retriever, cfg)
    assert result["answerable"] is False
    assert result["citations"] == []


def test_marker_zero_abstains() -> None:
    """Finding #2: [0] is not a valid 1-indexed reference → abstain."""
    cfg = Config(score_floor=0.30)
    hits = [_make_chunk("p::sec::c000", "We collect email addresses.", score=0.80)]
    retriever = FakeRetriever(hits)
    with _mock_anthropic("The policy collects email addresses [0]."):
        result = answer("What data is collected?", "test_policy", retriever, cfg)
    assert result["answerable"] is False
    assert result["citations"] == []


def test_unanswerable_prefix_not_sentinel() -> None:
    """Finding #3: 'Unanswerable? No — ...' with a valid [1] must NOT abstain."""
    cfg = Config(score_floor=0.30)
    hits = [_make_chunk("p::sec::c000", "We collect email addresses.", score=0.80)]
    retriever = FakeRetriever(hits)
    with _mock_anthropic("Unanswerable? No — the policy does collect emails [1]."):
        result = answer("What data is collected?", "test_policy", retriever, cfg)
    assert result["answerable"] is True
    assert len(result["citations"]) >= 1


def test_unanswerable_exact_still_abstains() -> None:
    """Finding #3: exact 'UNANSWERABLE' sentinel still triggers abstention."""
    cfg = Config(score_floor=0.30)
    hits = [_make_chunk("p::sec::c000", "We collect email addresses.", score=0.80)]
    retriever = FakeRetriever(hits)
    with _mock_anthropic("UNANSWERABLE"):
        result = answer("Does the policy cover biometrics?", "test_policy", retriever, cfg)
    assert result["answerable"] is False
    assert result["citations"] == []
