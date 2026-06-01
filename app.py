"""Streamlit demo — PolicyLens.

Wired up by ui-engineer (Phase 1). Runs against canned_answer() until Phase 2
integration swaps in the real answer() call.

Run: uv run streamlit run app.py
"""
import streamlit as st

from src.policylens.generate import Answer, canned_answer
from src.policylens.config import DEFAULT_CONFIG

st.set_page_config(page_title="PolicyLens", layout="centered")
st.title("PolicyLens")
st.caption("Ask plain-English questions about privacy policies. Answers cite the exact clause.")

# --- sidebar config (ui-engineer expands in Phase 1) ---
with st.sidebar:
    st.header("Settings")
    policy_id = st.text_input("Policy ID", value="fixture_policy",
                              help="Identifier for the policy to query")
    st.caption(f"Model: {DEFAULT_CONFIG.gen_model}")

# --- query input ---
query = st.text_input("Your question", placeholder="Does this app share my data with advertisers?")

if st.button("Ask", disabled=not query.strip()):
    with st.spinner("Retrieving and generating…"):
        # Phase 1 stub: swap canned_answer for real answer() after integration
        result: Answer = canned_answer(policy_id=policy_id)

    if result["answerable"]:
        st.success(result["text"])
        st.subheader("Citations")
        for c in result["citations"]:
            with st.expander(f"{c['chunk_id']} — {c['section']}"):
                st.markdown(f"> {c['quote']}")
    else:
        st.warning(result["text"])
        st.caption("No relevant clauses found above the confidence threshold.")
