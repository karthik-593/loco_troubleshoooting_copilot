"""Deterministic router (BUILD_PLAN §4.2 ``reassess``) + loop guards (§6).

Routing, in this fixed order:
  1. the safety reflex — evaluated here FIRST, unconditionally, from the current state
     (a defence in depth: even if a caller forgot to run ``evaluate_gates`` after its
     state update, reassess re-runs it; there is no path to a terminal that bypasses it);
  2. no matched fault / no KB procedure → defer_to_TLC (§5.6);
  2b. matched fault carries a hard gate and is not yet confirmed → confirm_fault (§5.5):
     a misparse must not route the pilot into a gated procedure. (A REFUSE from step 1
     still fires first — refusing is always safe.)
  2c. pilot reports the fault cleared (history fault_resolved == yes) → confirm with the
     KB's `resolved` terminal action (gate-free faults such as sanders end this way at
     whichever step cleared it — §10.12). Gated faults reach here only after the reflex.
  2d. a KB `defer_conditions` fact is yes ("if <situation>, contact TLC") → defer_to_TLC
     with that clause's text.
  2e. gate-free fault with an isolate-then-reset step: abnormality found and isolation
     unstated → ask; failed → defer_to_TLC (the clause's own text). (For gated faults the
     reset_limit evaluator does this inside the reflex.)
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
from engine.gates import GateVerdict, Outcome, evaluate_gates, isolation_status
from engine.state import HF_RESET_INSTRUCTED, HF_RESOLVED, DiagnosisState
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
        if verdict.outcome is Outcome.CAUTION and verdict.gate_type == "reset_limit":
            # The engine has told the pilot to reset: from now on a re-presentation of the
            # fault is a recurrence (§6.1.1(f)(ii)) — see update_state's backstop.
            state.history_facts[HF_RESET_INSTRUCTED] = "yes"
        return Decision("gate_terminal", _gate_terminal(fault, verdict), verdict, None)

    # 2. nothing matched → never guess.
    if fault is None or state.matched_fault != fault.fault_id:
        return Decision("terminal", T.defer_to_TLC("This isn't in my procedure set."), verdict, None)

    # 2b. §5.5 — hard-gated fault must be confirmed before any guidance.
    if fault.gated_steps and not state.fault_confirmed:
        return Decision("need_pilot_input", T.confirm_fault(fault), verdict, None)

    # 2c. fault cleared → confirm + the KB's 'resolved' action. On a reset-gated fault the
    #     permitted reset's follow-up conditions (monitoring, log book, TLC) always ride along.
    if state.history(HF_RESOLVED) == "yes":
        state.stuck_at = None
        guidance = tuple(g for g in (fault.terminal_actions.get("resolved"),) if g)
        guidance += tuple(s.gate.on_first_reset for s in fault.gated_steps
                          if s.gate and s.gate.type == "reset_limit" and s.gate.on_first_reset
                          and s.id in state.steps_claimed_done)
        return Decision("terminal", T.confirm(fault, guidance=guidance, source=fault.source), verdict, None)

    # 2d. "if <situation>, contact TLC" clauses.
    for dc in fault.defer_conditions:
        if state.history(dc.fact) == "yes":
            state.stuck_at = None
            t = T.defer_to_TLC(dc.text, fault_id=fault.fault_id)
            return Decision("terminal", T.Terminal(**{**t.__dict__, "message": dc.text, "source": dc.source}),
                            verdict, None)

    # 2e. isolate-then-reset on a gate-free fault.
    if not fault.gated_steps:
        pending, failed, _ = isolation_status(state, fault)
        if failed is not None:
            t = T.defer_to_TLC(failed.isolation.on_not_isolated, fault_id=fault.fault_id)
            return Decision("terminal", T.Terminal(**{**t.__dict__, "message": f"{failed.on_abnormality or ''} "
                            f"{failed.isolation.on_not_isolated}".strip(), "source": failed.isolation.source}),
                            verdict, None)
        if pending is not None:
            q = T.Terminal(kind="ask_history", fault_id=fault.fault_id,
                           message="Were you able to isolate the abnormal equipment?",
                           source=pending.isolation.source, step_id=pending.id)
            return Decision("need_pilot_input", q, verdict, None)

    # 3. the delta over the ordinary checks.
    delta = diff_steps(state, fault)
    if delta.needs_axis:                      # §2.4: the NEXT branch depends on an unknown loco axis
        return Decision("need_pilot_input", T.ask_config(fault, delta.needs_axis), verdict, delta)
    hold = state.intended_action if state.intended_action and any(
        s.gate and (s.gate.action == state.intended_action or
                    (s.gate.type == "reset_limit" and state.intended_action == "reset_QLM"))
        for s in fault.gated_steps) else None
    if not delta.complete:
        assert delta.next_unmet is not None
        step = fault.step(delta.next_unmet)
        state.stuck_at = step.id
        return Decision(
            "need_pilot_input",
            T.ask_step(fault, step, hold_action=hold, unrecognised=delta.unrecognised),
            verdict,
            delta,
        )

    # 3b. the next step in order is a GATED step the reflex let through (NO_FIRE): its
    #     preconditions are met — ask the step itself.
    for s in fault.steps:
        if s.id in state.steps_claimed_done:
            continue
        if s.gate is not None:
            state.stuck_at = s.id
            return Decision("need_pilot_input", T.ask_step(fault, s, unrecognised=delta.unrecognised), verdict, delta)
        break

    # 4. checks complete and the reflex did not fire. With the QLM gate that means the
    #    reset is already claimed done with history == no (rule 6) → confirm, adding the
    #    KB's follow-up (monitor / log / TLC) as guidance.
    state.stuck_at = None
    guidance = tuple(
        s.gate.on_first_reset for s in fault.gated_steps
        if s.gate and s.gate.type == "reset_limit" and s.gate.on_first_reset
    )
    if not fault.gated_steps:
        last_done = next((s for s in reversed(fault.steps) if s.id in state.steps_claimed_done), None)
        if last_done is not None and last_done.completes and fault.terminal_actions.get("resolved"):
            guidance = (fault.terminal_actions["resolved"],)  # a sanctioned way onward
        elif fault.terminal_actions.get("unresolved"):
            guidance = (fault.terminal_actions["unresolved"],)   # every step tried, still not cleared
    src = "; ".join(s.gate.source for s in fault.gated_steps if s.gate) or fault.source
    return Decision("terminal", T.confirm(fault, guidance=guidance, source=src), verdict, delta)
