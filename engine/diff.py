"""Claimed-vs-required delta (BUILD_PLAN §2.2: "the delta is the product").

Runs over the **ordinary (gate-null)** steps only. Gated steps are the safety
reflex's business (engine/gates.py), never the diff's.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.state import DiagnosisState
from kb.schema import AXIS_FACT_CONFIG, AXIS_FACT_TYPE, Fault

AXIS_FACTS = (AXIS_FACT_CONFIG, AXIS_FACT_TYPE)


def _fact(state: DiagnosisState, fault: Fault, key: str):
    """A branch fact: a history fact, or a loco-axis pseudo-fact resolved from the active
    loco — the latter ONLY if the fault declares that dependency (state.axis_value)."""
    if key in AXIS_FACTS:
        return state.axis_value(key, fault)
    return state.history(key)


@dataclass(frozen=True)
class StepDelta:
    next_unmet: str | None                      # first ordinary step not claimed, in TSD order
    missing: tuple[str, ...]                    # all ordinary steps not claimed, in TSD order
    complete: bool                              # no ordinary step missing
    unrecognised: tuple[str, ...] = field(default=())  # claims that are not in the checklist
    needs_axis: str | None = None               # a reached branch depends on an UNKNOWN loco axis


def diff_steps(state: DiagnosisState, fault: Fault) -> StepDelta:
    """Ordinary steps not yet claimed, in TSD order. Steps that come AFTER an unclaimed
    gated step are not yet due — the gate (evaluated by the reflex) is what is pending."""
    claimed = state.steps_claimed_done
    due: list[str] = []
    needs_axis: str | None = None
    for s in fault.steps:
        if s.gate is not None and s.id not in claimed:
            break
        if s.id in claimed:
            continue
        # a conditional branch is skipped only when a STATED fact contradicts it
        contradicted = False
        for k, v in s.applies_when.items():
            val = _fact(state, fault, k)
            if val is None and k in AXIS_FACTS and not due and needs_axis is None:
                needs_axis = k          # the NEXT step branches on a loco axis we don't know (§2.4)
            if val is not None and val != v:
                contradicted = True
        if s.gate is None and not contradicted:
            due.append(s.id)
    missing = tuple(due)
    unrecognised = tuple(state.tool_results.get("unrecognised_claims", ()))
    return StepDelta(
        next_unmet=missing[0] if missing else None,
        missing=missing,
        complete=not missing,
        unrecognised=unrecognised,
        needs_axis=needs_axis,
    )
