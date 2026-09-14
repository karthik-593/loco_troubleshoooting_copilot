"""phrase — render the engine's terminal as brief natural language. LLM job 3 of 3.

The model adds nothing the engine did not decide (§4.2). That is enforced two ways:
1. the model is only ever shown the terminal's own KB content, never the KB at large;
2. ``guard`` runs deterministic checks on the output per terminal kind — a refusal must
   still refuse, a caution must keep every condition, a question must still ask — and on
   ANY violation the reply falls back to ``render_verbatim``: the terminal's KB text,
   unphrased. A safety-relevant reply is therefore never worse than the KB's own words.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from engine.terminals import Terminal
from llm.interface import LLMProvider

PROMPT_PATH = Path(__file__).with_name("prompts") / "phrase.md"
MAX_CHARS = 600


@dataclass(frozen=True)
class PhraseResult:
    text: str
    used_fallback: bool
    violations: tuple[str, ...] = field(default=())


# ---------------------------------------------------------------------------
# deterministic rendering (fallback + the content the model is shown)
# ---------------------------------------------------------------------------

def render_verbatim(t: Terminal) -> str:
    if t.kind == "confirm":
        return " ".join(["Procedure verified — nothing missed.", *t.guidance]).strip()
    if t.kind == "ask_step":
        hold = f" Hold the {t.hold_action.replace('_', ' ')} until this is done." if t.hold_action else ""
        return f"Next check: {t.message}{hold} Done?"
    if t.kind == "ask_history":
        return t.message
    if t.kind == "caution":
        cond = "If no abnormality was found in the checks: " if t.conditional else ""
        return f"{cond}{t.message}"
    if t.kind == "refuse":
        return t.message
    if t.kind in ("confirm_fault", "clarify"):
        return t.message
    if t.kind == "defer_to_TLC":
        return t.message
    raise AssertionError(t.kind)


def terminal_payload(t: Terminal) -> str:
    lines = [f"kind: {t.kind}", f"content: {t.message}"]
    if t.guidance:
        lines.append(f"guidance: {' '.join(t.guidance)}")
    if t.hold_action:
        lines.append(f"hold_action: the pilot intends to {t.hold_action.replace('_', ' ')} — "
                     f"say plainly that this waits until the check is done")
    if t.conditional:
        lines.append("conditional: yes — applies only if no abnormality was found")
    if t.reasons:
        lines.append(f"reasons: {', '.join(t.reasons)}")
    if t.unrecognised_claims:
        lines.append(f"unrecognised_claims (mention briefly that these are not in the procedure): "
                     f"{', '.join(t.unrecognised_claims)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------

_NEGATION = re.compile(r"\b(not|don'?t|never|no|neither|nor|without)\b", re.I)
_RESET = re.compile(r"\bre-?set", re.I)


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?;])\s+|\n+", text) if p.strip()]


def _unnegated_reset_sentences(text: str) -> list[str]:
    """Sentences that mention a reset without any negation — i.e. read as an instruction."""
    return [p for p in _sentences(text) if _RESET.search(p) and not _NEGATION.search(p)]


def guard(t: Terminal, text: str) -> tuple[str, ...]:
    v: list[str] = []
    s = text.strip()
    low = s.lower()
    if not s:
        return ("empty",)
    if len(s) > MAX_CHARS:
        v.append("too_long")
    if re.search(r"^\s*([-*#]|\d+\.)\s", s, re.M):
        v.append("markdown_structure")

    if t.kind == "refuse":
        if not any(_RESET.search(p) and _NEGATION.search(p) for p in _sentences(s)):
            v.append("refusal_not_negated")
        if _unnegated_reset_sentences(s):
            v.append("refusal_instructs_reset")
        if "tlc" not in low:
            v.append("refusal_missing_TLC")
        if "relief" in t.message.lower() and "relief" not in low:
            v.append("refusal_missing_relief")
        if "fire extinguisher" in t.message.lower() and "extinguisher" not in low:
            v.append("refusal_missing_fire_precaution")
    elif t.kind == "caution":
        for key, needle in (("once", "once"), ("interval", "10 min"), ("TLC", "tlc"), ("log", "log")):
            if needle in t.message.lower() and needle not in low:
                v.append(f"caution_missing_{key}")
        if t.conditional and "abnormal" not in low:
            v.append("caution_dropped_condition")
    elif t.kind in ("ask_step", "ask_history", "confirm_fault", "clarify"):
        if "?" not in s:
            v.append("question_not_asked")
        if t.kind == "ask_step" and t.hold_action and not re.search(r"\b(before|until|after|hold|wait|first)\b", low):
            v.append("hold_action_dropped")
    elif t.kind == "confirm":
        if any("10 min" in g.lower() for g in t.guidance) and "10 min" not in low:
            v.append("confirm_missing_guidance")
    elif t.kind == "defer_to_TLC":
        if "tlc" not in low:
            v.append("defer_missing_TLC")
    return tuple(v)


def phrase(t: Terminal, provider: LLMProvider) -> PhraseResult:
    system = PROMPT_PATH.read_text(encoding="utf-8")
    try:
        text = provider.text(system, terminal_payload(t))
    except Exception as exc:  # provider failure → the KB's own words, never silence
        return PhraseResult(render_verbatim(t), True, (f"provider_error:{type(exc).__name__}",))
    violations = guard(t, text)
    if violations:
        return PhraseResult(render_verbatim(t), True, violations)
    return PhraseResult(text.strip(), False)
