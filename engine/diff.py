"""Claimed-vs-required delta (BUILD_PLAN §2.2: "the delta is the product").

Runs over the **ordinary (gate-null)** steps only. Gated steps are the safety
reflex's business (engine/gates.py), never the diff's.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine.state import DiagnosisState
from kb.schema import AXIS_FACT_CONFIG, AXIS_FACT_RB, AXIS_FACT_TYPE, Fault

AXIS_FACTS = (AXIS_FACT_CONFIG, AXIS_FACT_TYPE, AXIS_FACT_RB)


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
    completed: bool = False                     # a `completes` step is claimed: the procedure ended there


def _route_groups(fault: Fault) -> list[list[str]]:
    """Consecutive gate-free steps that each carry an applies_when with an "If ..." head and
    pairwise-disjoint condition keys: the alternative routes of one decision point."""
    groups: list[list[str]] = []
    cur: list[str] = []
    keys: set[str] = set()
    for s in fault.steps:
        alt = (s.gate is None and bool(s.applies_when) and not s.requires_stated
               and re.match(r"^\s*If\s", s.text, re.IGNORECASE) is not None)
        if alt and not (keys & set(s.applies_when)):
            cur.append(s.id); keys |= set(s.applies_when)
        else:
            if len(cur) > 1:
                groups.append(cur)
            cur, keys = ([s.id], set(s.applies_when)) if alt else ([], set())
    if len(cur) > 1:
        groups.append(cur)
    return groups


def _is_route_alternative(fault: Fault, step) -> bool:
    return any(step.id in g for g in _route_groups(fault))


def _chosen_route_keys(state: DiagnosisState, fault: Fault) -> set[str]:
    """Condition keys of route alternatives whose condition the pilot has STATED true: choosing
    one route excludes its unstated siblings ("dropping frequently" → not "after a long interval")."""
    chosen: set[str] = set()
    for g in _route_groups(fault):
        for sid in g:
            s = fault.step(sid)
            if all(_fact(state, fault, k) == v for k, v in s.applies_when.items()):
                chosen |= set(s.applies_when)
    return chosen


def step_skipped(state: DiagnosisState, fault: Fault, step) -> bool:
    """Not due: a conditional step a STATED fact contradicts, or a side-note (requires_stated)
    whose condition is not stated as required. Applies to gated steps too (batch 2: "or wedge
    Q118" inside the MVRH-not-working branch of §7.07 must not gate the other branches)."""
    contradicted = unstated = False
    for k, v in step.applies_when.items():
        val = _fact(state, fault, k)
        if val is None:
            unstated = True
        elif val != v:
            contradicted = True
    if step.requires_stated and (unstated or contradicted):
        return True
    return contradicted


def diff_steps(state: DiagnosisState, fault: Fault) -> StepDelta:
    """Ordinary steps not yet claimed, in TSD order. Steps that come AFTER an unclaimed
    gated step are not yet due — the gate (evaluated by the reflex) is what is pending."""
    claimed = state.steps_claimed_done
    due: list[str] = []
    needs_axis: str | None = None
    if any(s.completes and s.id in claimed for s in fault.steps):
        # a completing step is done: the procedure ended on it (its alternatives are not due)
        return StepDelta(next_unmet=None, missing=(), complete=True, completed=True,
                         unrecognised=tuple(state.tool_results.get("unrecognised_claims", ())))
    chosen_keys = _chosen_route_keys(state, fault)
    for s in fault.steps:
        if s.elicits:
            # An observation step exists to elicit ONE fact: it is done when — and only when —
            # that fact is stated. A claim without the answer is not completion (seen live
            # 2026-09-17: the parser claimed the VCB-type QUESTION step from a first message
            # describing the sign, and the engine confirmed the procedure on that fake claim).
            if _fact(state, fault, s.elicits) is not None:
                continue
        elif s.id in claimed:
            continue
        # a conditional branch is skipped only when a STATED fact contradicts it — or, for a
        # side-note step (requires_stated), unless every fact is stated as required
        contradicted = False
        unstated = False
        for k, v in s.applies_when.items():
            val = _fact(state, fault, k)
            if val is None and k in AXIS_FACTS and not due and needs_axis is None and s.gate is None:
                needs_axis = k          # the NEXT step branches on a loco axis we don't know (§2.4)
            if val is None:
                unstated = True
            elif val != v:
                contradicted = True
        if s.requires_stated and (unstated or contradicted):
            continue
        if s.gate is not None:
            if contradicted:
                continue                # a gated step in a branch not taken: not pending
            break                       # an unclaimed, due gated step: what is pending is the gate
        # a sibling route was chosen (its condition stated true) and this one's is unstated
        if (not contradicted and unstated and s.applies_when and not s.requires_stated
                and chosen_keys and not (set(s.applies_when) & chosen_keys)
                and _is_route_alternative(fault, s)):
            continue
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
