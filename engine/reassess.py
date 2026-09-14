"""Deterministic router (BUILD_PLAN §4.2 ``reassess``) + loop guards (§6).

Routing, in this fixed order:
  1. the safety reflex — evaluated here FIRST, unconditionally, from the current state
     (a defence in depth: even if a caller forgot to run ``evaluate_gates`` after its
     state update, reassess re-runs it; there is no path to a terminal that bypasses it);
  2. no matched fault / no KB procedure → defer_to_TLC (§5.6);
  3. ordinary checks incomplete → ask_step(next_unmet) — the ONE specific missed check,
     carrying hold_action if the pilot said they intend the gated action (fix D);
  4. everything (incl. the gated step) claimed done → confirm + KB follow-up guidance.

The loop-guard helpers (max-iter, no-progress, idempotency) are defined here and unit
tested, but only exercised by the agent loop in M3.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from engine import terminals as T
from engine.diff import StepDelta, diff_steps
from engine.gates import GateVerdict, Outcome, evaluate_gates
from engine.state import ACTION_RESET_QLM, DiagnosisState
from kb.schema import Fault

# ---------------------------------------------------------------------------
# loop guards (§6) — constants + helpers; exercised in M3
# ---------------------------------------------------------------------------
MAX_ITER = 6  # hard cap on agent_decide → execute_tool cycles per pilot turn


def max_iter_reached(state: DiagnosisState, cap: int = MAX_ITER) -> bool:
    return state.iter_count >= cap


def no_progress(before: tuple, after: tuple) -> bool:
    """True when a cycle left the diagnostic state unchanged (use ``state.snapshot()``)."""
    return before == after


def tool_result_cached(state: DiagnosisState, tool_name: str, args_key: Any = None) -> bool:
    """Idempotency: a tool whose fresh result is already in state must not re-run."""
    return (tool_name, args_key) in state.tool_results


Route = Literal["gate_terminal", "need_pilot_input", "terminal"]


@dataclass(frozen=True)
class Decision:
    route: Route
    terminal: T.Terminal
    verdict: GateVerdict          # the reflex result that was in force
    delta: Optional[StepDelta]    # the diff, when one was computed


# ---------------------------------------------------------------------------
# the router
# ---------------------------------------------------------------------------

def _gate_terminal(fault: Fault, verdict: GateVerdict) -> T.Terminal:
    step = fault.step(verdict.step_id)  # type: ignore[arg-type]
    if verdict.outcome is Outcome.REFUSE:
        return T.refuse(fault, step, verdict.message, verdict.reasons, source=verdict.source)
    if verdict.outcome is Outcome.CAUTION:
        return T.caution(fault, step, verdict.message, conditional=verdict.conditional)
    if verdict.outcome is Outcome.ASK:
        return T.ask_history(fault, step, verdict.question or verdict.message)
    raise AssertionError(f"unexpected fired verdict {verdict.outcome}")


def reassess(state: DiagnosisState, fault: Optional[Fault]) -> Decision:
    # 1. REFLEX. Always. Not optional, not the agent's call. (§5.1)
    verdict = evaluate_gates(state, fault)
    if verdict.fired:
        assert fault is not None
        return Decision("gate_terminal", _gate_terminal(fault, verdict), verdict, None)

    # 2. nothing matched → never guess.
    if fault is None or state.matched_fault != fault.fault_id:
        return Decision("terminal", T.defer_to_TLC("This isn't in my procedure set."), verdict, None)

    # 3. the delta over the ordinary checks.
    delta = diff_steps(state, fault)
    if not delta.complete:
        assert delta.next_unmet is not None
        step = fault.step(delta.next_unmet)
        state.stuck_at = step.id
        hold = ACTION_RESET_QLM if state.intended_action == ACTION_RESET_QLM else None
        return Decision(
            "need_pilot_input",
            T.ask_step(fault, step, hold_action=hold, unrecognised=delta.unrecognised),
            verdict,
            delta,
        )

    # 4. checks complete and the reflex did not fire. With the QLM gate that means the
    #    reset is already claimed done with history == no (rule 6) → confirm, adding the
    #    KB's follow-up (monitor / log / TLC) as guidance.
    state.stuck_at = None
    guidance = tuple(
        s.gate.on_first_reset for s in fault.gated_steps
        if s.gate and s.gate.type == "reset_limit" and s.gate.on_first_reset
    )
    src = "; ".join(s.gate.source for s in fault.gated_steps if s.gate) or fault.source
    return Decision("terminal", T.confirm(fault, guidance=guidance, source=src), verdict, delta)
