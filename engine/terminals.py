"""Terminals — Class C in BUILD_PLAN §7. The ENGINE emits these; the (future) LLM only
phrases them. Every piece of text in a terminal comes from the KB (hence the TSD),
never from model knowledge. Each terminal carries its TSD citation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from kb.schema import Fault, Step

TerminalKind = Literal["confirm", "ask_step", "ask_history", "caution", "refuse", "defer_to_TLC"]

# Refuse reasons (engine vocabulary; fix B in the gate review).
REASON_SECOND_RESET = "second_reset"
REASON_ABNORMALITY = "abnormality_found"


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

    @property
    def instructs_reset(self) -> bool:
        """Eval helper (§12.2 ``must_not_do: instruct_reset``): only a reset_limit
        CAUTION ever tells the pilot to reset (the permitted first reset)."""
        return self.kind == "caution" and self.gate_type == "reset_limit"


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
             unrecognised: tuple[str, ...] = ()) -> Terminal:
    """The ONE specific missed ordinary step (§2.2)."""
    return Terminal(
        kind="ask_step",
        fault_id=fault.fault_id,
        message=step.text,
        source=step.citation,
        step_id=step.id,
        hold_action=hold_action,
        unrecognised_claims=unrecognised,
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


def defer_to_TLC(reason: str, fault_id: Optional[str] = None) -> Terminal:
    """Unknown / out-of-scope fault, or no KB procedure (§5.2, §5.6): never guess."""
    return Terminal(
        kind="defer_to_TLC",
        fault_id=fault_id,
        message=f"{reason} Contact TLC.",
        source="BUILD_PLAN §5.6 (no procedure content)",
    )
