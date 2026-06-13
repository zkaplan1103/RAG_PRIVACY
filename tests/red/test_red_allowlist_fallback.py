"""RED-TEAM PoC: policy-allowlist fallback fails SAFE, not OPEN.

The allowlist gate is what stops an attacker spending on arbitrary policy_ids.
Confirm degenerate KNOWN_POLICY_IDS values never produce an "allow everything"
state.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "api"))
sys.path.insert(0, str(REPO_ROOT / "src"))


def _build(monkeypatch, value):
    """Reload handler with a given KNOWN_POLICY_IDS env and return the allowlist."""
    if value is None:
        monkeypatch.delenv("KNOWN_POLICY_IDS", raising=False)
    else:
        monkeypatch.setenv("KNOWN_POLICY_IDS", value)
    sys.modules.pop("handler", None)
    import handler  # type: ignore[import-not-found]
    importlib.reload(handler)
    return handler._build_policy_allowlist()


def test_unset_falls_back_to_builtin(monkeypatch):
    ids = _build(monkeypatch, None)
    assert "105_amazon_com" in ids
    # FINDING (Low): docstring/comment + memory claim "115-policy set" but the
    # literal frozenset actually has 117 unique entries (miscount, not a dup).
    # Bounded set, not "*", so spend-safe — but the count claim is wrong.
    assert len(ids) == 117  # documented-as-115, actually 117


def test_builtin_has_no_duplicate_literals(monkeypatch):
    import re
    src = (REPO_ROOT / "api" / "handler.py").read_text()
    block = src.split("opp115_ids = frozenset(")[1].split(")")[0]
    literals = re.findall(r'"([^"]+)"', block)
    from collections import Counter
    dupes = {k: v for k, v in Counter(literals).items() if v > 1}
    assert dupes == {}, f"duplicate literals: {dupes}"
    assert len(literals) == 117  # 117 distinct literals, mislabeled as 115


def test_commas_only_is_empty_not_wildcard(monkeypatch):
    """KNOWN_POLICY_IDS=',,,' -> truthy string -> empty frozenset -> EVERYTHING 404s.
    Fails CLOSED (denies all), which is safe spend-wise."""
    ids = _build(monkeypatch, ",,,")
    assert ids == frozenset()


def test_empty_string_falls_back_not_wildcard(monkeypatch):
    """KNOWN_POLICY_IDS='' -> falsy -> falls back to builtin, not '*'."""
    ids = _build(monkeypatch, "")
    assert len(ids) == 117


def test_no_path_allows_arbitrary_id(monkeypatch):
    """There is no value of KNOWN_POLICY_IDS that makes an arbitrary id pass."""
    for val in [None, "", ",", "  ,  ,  ", "105_amazon_com"]:
        ids = _build(monkeypatch, val)
        assert "attacker_unknown_policy_zzz" not in ids
