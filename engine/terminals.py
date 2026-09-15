"""Terminals — Class C in BUILD_PLAN §7. The ENGINE emits these; the (future) LLM only
phrases them. Every piece of text in a terminal comes from the KB (hence the TSD),
never from model knowledge. Each terminal carries its TSD citation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from kb.schema import Fault, Step

TerminalKind = Literal["confirm", "ask_step", "ask_history", "caution", "refuse", "defer_to_TLC",
                       "confirm_fault", "clarify", "ask_config"]

# Refuse reasons (engine vocabulary; fix B in the gate review).
REASON_SECOND_RESET = "second_reset"               # (f)(ii): pilot states a prior reset this trip
REASON_RECURRED = "recurred_after_reset"           # (f)(ii): relay acted again after the first reset
REASON_ABNORMALITY = "abnormality_found"           # (f)(i)
REASON_NOT_ISOLATED = "abnormality_not_isolated"   # M4: isolate-then-reset step, isolation failed


@dataclass(frozen=True)
class Terminal:
    kind: TerminalKind
    fault_id: Optional[str]
    message: str                        # engine-chosen content, from the KB
    source: str                         # TSD citation(s)
    step_id: Optional[str] = None       # for ask_step / confirm
    gate_type: Optional[str] = None     # for ask_history / caution / refuse
    reasons: tuple[str, ...] = ()       # for refuse — ALL applicable reasons
    hold_action: Optional[str] = None   # fix D: a gated action the pilot said they intend,
                                        # held until the ordinary checks are complete
    conditional: bool = False           # fix C: caution is conditional on "no abnormality"
    guidance: tuple[str, ...] = ()      # extra KB text (e.g. on_first_reset after confirm)
    unrecognised_claims: tuple[str, ...] = field(default=())
    do_now: bool = False                # ask_step: the pilot has said this check is NOT done —
                                        # tell them to do it now, do not ask again
    verbatim: bool = False              # render the KB text itself, no phrasing (pilot asked
                                        # for the exact list after the short spoken form)

    @property
    def instructs_reset(self) -> bool:
        """Eval helper (§12.2 ``must_not_do: instruct_reset``): only a reset_limit
        CAUTION ever tells the pilot to reset (the permitted first reset)."""
        return self.kind == "caution" and self.gate_type == "reset_limit"


# Engine fact appended to a confirm when the permitted reset is already claimed done; the
# phrasing keys on it (render the follow-up as what comes next, not "reset it").
RESET_DONE_NOTE = "The one permitted reset has been done."


def signature(t: Terminal) -> str:
    """What the pilot was told, for repeat detection: kind, step, content and guidance."""
    return "|".join([t.kind, t.step_id or "", t.gate_type or "", t.message, *t.guidance, *t.reasons])


# ---------------------------------------------------------------------------
# constructors
# ---------------------------------------------------------------------------

def confirm(fault: Fault, guidance: tuple[str, ...] = (), source: str = "") -> Terminal:
    """Pilot did it right: confirm, add nothing except KB follow-up guidance."""
    return Terminal(
        kind="confirm",
        fault_id=fault.fault_id,
        message="Procedure verified complete.",
        source=source or fault.source,
        guidance=guidance,
    )


def ask_step(fault: Fault, step: Step, hold_action: Optional[str] = None,
             unrecognised: tuple[str, ...] = (), do_now: bool = False) -> Terminal:
    """The ONE specific missed ordinary step (§2.2). ``do_now``: the pilot has already said
    it is not done, so it is put as the next action (the TSD step text IS an instruction),
    not as a question again."""
    return Terminal(
        kind="ask_step",
        fault_id=fault.fault_id,
        message=step.text,
        source=step.citation,
        step_id=step.id,
        hold_action=hold_action,
        unrecognised_claims=unrecognised,
        do_now=do_now,
    )


def ask_history(fault: Fault, step: Step, question: str) -> Terminal:
    """The single history question a gate needs (§2.3) — asked only if not volunteered."""
    assert step.gate is not None
    return Terminal(
        kind="ask_history",
        fault_id=fault.fault_id,
        message=question,
        source=step.gate.source,
        step_id=step.id,
        gate_type=step.gate.type,
    )


_IF_CLAUSE = re.compile(r"^\s*If\s+(.+?)[,;:]", re.IGNORECASE | re.DOTALL)


def branch_condition(step: Step) -> Optional[str]:
    """The KB step's own "If <condition>," head, verbatim — None if the step has no such head."""
    m = _IF_CLAUSE.match(step.text)
    return " ".join(m.group(1).split()) if m else None


def ask_branch(fault: Fault, steps: list[Step]) -> Terminal:
    """The next due steps are alternative branches whose conditions the pilot has not stated
    (§2.3: ask the ONE fact that decides the route, not "have you done <branch>"). The
    question is assembled from the steps' own "If ..." clauses; nothing is composed."""
    conds = [c for c in (branch_condition(s) for s in steps) if c]
    question = "Which applies now: " + "; or ".join(conds) + "? Or has it not recurred?"
    return Terminal(
        kind="ask_history",
        fault_id=fault.fault_id,
        message=question,
        source="; ".join(dict.fromkeys(s.citation for s in steps)),
        step_id=steps[0].id,
    )


def caution(fault: Fault, step: Step, message: str, conditional: bool = False) -> Terminal:
    assert step.gate is not None
    return Terminal(
        kind="caution",
        fault_id=fault.fault_id,
        message=message,
        source=step.gate.source,
        step_id=step.id,
        gate_type=step.gate.type,
        conditional=conditional,
    )


def refuse(fault: Fault, step: Step, message: str, reasons: tuple[str, ...],
           source: Optional[str] = None) -> Terminal:
    assert step.gate is not None
    return Terminal(
        kind="refuse",
        fault_id=fault.fault_id,
        message=message,
        source=source or step.gate.source,
        step_id=step.id,
        gate_type=step.gate.type,
        reasons=reasons,
    )


def confirm_fault(fault: Fault) -> Terminal:
    """§5.5: an LLM-matched fault that carries a hard gate is confirmed in ONE line before
    any guidance. The line names the fault and its KB presenting signs — nothing else."""
    signs = "; ".join(fault.presenting_signs)
    return Terminal(
        kind="confirm_fault",
        fault_id=fault.fault_id,
        message=f"Sounds like {fault.fault_id.replace('_', ' ')} — {signs}?",
        source=fault.source,
    )


def ask_config(fault: Fault, axis: str) -> Terminal:
    """§2.4: ask for loco type / config ONLY when the reached branch depends on it."""
    q = ("Is this loco SIV or ARNO fitted?" if axis == "loco_config"
         else "Which class is this loco — WAG-7, WAG-5 or WAP-4?")
    return Terminal(kind="ask_config", fault_id=fault.fault_id, message=q,
                    source=f"BUILD_PLAN §2.4 (branch of {fault.fault_id} depends on {axis})",
                    step_id=None, gate_type=None, reasons=(axis,))


def clarify(question: str) -> Terminal:
    """Low-confidence parse (§8): one-line clarification instead of acting."""
    return Terminal(kind="clarify", fault_id=None, message=question,
                    source="BUILD_PLAN §8 (low-confidence parse → clarify, never act)")


def defer_to_TLC(reason: str, fault_id: Optional[str] = None) -> Terminal:
    """Unknown / out-of-scope fault, or no KB procedure (§5.2, §5.6): never guess."""
    return Terminal(
        kind="defer_to_TLC",
        fault_id=fault_id,
        message=f"{reason} Contact TLC.",
        source="BUILD_PLAN §5.6 (no procedure content)",
    )
