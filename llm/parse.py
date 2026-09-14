"""parse — pilot free text → StateUpdate (+ confidence). LLM job 1 of 3 (BUILD_PLAN §8).

Order of precedence, deterministic first:
1. exact alias match (engine/matcher) → fault matched AND confirmed, no model involved
   for the fault itself (the model still extracts claims / history);
2. otherwise the model guesses the fault from the KB's fault list, with a confidence:
   * below ``CLARIFY_THRESHOLD`` → no update; ask a one-line clarification (§8);
   * else the fault is set but ``fault_confirmed=False`` — a hard-gated fault must be
     confirmed by the pilot before any guidance (§5.5; engine/reassess step 2b).

Everything the model returns is validated against the KB: unknown fault → dropped;
claimed step ids not in the checklist → rejected and surfaced (§8, "never silently
accepted"). The model's output never enters state unvalidated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from engine.matcher import KnowledgeBase
from engine.state import (
    HF_ABNORMALITY,
    HF_RESET_EARLIER,
    DiagnosisState,
    StateUpdate,
)
from engine.tools import HF_OTHER_RELAYS
from llm.interface import LLMProvider
from llm.schemas import ParseOutput

PROMPT_PATH = Path(__file__).with_name("prompts") / "parse.md"
CLARIFY_THRESHOLD = 0.6

CLARIFY_QUESTION = ("I didn't catch which relay or fault this is. Which relay target has "
                    "dropped, and what have you checked so far?")


@dataclass(frozen=True)
class ParseResult:
    update: Optional[StateUpdate]          # None → do not act
    confidence: float
    needs_clarification: bool = False
    clarification: Optional[str] = None
    raw: Optional[ParseOutput] = None
    rejected_steps: tuple[str, ...] = field(default=())   # model claims not in the KB


# ---------------------------------------------------------------------------
# prompt assembly — the KB vocabulary the model is allowed to map onto
# ---------------------------------------------------------------------------

def kb_vocabulary(kb: KnowledgeBase) -> str:
    lines = ["Faults (fault_id — aliases — presenting signs):"]
    for fid in kb.fault_ids:
        f = kb.get(fid)
        lines.append(f"- {fid} — {', '.join(f.aliases)} — {'; '.join(f.presenting_signs)}")
    lines.append("")
    lines.append("Checklist step ids (per fault; claim only if the pilot says it was done):")
    for fid in kb.fault_ids:
        f = kb.get(fid)
        for s in f.ordinary_steps:
            lines.append(f"- {fid}.{s.id}: {s.text}")
        for s in f.gated_steps:
            lines.append(f"- {fid}.{s.id}: (gated) the pilot says they already reset / performed the gated action")
    lines.append("")
    lines.append("Actions: reset_QLM")
    return "\n".join(lines)


def system_prompt(kb: KnowledgeBase) -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").replace("{kb_vocabulary}", kb_vocabulary(kb))


def user_prompt(text: str, state: DiagnosisState, last_assistant: Optional[str]) -> str:
    ctx = []
    if state.matched_fault:
        ctx.append(f"Current matched fault: {state.matched_fault} "
                   f"({'confirmed' if state.fault_confirmed else 'NOT yet confirmed'})")
    if state.steps_claimed_done:
        ctx.append(f"Steps already claimed: {sorted(state.steps_claimed_done)}")
    if last_assistant:
        ctx.append(f"Assistant's last message: {last_assistant}")
    ctx.append(f"Pilot's message: {text}")
    return "\n".join(ctx)


# ---------------------------------------------------------------------------
# validation against the KB
# ---------------------------------------------------------------------------

def _yn(v: str) -> Optional[str]:
    return None if v == "unknown" else v


def _validate(out: ParseOutput, fault_id: Optional[str], kb: KnowledgeBase
              ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the model's claimed steps into (accepted, rejected) against the KB."""
    if not fault_id or fault_id not in kb.fault_ids:
        return (), tuple(out.claimed_steps)
    known = set(kb.get(fault_id).step_ids)
    accepted, rejected = [], []
    for s in out.claimed_steps:
        sid = s.split(".", 1)[1] if s.startswith(fault_id + ".") else s
        (accepted if sid in known else rejected).append(sid)
    return tuple(accepted), tuple(rejected)


def parse_turn(text: str, state: DiagnosisState, kb: KnowledgeBase, provider: LLMProvider,
               last_assistant: Optional[str] = None) -> ParseResult:
    # 1. deterministic alias match — no model needed for the fault.
    alias_hit = kb.match_alias(text)

    out = provider.structured(system_prompt(kb), user_prompt(text, state, last_assistant), ParseOutput)

    # 2. resolve the fault: alias > current state > model guess (validated).
    if alias_hit is not None:
        fault_id, confirmed, confidence = alias_hit.fault_id, True, 1.0
    elif state.matched_fault:
        fault_id, confirmed, confidence = state.matched_fault, None, 1.0
        if out.confirms_fault == "yes":
            confirmed = True
        elif out.confirms_fault == "no":
            # pilot says it is NOT that fault: drop it; fall through to a model guess
            guess = out.fault_guess if out.fault_guess in kb.fault_ids else None
            if guess and guess != state.matched_fault:
                fault_id, confirmed, confidence = guess, False, out.fault_confidence
            else:
                return ParseResult(None, out.fault_confidence, True, CLARIFY_QUESTION, out)
    else:
        guess = out.fault_guess if out.fault_guess in kb.fault_ids else None
        if guess is None or out.fault_confidence < CLARIFY_THRESHOLD:
            return ParseResult(None, out.fault_confidence, True, CLARIFY_QUESTION, out)
        fault_id, confirmed, confidence = guess, False, out.fault_confidence

    accepted, rejected = _validate(out, fault_id, kb)

    history: dict = {}
    if _yn(out.abnormality_found):
        history[HF_ABNORMALITY] = out.abnormality_found
    if _yn(out.was_reset_earlier_this_trip):
        history[HF_RESET_EARLIER] = out.was_reset_earlier_this_trip
    if out.other_relays_acted is not None:
        history[HF_OTHER_RELAYS] = [r.upper() for r in out.other_relays_acted]

    update = StateUpdate(
        fault_id=fault_id if fault_id != state.matched_fault else None,
        fault_confirmed=confirmed,
        claimed_steps=accepted + rejected,   # rejected ones are surfaced by update_state/diff
        history=history,
        intended_action=out.intended_action,
    )
    return ParseResult(update, confidence, False, None, out, rejected_steps=rejected + tuple(out.unmapped_claims))
