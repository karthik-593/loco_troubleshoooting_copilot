"""Conversation state (BUILD_PLAN §10.1, field-for-field) + the state transition.

``update_state`` is the ONLY way state changes. Every call to it must be followed by
the safety reflex (``engine.gates.evaluate_gates``) — see §5.1. In M1 that sequencing
lives in ``engine.run_turn``; in M3 it is wired into the LangGraph graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from kb.schema import Fault

Config = Literal["siv", "arno", "unknown"]
YesNo = Literal["yes", "no"]

# History-fact keys used by the QLM procedure. ``was_QLM_reset_earlier_this_trip`` is
# the reset_limit gate's ``needs_history`` key in kb/faults/qlm_dropped.yaml;
# ``abnormality_found`` is the pilot's verdict on §6.1.1(a)–(c).
HF_RESET_EARLIER = "was_QLM_reset_earlier_this_trip"
HF_ABNORMALITY = "abnormality_found"          # fault-wide default abnormality verdict
HF_RESOLVED = "fault_resolved"                 # pilot reports the fault cleared (gate-free faults)
HF_OTHER_RELAYS = "other_relays_acted"         # list of other relay targets the pilot reported

# Intended-action vocabulary (structured; the M2 parser maps free text onto these).
ACTION_RESET_QLM = "reset_QLM"


@dataclass
class DiagnosisState:
    matched_fault: Optional[str] = None            # fault_id | null
    fault_confirmed: bool = False
    config: Config = "unknown"                     # resolved lazily (§2.4)
    steps_required: list[str] = field(default_factory=list)   # from KB once matched
    steps_claimed_done: set[str] = field(default_factory=set)
    history_facts: dict[str, Any] = field(default_factory=dict)  # e.g. reset-earlier: yes/no
    intended_action: Optional[str] = None          # pilot's next move, for the reflex
    stuck_at: Optional[str] = None
    tool_results: dict[str, Any] = field(default_factory=dict)  # idempotency / no-progress
    iter_count: int = 0                            # loop guard (§6)

    # -- helpers -------------------------------------------------------------
    def history(self, key: str) -> Optional[str]:
        """Tri-state read: 'yes' | 'no' | None (= not stated)."""
        v = self.history_facts.get(key)
        return None if v is None else str(v).lower()

    def snapshot(self) -> tuple:
        """Hashable view of the diagnostic fields, for the no-progress detector (§6)."""
        return (
            self.matched_fault,
            self.fault_confirmed,
            self.config,
            tuple(self.steps_required),
            tuple(sorted(self.steps_claimed_done)),
            tuple(sorted((k, str(v)) for k, v in self.history_facts.items())),
            self.intended_action,
            self.stuck_at,
        )


@dataclass(frozen=True)
class StateUpdate:
    """A structured update — what the M2 ``parse`` node will emit, built by hand in M1.

    Field semantics are *merge*: ``None`` / empty means "nothing new on this field".
    """
    fault_id: Optional[str] = None
    # None with a fault_id = "structured/deterministic match" → treated as confirmed.
    # The M2 parser passes an explicit False for an LLM-guessed hard-gated fault (§5.5).
    fault_confirmed: Optional[bool] = None
    config: Optional[Config] = None
    claimed_steps: tuple[str, ...] = ()
    history: dict[str, Any] = field(default_factory=dict)
    intended_action: Optional[str] = None
    clear_intended_action: bool = False


def update_state(state: DiagnosisState, update: StateUpdate, fault: Optional[Fault]) -> DiagnosisState:
    """Merge ``update`` into ``state`` in place and return it.

    * ``steps_required`` is (re)populated from the KB when a fault is matched.
    * Claimed steps are recorded **only if they exist in the matched fault's checklist**
      (BUILD_PLAN §8: unrecognised claims are surfaced by the diff, never silently
      accepted). Unrecognised claims are kept aside in ``tool_results['unrecognised_claims']``
      so the diff can report them.
    * ``intended_action`` persists across turns until explicitly cleared or replaced.
    """
    if update.fault_id is not None:
        if update.fault_id != state.matched_fault:
            state.fault_confirmed = False
        state.matched_fault = update.fault_id
        if update.fault_confirmed is None:
            state.fault_confirmed = True      # structured / alias match is trusted
    if fault is not None and state.matched_fault == fault.fault_id:
        state.steps_required = list(fault.step_ids)
    if update.fault_confirmed is not None:
        state.fault_confirmed = update.fault_confirmed
    if update.config is not None:
        state.config = update.config

    known = set(state.steps_required)
    unrecognised = [s for s in update.claimed_steps if s not in known]
    state.steps_claimed_done.update(s for s in update.claimed_steps if s in known)
    if unrecognised:
        state.tool_results["unrecognised_claims"] = unrecognised
    else:
        state.tool_results.pop("unrecognised_claims", None)

    state.history_facts.update(update.history)

    if update.clear_intended_action:
        state.intended_action = None
    elif update.intended_action is not None:
        state.intended_action = update.intended_action
    return state


def resolve_combination(state: DiagnosisState, fault: Optional[Fault], lookup) -> Optional[str]:
    """DETERMINISTIC fault-identity resolution, run right after every state update and
    BEFORE the reflex: if the pilot has reported other relays that match one of the matched
    fault's combination rules and the target fault is in the KB, switch ``matched_fault``
    to it (claimed steps carry over — the combination faults reuse the step ids).

    Why here and not left to the agent's ``check_combination`` tool: the reflex evaluates
    gates against ``matched_fault``; if identity waited on a discretionary tool, a gate
    could fire (and short-circuit the loop) under the WRONG procedure — observed live
    2026-09-15. ``check_combination`` remains available to the agent as a query.
    ``lookup(fault_id) -> Fault | None`` is the KB accessor. Returns the new fault_id or None."""
    if fault is None or state.matched_fault != fault.fault_id:
        return None
    reported = {str(r).upper() for r in (state.history_facts.get(HF_OTHER_RELAYS) or [])}
    if not reported:
        return None
    for rule in fault.combination_rules:
        if reported & {r.upper() for r in rule.if_also}:
            target = lookup(rule.route_to)
            if target is None:
                return None                              # not encoded → stays; reassess defers to TLC
            state.matched_fault = target.fault_id
            state.steps_required = list(target.step_ids)
            state.steps_claimed_done &= set(target.step_ids)
            return target.fault_id
    return None


def checks_complete(state: DiagnosisState, fault: Fault) -> bool:
    """True when every ORDINARY (gate-null) step of ``fault`` is claimed done."""
    return all(s.id in state.steps_claimed_done for s in fault.ordinary_steps)
