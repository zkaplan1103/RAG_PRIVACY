"""PolicyLens — Streamlit demo.

Ask plain-English questions about privacy policies. Every answer cites the exact clause.

Run: uv run streamlit run app.py

Phase 1: uses canned_answer() stub until Phase 2 integration wires in the real answer().
"""
import streamlit as st

from src.policylens.generate import Answer, canned_answer
from src.policylens.config import DEFAULT_CONFIG

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="PolicyLens",
    page_icon="🔍",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("PolicyLens")
st.markdown(
    "_Ask plain-English questions about privacy policies. "
    "Every answer cites the exact clause it used._"
)
st.divider()

# ---------------------------------------------------------------------------
# Sidebar — policy selector + settings
# ---------------------------------------------------------------------------
KNOWN_POLICIES = {
    "fixture_policy": "Fixture Policy (stub)",
    "1017_sci_news_com": "Sci-News.com",
    "1028_redorbit_com": "RedOrbit.com",
    "1034_aol_com": "AOL",
    "1050_honda_com": "Honda",
    "105_amazon_com": "Amazon",
}

with st.sidebar:
    st.header("Settings")
    policy_label = st.selectbox(
        "Policy",
        options=list(KNOWN_POLICIES.keys()),
        format_func=lambda k: KNOWN_POLICIES[k],
    )
    policy_id: str = policy_label  # type: ignore[assignment]

    st.caption(f"Model: `{DEFAULT_CONFIG.gen_model}`")
    st.caption(f"Score floor: `{DEFAULT_CONFIG.score_floor}`")
    st.caption(f"Top-k: `{DEFAULT_CONFIG.top_k}`")
    st.divider()
    st.markdown(
        "**Data source:** [OPP-115 corpus](https://usableprivacy.org/data) "
        "(Wilson et al., ACL 2016).  \n"
        "Research/teaching use only."
    )

# ---------------------------------------------------------------------------
# Query input
# ---------------------------------------------------------------------------
EXAMPLE_QUESTIONS = [
    "Does this app share my data with advertisers?",
    "How long is my data retained?",
    "Can I delete my account data?",
    "Does the policy cover biometric data?",
]

with st.expander("Example questions", expanded=False):
    for q in EXAMPLE_QUESTIONS:
        if st.button(q, key=f"ex_{q[:20]}"):
            st.session_state["query"] = q

query = st.text_input(
    "Your question",
    value=st.session_state.get("query", ""),
    placeholder="Does this app share my data with advertisers?",
)

ask_clicked = st.button("Ask", type="primary", disabled=not query.strip())

# ---------------------------------------------------------------------------
# Answer + citations
# ---------------------------------------------------------------------------
if ask_clicked and query.strip():
    with st.spinner("Retrieving and generating…"):
        # Phase 2 integration: replace canned_answer with real answer() call
        result: Answer = canned_answer(policy_id=policy_id)

    st.divider()

    if result["answerable"]:
        st.subheader("Answer")
        st.write(result["text"])

        if result["citations"]:
            st.subheader("Citations")
            for c in result["citations"]:
                with st.expander(f"**{c['section']}** — `{c['chunk_id']}`"):
                    st.markdown(f"> {c['quote']}")

        st.caption(f"Model: `{result['model']}` · Policy: `{result['policy_id']}`")
    else:
        st.warning("**This policy doesn't address that question.**")
        st.markdown(
            "No relevant clauses were found above the confidence threshold. "
            "Try rephrasing, or check a different policy."
        )
        st.caption(f"Policy: `{result['policy_id']}`")
