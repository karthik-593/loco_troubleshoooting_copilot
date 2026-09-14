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
    claimed = state.steps_claimed_done
    missing = tuple(s.id for s in fault.ordinary_steps if s.id not in claimed)
    unrecognised = tuple(state.tool_results.get("unrecognised_claims", ()))
    return StepDelta(
        next_unmet=missing[0] if missing else None,
        missing=missing,
        complete=not missing,
        unrecognised=unrecognised,
    )
