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
        """Deterministic alias match: the whole normalised text equals an alias, or an
        alias appears verbatim as a whole phrase inside it ("QLM locked, all normal").
        Returns None when nothing matches — the caller then either asks the M2 parser
        for a (to-be-confirmed) guess or defers to TLC (BUILD_PLAN §5.6); never guesses."""
        t = _norm(text)
        fid = self._alias_index.get(t)
        if fid:
            return self._faults[fid]
        hits = {f for alias, f in self._alias_index.items()
                if re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", t)}
        if len(hits) == 1:
            return self._faults[hits.pop()]
        return None  # none, or ambiguous across faults → not deterministic


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
    """LLM-assisted matching lives in ``llm.parse.parse_turn`` (M2). The engine package
    itself contains no NLP and never will; this stub exists so nothing in ``engine/``
    can be mistaken for a model-backed matcher."""
    raise NotImplementedError("free-text fault matching is llm.parse.parse_turn, not an engine capability")
