"""PolicyLens — Streamlit demo.

Ask plain-English questions about privacy policies. Every answer cites the exact clause.

Run: uv run streamlit run app.py
"""
import os

import streamlit as st

from src.policylens.config import DEFAULT_CONFIG
from src.policylens.generate import Answer, answer
from src.policylens.retrieve import make_retriever

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="PolicyLens",
    page_icon="🔍",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Cached retriever — loaded once per Streamlit process
# ---------------------------------------------------------------------------
@st.cache_resource
def get_retriever():
    # make_retriever respects DEFAULT_CONFIG.retrieval_backend ("chroma" by default).
    # Switch to "pgvector" by setting retrieval_backend in Config and providing
    # the env var named by Config.db_url_env. The chroma path is byte-identical.
    return make_retriever(DEFAULT_CONFIG)

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
    "105_amazon_com": "Amazon",
    "1017_sci_news_com": "Sci-News.com",
    "1028_redorbit_com": "RedOrbit.com",
    "1034_aol_com": "AOL",
    "1050_honda_com": "Honda",
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
# API key check
# ---------------------------------------------------------------------------
if not os.environ.get("ANTHROPIC_API_KEY"):
    st.error(
        "**ANTHROPIC_API_KEY not set.** "
        "Export it in your shell before running: `export ANTHROPIC_API_KEY=sk-...`"
    )
    st.stop()

# Check that the retriever can be constructed (index built / DB reachable).
try:
    retriever = get_retriever()
    if DEFAULT_CONFIG.retrieval_backend == "chroma":
        # For Chroma: confirm the collection has been indexed.
        import chromadb  # type: ignore[import-untyped]

        _client = chromadb.PersistentClient(path=DEFAULT_CONFIG.index_dir)
        _col = _client.get_collection("policylens")
        if _col.count() == 0:
            st.error(
                "**Index not built yet.** "
                "Run `make index` (or `uv run python -m policylens.index build`) first."
            )
            st.stop()
except Exception as e:
    backend = DEFAULT_CONFIG.retrieval_backend
    if backend == "pgvector":
        st.error(
            f"**Could not connect to pgvector:** {e}  \n"
            f"Ensure {DEFAULT_CONFIG.db_url_env} is set and the DB is reachable."
        )
    else:
        st.error(f"**Could not load index:** {e}  \nRun `make index` to build it.")
    st.stop()

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
        result: Answer = answer(query, policy_id, retriever, DEFAULT_CONFIG)

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
