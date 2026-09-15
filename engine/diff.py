"""Claimed-vs-required delta (BUILD_PLAN §2.2: "the delta is the product").

Runs over the **ordinary (gate-null)** steps only. Gated steps are the safety
reflex's business (engine/gates.py), never the diff's.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.state import DiagnosisState
from kb.schema import Fault


@dataclass(frozen=True)
class StepDelta:
    next_unmet: str | None                      # first ordinary step not claimed, in TSD order
    missing: tuple[str, ...]                    # all ordinary steps not claimed, in TSD order
    complete: bool                              # no ordinary step missing
    unrecognised: tuple[str, ...] = field(default=())  # claims that are not in the checklist


def diff_steps(state: DiagnosisState, fault: Fault) -> StepDelta:
    """Ordinary steps not yet claimed, in TSD order. Steps that come AFTER an unclaimed
    gated step are not yet due — the gate (evaluated by the reflex) is what is pending."""
    claimed = state.steps_claimed_done
    due: list[str] = []
    for s in fault.steps:
        if s.gate is not None and s.id not in claimed:
            break
        if s.id in claimed:
            continue
        # a conditional branch is skipped only when a STATED fact contradicts it
        contradicted = any(state.history(k) is not None and state.history(k) != v
                           for k, v in s.applies_when.items())
        if s.gate is None and not contradicted:
            due.append(s.id)
    missing = tuple(due)
    unrecognised = tuple(state.tool_results.get("unrecognised_claims", ()))
    return StepDelta(
        next_unmet=missing[0] if missing else None,
        missing=missing,
        complete=not missing,
        unrecognised=unrecognised,
    )
