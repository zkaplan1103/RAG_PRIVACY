"""Generation layer: answer() with citations and abstention.

Filled in by rag-engineer (Phase 1). This stub defines the Answer schema and
the answer() signature so ui-engineer can code against it now.

Answer schema — see docs/CONTRACTS.md §3.
"""
from __future__ import annotations

from typing import TypedDict

from .config import Config
from .retrieve import Retriever


class Citation(TypedDict):
    chunk_id: str
    section: str
    quote: str   # short supporting snippet (<= 25 words) for UI display


class Answer(TypedDict):
    answerable: bool          # False => abstain
    text: str                 # plain-English answer or the abstention message
    citations: list[Citation] # empty iff answerable is False
    policy_id: str
    model: str                # which LLM produced this (for eval/reporting)


ABSTENTION_TEXT = "The policy doesn't address this question."


def answer(
    query: str,
    policy_id: str,
    retriever: Retriever,
    cfg: Config,
) -> Answer:
    """Retrieve relevant clauses and generate a cited answer.

    Abstains (answerable=False) when no chunk scores above cfg.score_floor.
    Filled in by rag-engineer (Phase 1).
    """
    raise NotImplementedError("rag-engineer fills this in during Phase 1")


def canned_answer(policy_id: str = "fixture_policy") -> Answer:
    """Return a hardcoded Answer for UI development / smoke tests before Phase 1."""
    return Answer(
        answerable=True,
        text=(
            "According to the policy, the service may share data with advertising "
            "partners to deliver targeted ads. Users can opt out via account settings."
        ),
        citations=[
            Citation(
                chunk_id=f"{policy_id}::sec2::c01",
                section="Data Sharing",
                quote="may share data with advertising partners to deliver targeted ads",
            )
        ],
        policy_id=policy_id,
        model="canned-stub",
    )
