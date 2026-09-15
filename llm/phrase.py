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

from engine.terminals import REASON_NOT_ISOLATED, REASON_RECURRED, REASON_SECOND_RESET, Terminal

# How each refusal reason is to be framed (the verdict text is the KB's; only the framing differs).
_REASON_FRAMING = {
    REASON_SECOND_RESET: "the pilot states the relay was already reset once earlier this trip",
    REASON_RECURRED: "the relay re-tripped after the first reset — the fault is real; do not reset "
                     "again; get relief",
    "abnormality_found": "an abnormality was found in the checks",
    REASON_NOT_ISOLATED: "the abnormal equipment could not be isolated",
}
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
    if t.kind in ("confirm_fault", "clarify", "ask_config"):
        return t.message
    if t.kind == "defer_to_TLC":
        return t.message
    raise AssertionError(t.kind)


def terminal_payload(t: Terminal) -> str:
    lines = [f"kind: {t.kind}", f"content: {t.message}"]
    if t.guidance:
        lines.append(f"guidance: {' '.join(t.guidance)}")
    if t.kind == "confirm" and "reset has been done" in t.message:
        lines.append("already_done: the reset in the guidance is DONE — phrase the guidance as what "
                     "follows from here (resume, the 10-minute checks, log book, TLC); do not tell "
                     "them to reset")
    if t.hold_action:
        lines.append(f"hold_action: the pilot intends to {t.hold_action.replace('_', ' ')} — "
                     f"say plainly that this waits until the check is done")
    if t.conditional:
        lines.append("conditional: yes — applies only if no abnormality was found")
    if t.reasons:
        lines.append("reasons: " + "; ".join(f"{r} ({_REASON_FRAMING.get(r, r)})" for r in t.reasons))
    if t.unrecognised_claims:
        lines.append(f"unrecognised_claims (mention briefly that these are not in the procedure): "
                     f"{', '.join(t.unrecognised_claims)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------

_NEGATION = re.compile(r"\b(not|don'?t|never|no|neither|nor|without)\b", re.I)
_RESET = re.compile(r"\bre-?set", re.I)
# "reset" referring to a reset ALREADY MADE — as a noun ("after your reset", "the first
# reset") or a past-tense verb with a subject ("after you reset it", "it was reset") — is
# not an instruction. Stripped before the instruction check.
_RESET_AS_EVENT = re.compile(
    r"\b(after|following|since|before|post|the|your|that|this|a|first|earlier|previous|last|second)"
    r"(\s+(first|last|earlier|previous|second|initial))?\s+re-?set(s|ting)?\b"
    r"|\b(you|we|i|they|he|she|pilot|it|was|were|been|already|having|have|had)"
    r"(\s+(have|had|already|just|then))?\s+re-?set\b", re.I)


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?;])\s+|\n+", text) if p.strip()]


def _unnegated_reset_sentences(text: str) -> list[str]:
    """Sentences that read as a reset INSTRUCTION: a reset verb with no negation, after
    discounting noun uses that merely refer to a reset already made."""
    out = []
    for p in _sentences(text):
        stripped = _RESET_AS_EVENT.sub(" ", p)
        if _RESET.search(stripped) and not _NEGATION.search(p):
            out.append(p)
    return out


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
        # An isolation-failed refusal's KB text is "contact TLC" (§6.1.2(b)) with no literal
        # "do not reset"; every other refusal must keep an explicit negated reset.
        needs_negation = t.reasons != (REASON_NOT_ISOLATED,)
        if needs_negation and not any(_RESET.search(p) and _NEGATION.search(p) for p in _sentences(s)):
            v.append("refusal_not_negated")
        if _unnegated_reset_sentences(s):
            v.append("refusal_instructs_reset")
        if "tlc" not in low:
            v.append("refusal_missing_TLC")
        if "relief" in t.message.lower() and "relief" not in low:
            v.append("refusal_missing_relief")
        if "fire extinguisher" in t.message.lower() and "extinguisher" not in low:
            v.append("refusal_missing_fire_precaution")
        # (f)(ii) framing must reflect the actual situation (engine-decided reason)
        if REASON_RECURRED in t.reasons and not re.search(
                r"\b(re-?trip\w*|re-?lock\w*|recur\w*|second time|twice|once now|(acted|tripped|dropped|locked) again"
                r"|after (the|your|that) reset|after you reset)\b", low):
            v.append("refusal_recurrence_framing_missing")
        if REASON_SECOND_RESET in t.reasons and REASON_RECURRED not in t.reasons and not re.search(
                r"\b(already|earlier|once|second time|twice|again)\b", low):
            v.append("refusal_prior_reset_framing_missing")
    elif t.kind == "caution":
        for key, needle in (("once", "once"), ("interval", "10 min"), ("TLC", "tlc"), ("log", "log")):
            if needle in t.message.lower() and needle not in low:
                v.append(f"caution_missing_{key}")
        if t.conditional and "abnormal" not in low:
            v.append("caution_dropped_condition")
    elif t.kind in ("ask_step", "ask_history", "confirm_fault", "clarify", "ask_config"):
        if "?" not in s:
            v.append("question_not_asked")
        if t.kind == "ask_step" and t.hold_action and not re.search(r"\b(before|until|after|hold|wait|first)\b", low):
            v.append("hold_action_dropped")
        if t.kind == "ask_step" and not t.hold_action and re.search(r"\b(hold|wait|do not (move|resume|proceed))\b", low):
            v.append("added_hold_instruction")            # the engine issued no hold
    elif t.kind == "confirm":
        if any("10 min" in g.lower() for g in t.guidance) and "10 min" not in low:
            v.append("confirm_missing_guidance")
    elif t.kind == "defer_to_TLC":
        if "tlc" not in low:
            v.append("defer_missing_TLC")
        if "procedure set" in t.message and "i can verify" not in low:
            v.append("out_of_scope_dropped_coverage")     # §5.6: the list of covered faults must survive
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
