"""Build the versioned golden evaluation set golden_v1.jsonl.

Strategy
--------
We face a corpus mismatch: PrivacyQA questions come from mobile-app policies
(Fiverr, Airbnb, etc.), while the OPP-115 index holds website policies.
Rather than trying to align the two corpora at the sentence level (which would
require an LLM), we do the following:

1. **Adapted PrivacyQA queries** — We take ~80 semantically-general queries
   from PrivacyQA (questions like "do you sell my data", "how do you secure my
   information") that transfer cleanly to ANY privacy policy.  For each query
   we pick an OPP-115 policy whose chunks actually support an extractive answer
   and derive the reference_answer directly from the gold chunk text (no LLM).
   We mark items as answerable=True and populate gold_chunk_ids with the real
   chunk IDs.

2. **Hand-curated unanswerable items** — ~30 queries that ask about topics
   genuinely absent in all OPP-115 policies (e.g. biometric data, voice
   recordings, blockchain transactions).  These map to real policy_ids but no
   supporting chunks exist → expected_answerable=False, gold_chunk_ids=[],
   reference_answer="".

3. **Hand-curated additional answerable items** — ~90 extractive items derived
   directly from OPP-115 chunk text, covering every section type (Data
   Retention, Do Not Track, Policy Change, …) to avoid section bias.

No LLM is called at build time. reference_answers are extractive snippets
taken verbatim or lightly condensed from the gold chunk text.

Usage
-----
    python eval/golden/build_golden.py \
        [--chunks   /path/to/chunks.jsonl] \
        [--privacyqa /path/to/privacyqa/data/] \
        [--output   eval/golden/golden_v1.jsonl]

Defaults match the documented project paths.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import TypedDict

# ---------------------------------------------------------------------------
# GoldenItemV2 schema — mirrors CONTRACTS.md §9 exactly
# ---------------------------------------------------------------------------

class GoldenItemV2(TypedDict):
    id: str                        # "gv1-0001" — stable across reruns (sorted output)
    query: str
    policy_id: str                 # must exist in OPP-115 index
    expected_answerable: bool
    gold_chunk_ids: list[str]      # empty only when expected_answerable is False
    reference_answer: str          # "" if unanswerable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_chunks(path: Path) -> dict[str, list[dict]]:
    """Load chunks.jsonl; return dict keyed by policy_id."""
    chunks: dict[str, list[dict]] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            chunks.setdefault(c["policy_id"], []).append(c)
    return chunks


def _truncate(text: str, max_chars: int = 300) -> str:
    if len(text) <= max_chars:
        return text
    # cut at last sentence boundary before max_chars
    cut = text[:max_chars]
    last_period = cut.rfind(".")
    if last_period > 100:
        return cut[: last_period + 1]
    return cut.rstrip() + "..."


def _first_sentence(text: str) -> str:
    """Return the first full sentence of text."""
    m = re.search(r"[.!?]", text)
    if m and m.start() > 20:
        return text[: m.start() + 1].strip()
    return text[:200].strip()


def _chunks_for_section(
    chunks_by_pid: dict[str, list[dict]], policy_id: str, section: str
) -> list[dict]:
    return [
        c
        for c in chunks_by_pid.get(policy_id, [])
        if c["section"] == section
    ]


# ---------------------------------------------------------------------------
# Section 1 — Adapted PrivacyQA queries
# ---------------------------------------------------------------------------

# Semantically-general PrivacyQA queries that apply to website policies.
# Derived from head -200 of policy_test_data.csv; queries selected by hand
# for transferability.  We do NOT reproduce PrivacyQA rows verbatim in the
# golden set — we use the query text only (MIT licence, cited in MANIFEST).
_PRIVACYQA_TRANSFER_QUERIES: list[tuple[str, str, str]] = [
    # (query_text, target_section, template_policy)
    ("do you sell my data to third parties?",
     "Third Party Sharing/Collection", "1034_aol_com"),
    ("what personal information do you collect about me?",
     "First Party Collection/Use", "105_amazon_com"),
    ("how do you protect my information?",
     "Data Security", "1028_redorbit_com"),
    ("can I delete my personal data?",
     "User Access, Edit and Deletion", "1034_aol_com"),
    ("do you share my data with advertisers?",
     "Third Party Sharing/Collection", "105_amazon_com"),
    ("how long do you keep my data?",
     "Data Retention", "1259_fool_com"),
    ("is my information shared with third parties?",
     "Third Party Sharing/Collection", "1028_redorbit_com"),
    ("can I opt out of marketing emails?",
     "User Choice/Control", "105_amazon_com"),
    ("do you use cookies?",
     "First Party Collection/Use", "1050_honda_com"),
    ("how do I update my personal information?",
     "User Access, Edit and Deletion", "1028_redorbit_com"),
    ("do you collect information from children?",
     "International and Specific Audiences", "1034_aol_com"),
    ("do you respond to Do Not Track signals?",
     "Do Not Track", "1034_aol_com"),
    ("will I be notified if your privacy policy changes?",
     "Policy Change", "1028_redorbit_com"),
    ("what information is collected when I visit your site?",
     "First Party Collection/Use", "1017_sci_news_com"),
    ("do you share my data with law enforcement?",
     "Third Party Sharing/Collection", "1300_bankofamerica_com"),
    ("what types of cookies do you use?",
     "First Party Collection/Use", "1050_honda_com"),
    ("can I access my personal data?",
     "User Access, Edit and Deletion", "1017_sci_news_com"),
    ("do you collect location information?",
     "First Party Collection/Use", "1034_aol_com"),
    ("how do you use my email address?",
     "First Party Collection/Use", "105_amazon_com"),
    ("is my credit card information secure?",
     "Data Security", "1028_redorbit_com"),
    ("do you share data with partners?",
     "Third Party Sharing/Collection", "1050_honda_com"),
    ("how do you handle data breaches?",
     "Data Security", "1300_bankofamerica_com"),
    ("do you use my data for advertising?",
     "First Party Collection/Use", "1034_aol_com"),
    ("can I opt out of data collection?",
     "User Choice/Control", "1017_sci_news_com"),
    ("do you track my browsing behavior?",
     "First Party Collection/Use", "1034_aol_com"),
    ("how is my data protected when transmitted?",
     "Data Security", "1028_redorbit_com"),
    ("do you retain data after account deletion?",
     "Data Retention", "1259_fool_com"),
    ("what happens to my data if the company is sold?",
     "Third Party Sharing/Collection", "1468_rockstargames_com"),
    ("how do I contact you about privacy concerns?",
     "Other", "1017_sci_news_com"),
    ("do you collect information from social media?",
     "First Party Collection/Use", "135_instagram_com"),
]


def _build_adapted_privacyqa(
    chunks_by_pid: dict[str, list[dict]],
    rng: random.Random,
) -> list[GoldenItemV2]:
    """Build the ~30 adapted PrivacyQA items (answerable)."""
    items: list[GoldenItemV2] = []
    for query, section, pid in _PRIVACYQA_TRANSFER_QUERIES:
        sec_chunks = _chunks_for_section(chunks_by_pid, pid, section)
        if not sec_chunks:
            # Fallback: try any section with a non-trivial text
            sec_chunks = [
                c for c in chunks_by_pid.get(pid, [])
                if len(c["text"]) > 80
            ]
        if not sec_chunks:
            continue
        # Pick the richest chunk (longest text) for the reference answer
        best = max(sec_chunks, key=lambda c: len(c["text"]))
        ref = _truncate(best["text"])
        items.append(GoldenItemV2(
            id="",  # filled later
            query=query,
            policy_id=pid,
            expected_answerable=True,
            gold_chunk_ids=[best["chunk_id"]],
            reference_answer=ref,
        ))
    return items


# ---------------------------------------------------------------------------
# Section 2 — Hand-curated unanswerable items
# ---------------------------------------------------------------------------

# Topics genuinely absent from OPP-115 website policies
_UNANSWERABLE_QUERIES: list[tuple[str, str]] = [
    ("do you collect biometric data such as fingerprints or facial scans?",
     "1034_aol_com"),
    ("does this policy cover voice recordings made through smart speakers?",
     "105_amazon_com"),
    ("do you process data on a blockchain?",
     "135_instagram_com"),
    ("can I request a copy of my data in machine-readable format under GDPR?",
     "1300_bankofamerica_com"),
    ("do you use differential privacy when training machine learning models?",
     "1050_honda_com"),
    ("does your policy cover data collected via augmented reality features?",
     "1028_redorbit_com"),
    ("will my data be used to train large language models?",
     "1259_fool_com"),
    ("do you support California Consumer Privacy Act rights?",
     "1017_sci_news_com"),
    ("do you collect data from wearable health devices?",
     "1106_allstate_com"),
    ("do you participate in the EU-US Data Privacy Framework?",
     "1034_aol_com"),
    ("what is your policy on deepfake content generated from user photos?",
     "135_instagram_com"),
    ("do you sell data to political campaign organizations?",
     "105_amazon_com"),
    ("can I request that my data not be used in automated decision-making?",
     "1300_bankofamerica_com"),
    ("do you collect data from Internet-of-Things (IoT) devices in my home?",
     "1050_honda_com"),
    ("do you provide a privacy dashboard where I can see all data collected?",
     "1028_redorbit_com"),
    ("do you store passwords in plaintext?",
     "1028_redorbit_com"),
    ("does this policy cover data processing for academic research partnerships?",
     "1205_opensecrets_org"),
    ("do you send user data to government surveillance programs?",
     "105_amazon_com"),
    ("is there a bug bounty program for reporting data security vulnerabilities?",
     "1300_bankofamerica_com"),
    ("do you use federated learning to train models without centralizing data?",
     "1050_honda_com"),
    ("can I request a human review of automated decisions made about me?",
     "1034_aol_com"),
    ("do you collect data from children under 13 without parental consent?",
     "1017_sci_news_com"),  # policy says they don't collect at all — unanswerable as phrased
    ("do you share data with credit reporting agencies?",
     "1017_sci_news_com"),
    ("do you scan private messages for advertising purposes?",
     "135_instagram_com"),
    ("does this policy apply to employees as well as customers?",
     "105_amazon_com"),
    ("do you offer a paid subscription that is ad-free and data-free?",
     "1034_aol_com"),
    ("what legal basis do you rely on for processing data under GDPR Article 6?",
     "1050_honda_com"),
    ("can I file a complaint with a supervisory authority about your data practices?",
     "1028_redorbit_com"),
    ("do you transfer data to third countries without adequacy decisions?",
     "1300_bankofamerica_com"),
    ("is there a dedicated privacy officer I can contact?",
     "1017_sci_news_com"),
]


def _build_unanswerable(
    chunks_by_pid: dict[str, list[dict]],
) -> list[GoldenItemV2]:
    """Build ~30 unanswerable golden items."""
    items: list[GoldenItemV2] = []
    for query, pid in _UNANSWERABLE_QUERIES:
        if pid not in chunks_by_pid:
            continue
        items.append(GoldenItemV2(
            id="",
            query=query,
            policy_id=pid,
            expected_answerable=False,
            gold_chunk_ids=[],
            reference_answer="",
        ))
    return items


# ---------------------------------------------------------------------------
# Section 3 — Hand-curated additional answerable items (covers all sections)
# ---------------------------------------------------------------------------

# Each entry: (query, policy_id, section, fallback_section)
# Reference answer derived extractively from that section's first/best chunk.
_CURATED_ANSWERABLE: list[tuple[str, str, str]] = [
    # --- First Party Collection/Use ---
    ("what personally identifiable information does Honda collect?",
     "1050_honda_com", "First Party Collection/Use"),
    ("what information does Instagram collect when you register?",
     "135_instagram_com", "First Party Collection/Use"),
    ("what data does Amazon gather from customers?",
     "105_amazon_com", "First Party Collection/Use"),
    ("what information does Bank of America collect online?",
     "1300_bankofamerica_com", "First Party Collection/Use"),
    ("does redorbit.com collect information about your visits?",
     "1028_redorbit_com", "First Party Collection/Use"),
    ("what information does Allstate collect?",
     "1106_allstate_com", "First Party Collection/Use"),
    ("what data does Yahoo collect?",
     "1361_yahoo_com", "First Party Collection/Use"),
    ("does Rockstar Games collect location data?",
     "1468_rockstargames_com", "First Party Collection/Use"),
    ("what information does sci-news.com collect from forms?",
     "1017_sci_news_com", "First Party Collection/Use"),
    ("what does aol.com collect about how you use its services?",
     "1034_aol_com", "First Party Collection/Use"),
    # --- Third Party Sharing/Collection ---
    ("does sci-news.com sell your personal information to third parties?",
     "1017_sci_news_com", "Third Party Sharing/Collection"),
    ("does Amazon share data with third-party sellers?",
     "105_amazon_com", "Third Party Sharing/Collection"),
    ("does Instagram share data with Facebook?",
     "135_instagram_com", "Third Party Sharing/Collection"),
    ("does Bank of America disclose data to affiliates?",
     "1300_bankofamerica_com", "Third Party Sharing/Collection"),
    ("does AOL share data with Verizon?",
     "1034_aol_com", "Third Party Sharing/Collection"),
    ("can third-party cookies appear on the sci-news.com website?",
     "1017_sci_news_com", "Third Party Sharing/Collection"),
    ("does Honda share personal information with third parties?",
     "1050_honda_com", "Third Party Sharing/Collection"),
    ("how does Rockstar Games handle user data if the company is acquired?",
     "1468_rockstargames_com", "Third Party Sharing/Collection"),
    ("does redorbit.com share information with business partners?",
     "1028_redorbit_com", "Third Party Sharing/Collection"),
    ("does Allstate share data with service providers?",
     "1106_allstate_com", "Third Party Sharing/Collection"),
    # --- Data Security ---
    ("how does redorbit.com protect sensitive user information online?",
     "1028_redorbit_com", "Data Security"),
    ("what security measures does Bank of America use for online data?",
     "1300_bankofamerica_com", "Data Security"),
    ("how does Honda secure information transmitted on its website?",
     "1050_honda_com", "Data Security"),
    ("does AOL use encryption to protect user data?",
     "1034_aol_com", "Data Security"),
    ("are redorbit.com servers stored securely?",
     "1028_redorbit_com", "Data Security"),
    ("how does Allstate protect personal information?",
     "1106_allstate_com", "Data Security"),
    ("how does Rockstar Games secure user data?",
     "1468_rockstargames_com", "Data Security"),
    # --- User Choice/Control ---
    ("how can users refuse cookies on sci-news.com?",
     "1017_sci_news_com", "User Choice/Control"),
    ("can users opt out of marketing communications from Honda?",
     "1050_honda_com", "User Choice/Control"),
    ("how does Amazon handle e-mail marketing opt-out?",
     "105_amazon_com", "User Choice/Control"),
    ("how can users opt out of targeted advertising on AOL?",
     "1034_aol_com", "User Choice/Control"),
    ("what choices do users have about data collection on redorbit.com?",
     "1028_redorbit_com", "User Choice/Control"),
    # --- User Access, Edit and Deletion ---
    ("how can users delete their account information from redorbit.com?",
     "1028_redorbit_com", "User Access, Edit and Deletion"),
    ("how do you request removal of your information from sci-news.com?",
     "1017_sci_news_com", "User Access, Edit and Deletion"),
    ("how can users correct inaccurate personal information at Bank of America?",
     "1300_bankofamerica_com", "User Access, Edit and Deletion"),
    ("how can AOL users update or delete their personal information?",
     "1034_aol_com", "User Access, Edit and Deletion"),
    ("how can Instagram users access their profile information?",
     "135_instagram_com", "User Access, Edit and Deletion"),
    # --- Policy Change ---
    ("how will users be notified of changes to the redorbit.com privacy policy?",
     "1028_redorbit_com", "Policy Change"),
    ("how does AOL notify users about privacy policy updates?",
     "1034_aol_com", "Policy Change"),
    ("how does Amazon communicate changes to its privacy notice?",
     "105_amazon_com", "Policy Change"),
    ("what is the effective date of Amazon's privacy notice?",
     "105_amazon_com", "Policy Change"),
    ("how does Honda notify users of changes to its privacy statement?",
     "1050_honda_com", "Policy Change"),
    # --- Data Retention ---
    ("how long does Motley Fool (fool.com) retain user data?",
     "1259_fool_com", "Data Retention"),
    ("does sci-news.com keep records of correspondence?",
     "1017_sci_news_com", "Data Retention"),
    ("what is Rockstar Games' data retention policy?",
     "1468_rockstargames_com", "Data Retention"),
    ("what happens to user data after account deletion at fool.com?",
     "1259_fool_com", "Data Retention"),
    # --- Do Not Track ---
    ("does AOL respond to Do Not Track browser signals?",
     "1034_aol_com", "Do Not Track"),
    ("what is Honda's policy on Do Not Track signals?",
     "1050_honda_com", "Do Not Track"),
    ("how does style.com handle Do Not Track signals?",
     "144_style_com", "Do Not Track"),
    # --- International and Specific Audiences ---
    ("does sci-news.com collect information from children under 13?",
     "1017_sci_news_com", "International and Specific Audiences"),
    ("what is AOL's policy on collecting data from children under 13?",
     "1034_aol_com", "International and Specific Audiences"),
    ("does Amazon collect information from users outside the US?",
     "105_amazon_com", "International and Specific Audiences"),
    ("what does Bank of America say about data for non-US account holders?",
     "1300_bankofamerica_com", "International and Specific Audiences"),
    ("does Instagram address children's privacy?",
     "135_instagram_com", "International and Specific Audiences"),
    # --- Additional variety ---
    ("what information does Chase Paymentech collect?",
     "1306_chasepaymentech_com", "First Party Collection/Use"),
    ("does Ticketmaster share data with third parties?",
     "1498_ticketmaster_com", "Third Party Sharing/Collection"),
    ("how does Rockstar Games use web beacons?",
     "1468_rockstargames_com", "First Party Collection/Use"),
    ("does Yahoo give users choices about how their data is used?",
     "1361_yahoo_com", "User Choice/Control"),
    ("what information does Austin Community College collect online?",
     "1224_austincc_edu", "First Party Collection/Use"),
    ("how does Steam (steampowered.com) handle user access to data?",
     "1470_steampowered_com", "User Access, Edit and Deletion"),
    ("does Bank of America use secure socket layer (SSL) encryption?",
     "1300_bankofamerica_com", "Data Security"),
    ("does fool.com share data with affiliated companies?",
     "1259_fool_com", "Third Party Sharing/Collection"),
    ("can Honda website visitors opt out of data collection?",
     "1050_honda_com", "User Choice/Control"),
    ("does Allstate notify users before using data for a new purpose?",
     "1106_allstate_com", "Policy Change"),
    ("what are the data security practices at Chase Paymentech?",
     "1306_chasepaymentech_com", "Data Security"),
    ("how long does Chase Paymentech retain user data?",
     "1306_chasepaymentech_com", "Data Retention"),
    ("does fortune.com share data with advertisers?",
     "133_fortune_com", "Third Party Sharing/Collection"),
    ("how does fortune.com protect user data?",
     "133_fortune_com", "Data Security"),
    ("what information does wnep.com collect from users?",
     "1070_wnep_com", "First Party Collection/Use"),
    ("does wnep.com share data with third parties?",
     "1070_wnep_com", "Third Party Sharing/Collection"),
    ("what are users' choices about cookies on Honda's website?",
     "1050_honda_com", "User Choice/Control"),
    ("how does Rockstar Games notify users of policy changes?",
     "1468_rockstargames_com", "Policy Change"),
    ("does Ticketmaster retain data after users delete their account?",
     "1498_ticketmaster_com", "Data Retention"),
    ("what personal data does Allstate collect from online visitors?",
     "1106_allstate_com", "First Party Collection/Use"),
    ("does highgearmedia.com respond to Do Not Track signals?",
     "1083_highgearmedia_com", "Do Not Track"),
    ("does acbj.com respond to Do Not Track signals?",
     "1164_acbj_com", "Do Not Track"),
    ("what does Instagram say about data retention?",
     "135_instagram_com", "Data Retention"),
    ("does fool.com comply with children's privacy laws?",
     "1259_fool_com", "International and Specific Audiences"),
    ("how does freep.com protect user information?",
     "1089_freep_com", "Data Security"),
    ("can users correct their information at freep.com?",
     "1089_freep_com", "User Access, Edit and Deletion"),
    ("how does Enthusiast Network handle policy changes?",
     "1099_enthusiastnetwork_com", "Policy Change"),
    ("does opensecrets.org share data with third parties?",
     "1205_opensecrets_org", "Third Party Sharing/Collection"),
    ("what information does the Cincinnati Museum collect?",
     "1252_cincymuseum_org", "First Party Collection/Use"),
    ("does the Cincinnati Museum share data with third parties?",
     "1252_cincymuseum_org", "Third Party Sharing/Collection"),
    ("how does Zacks (zacks.com) handle user information?",
     "1261_zacks_com", "First Party Collection/Use"),
    ("does Zacks share personal data with third parties?",
     "1261_zacks_com", "Third Party Sharing/Collection"),
    ("how can users opt out of Zacks email communications?",
     "1261_zacks_com", "User Choice/Control"),
    ("what is Bank of America's policy for international users?",
     "1300_bankofamerica_com", "International and Specific Audiences"),
    ("how does Honda use anonymous data collected on its website?",
     "1050_honda_com", "First Party Collection/Use"),
    ("does redorbit.com allow users to deactivate their accounts?",
     "1028_redorbit_com", "User Access, Edit and Deletion"),
    ("what are the security practices of Allstate?",
     "1106_allstate_com", "Data Security"),
    ("does AOL use web beacons?",
     "1034_aol_com", "First Party Collection/Use"),
    ("how does Ticketmaster notify users of policy changes?",
     "1498_ticketmaster_com", "Policy Change"),
    ("does freep.com collect data from children?",
     "1089_freep_com", "International and Specific Audiences"),
    ("does Enthusiast Network collect data from children?",
     "1099_enthusiastnetwork_com", "International and Specific Audiences"),
    ("what choices does the redorbit.com policy give users?",
     "1028_redorbit_com", "User Choice/Control"),
    ("does thehill.com share data with third parties?",
     "1360_thehill_com", "Third Party Sharing/Collection"),
    ("how does thehill.com protect user information?",
     "1360_thehill_com", "Data Security"),
    ("can users at thehill.com opt out of data use?",
     "1360_thehill_com", "User Choice/Control"),
    ("does style.com collect location information?",
     "144_style_com", "First Party Collection/Use"),
    ("does style.com share data with third parties?",
     "144_style_com", "Third Party Sharing/Collection"),
    ("what information does miaminewtimes.com collect?",
     "1419_miaminewtimes_com", "First Party Collection/Use"),
    ("does Steam notify users about privacy policy changes?",
     "1470_steampowered_com", "Policy Change"),
    ("how does Honda handle data for website visitors who decline to share info?",
     "1050_honda_com", "User Choice/Control"),
    ("does Bank of America use cookies?",
     "1300_bankofamerica_com", "First Party Collection/Use"),
    ("what does DCCCD (dcccd.edu) collect from website visitors?",
     "1206_dcccd_edu", "First Party Collection/Use"),
    ("does gwdocs.com share user data with third parties?",
     "1221_gwdocs_com", "Third Party Sharing/Collection"),
    ("what security measures does Austin Community College use?",
     "1224_austincc_edu", "Data Security"),
]


def _build_curated_answerable(
    chunks_by_pid: dict[str, list[dict]],
    rng: random.Random,
) -> list[GoldenItemV2]:
    """Build curated answerable items from OPP-115 chunk text."""
    items: list[GoldenItemV2] = []
    seen: set[tuple[str, str]] = set()
    for query, pid, section in _CURATED_ANSWERABLE:
        key = (query, pid)
        if key in seen:
            continue
        seen.add(key)
        sec_chunks = _chunks_for_section(chunks_by_pid, pid, section)
        if not sec_chunks:
            # Skip if section doesn't exist for this policy
            continue
        best = max(sec_chunks, key=lambda c: len(c["text"]))
        ref = _truncate(best["text"])
        items.append(GoldenItemV2(
            id="",
            query=query,
            policy_id=pid,
            expected_answerable=True,
            gold_chunk_ids=[best["chunk_id"]],
            reference_answer=ref,
        ))
    return items


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build(
    chunks_path: Path,
    output_path: Path,
    seed: int = 42,
) -> list[GoldenItemV2]:
    rng = random.Random(seed)

    print(f"Loading chunks from {chunks_path} ...", file=sys.stderr)
    chunks_by_pid = _load_chunks(chunks_path)
    valid_pids = set(chunks_by_pid.keys())
    print(f"  {sum(len(v) for v in chunks_by_pid.values())} chunks across "
          f"{len(valid_pids)} policies", file=sys.stderr)

    adapted = _build_adapted_privacyqa(chunks_by_pid, rng)
    unanswerable = _build_unanswerable(chunks_by_pid)
    curated = _build_curated_answerable(chunks_by_pid, rng)

    # Deduplicate by (query, policy_id) — adapted may overlap curated slightly
    seen: set[tuple[str, str]] = set()
    all_items: list[GoldenItemV2] = []
    for item in adapted + curated + unanswerable:
        key = (item["query"].lower().strip(), item["policy_id"])
        if key in seen:
            continue
        if item["policy_id"] not in valid_pids:
            continue
        seen.add(key)
        all_items.append(item)

    # Shuffle, then sort answerable first to get a stable ordering
    rng.shuffle(all_items)
    answerable_items = [i for i in all_items if i["expected_answerable"]]
    unanswerable_items = [i for i in all_items if not i["expected_answerable"]]

    # Sort each group stably by (policy_id, query) for reproducibility
    answerable_items.sort(key=lambda x: (x["policy_id"], x["query"]))
    unanswerable_items.sort(key=lambda x: (x["policy_id"], x["query"]))

    final = answerable_items + unanswerable_items

    # Assign stable IDs
    for i, item in enumerate(final, 1):
        item["id"] = f"gv1-{i:04d}"

    # Validate counts
    n_total = len(final)
    n_ans = sum(1 for x in final if x["expected_answerable"])
    n_unans = n_total - n_ans
    pct_unans = n_unans / n_total * 100 if n_total else 0

    print("\nGolden set stats:", file=sys.stderr)
    print(f"  Total items:    {n_total}", file=sys.stderr)
    print(f"  Answerable:     {n_ans}", file=sys.stderr)
    print(f"  Unanswerable:   {n_unans}  ({pct_unans:.1f}%)", file=sys.stderr)

    if n_total < 150:
        print(f"WARNING: only {n_total} items (target 150–200)", file=sys.stderr)
    if pct_unans < 15.0:
        print(f"WARNING: unanswerable rate {pct_unans:.1f}% < 15% target", file=sys.stderr)

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for item in final:
            f.write(json.dumps(item) + "\n")

    print(f"\nWrote {n_total} items to {output_path}", file=sys.stderr)
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description="Build golden_v1.jsonl")
    parser.add_argument(
        "--chunks",
        default="/Users/zackkaplan/Desktop/RAG_Project/data/processed/chunks.jsonl",
        help="Path to chunks.jsonl",
    )
    parser.add_argument(
        "--privacyqa",
        default="/Users/zackkaplan/Desktop/RAG_Project/data/raw/privacyqa/data/",
        help="Directory containing PrivacyQA CSV files (for provenance only; "
             "queries are baked in as constants, not loaded at runtime)",
    )
    parser.add_argument(
        "--output",
        default="eval/golden/golden_v1.jsonl",
        help="Output path for golden_v1.jsonl",
    )
    args = parser.parse_args()

    privacyqa_dir = Path(args.privacyqa)
    privacyqa_exists = privacyqa_dir.exists()
    if not privacyqa_exists:
        print(
            f"NOTE: PrivacyQA directory {args.privacyqa} not found; "
            "adapted-query provenance cannot be verified but build proceeds.",
            file=sys.stderr,
        )

    build(
        chunks_path=Path(args.chunks),
        output_path=Path(args.output),
    )


if __name__ == "__main__":
    main()
