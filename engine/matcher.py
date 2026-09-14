"""KB loader + deterministic fault matching.

M1 scope (HANDOFF): load the KB into typed objects and match a pilot's fault name by
**exact alias** (case/whitespace-insensitive). Free-text / LLM-assisted matching is an
M2 job (BUILD_PLAN §8 parse) and is stubbed here — it must raise, never guess.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from kb.schema import FAULTS_DIR, Fault, load_fault_file

# Re-exported so engine code imports its types from one place.
__all__ = ["Fault", "KnowledgeBase", "load_kb", "match_alias", "match_free_text"]


def _norm(text: str) -> str:
    """Normalise for alias comparison: lower-case, collapse whitespace, strip punctuation."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s/-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class KnowledgeBase:
    def __init__(self, faults: dict[str, Fault]):
        self._faults = faults
        # alias -> fault_id, normalised. The fault_id itself is also accepted.
        self._alias_index: dict[str, str] = {}
        for fid, f in faults.items():
            for alias in [fid, *f.aliases]:
                key = _norm(alias)
                other = self._alias_index.get(key)
                if other is not None and other != fid:
                    raise ValueError(f"alias {alias!r} is ambiguous between {other} and {fid}")
                self._alias_index[key] = fid

    @property
    def fault_ids(self) -> list[str]:
        return sorted(self._faults)

    def get(self, fault_id: str) -> Fault:
        return self._faults[fault_id]

    def match_alias(self, text: str) -> Optional[Fault]:
        """Exact (normalised) alias match. Returns None when nothing matches — the
        caller must then refuse / defer to TLC (BUILD_PLAN §5.6), never guess."""
        fid = self._alias_index.get(_norm(text))
        return self._faults[fid] if fid else None


def load_kb(faults_dir: Path = FAULTS_DIR) -> KnowledgeBase:
    faults: dict[str, Fault] = {}
    for path in sorted(faults_dir.glob("*.yaml")):
        f = load_fault_file(path)
        if f.fault_id in faults:
            raise ValueError(f"duplicate fault_id {f.fault_id} in {path.name}")
        faults[f.fault_id] = f
    return KnowledgeBase(faults)


_default_kb: Optional[KnowledgeBase] = None


def default_kb() -> KnowledgeBase:
    global _default_kb
    if _default_kb is None:
        _default_kb = load_kb()
    return _default_kb


def match_alias(text: str, kb: Optional[KnowledgeBase] = None) -> Optional[Fault]:
    return (kb or default_kb()).match_alias(text)


def match_free_text(text: str, kb: Optional[KnowledgeBase] = None):
    """M2: LLM-assisted parse of a messy pilot turn into a fault guess + confidence.
    Deliberately unimplemented in M1 — there is no NLP in the deterministic core."""
    raise NotImplementedError("free-text fault matching is an M2 (LLM parse) capability")
