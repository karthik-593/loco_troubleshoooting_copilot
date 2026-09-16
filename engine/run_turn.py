"""M1 single-pass driver — the engine with NO LLM and NO agent.

    structured update → update_state → SAFETY REFLEX → (fired? terminal) : diff → reassess → terminal

This is what the structured-input scenario tests drive. In M3 the same nodes are wired
into the LangGraph loop; the reflex position (immediately after every state update)
must be preserved there exactly (BUILD_PLAN §4.1, §5.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.gates import GateVerdict, evaluate_gates
from engine.matcher import KnowledgeBase, default_kb
from engine.reassess import Decision, reassess
from engine.state import DiagnosisState, StateUpdate, resolve_combination, update_state
from engine.terminals import Terminal


@dataclass(frozen=True)
class TurnTrace:
    """What happened this turn, in graph-node order — for tests and (later) eval traces."""
    verdict_after_update: GateVerdict      # the reflex run right after update_state
    short_circuit: bool                    # True → agent_decide/diff were never reached
    decision: Decision
    tool_path: tuple[str, ...] = field(default=())   # M1: ("diff",) or ("(reflex short-circuit)",)

    @property
    def terminal(self) -> Terminal:
        return self.decision.terminal


def run_turn(state: DiagnosisState, update: StateUpdate, kb: KnowledgeBase | None = None) -> TurnTrace:
    kb = kb or default_kb()
    fault_id = update.fault_id or state.matched_fault
    fault = kb.get(fault_id) if fault_id and fault_id in kb.fault_ids else None

    update_state(state, update, fault)
    rerouted = resolve_combination(state, fault, lambda fid: kb.get(fid) if fid in kb.fault_ids else None)
    if rerouted:
        fault = kb.get(rerouted)

    # SAFETY REFLEX — mandatory, right after the state update (§5.1).
    verdict = evaluate_gates(state, fault)
    if verdict.fired:
        # Short-circuit: no diff, no tool, no agent. reassess re-derives the same
        # verdict and builds the terminal.
        decision = reassess(state, fault)
        # a CAUTION / ASK on a fault not yet confirmed waits behind the §5.5 confirm line
        assert decision.route == "gate_terminal" or decision.terminal.kind == "confirm_fault"
        path = (f"(reroute→{rerouted})", "(reflex short-circuit)") if rerouted else ("(reflex short-circuit)",)
        return TurnTrace(verdict, True, decision, tool_path=path)

    decision = reassess(state, fault)
    path = (f"(reroute→{rerouted})", "diff") if rerouted else ("diff",)
    return TurnTrace(verdict, False, decision, tool_path=path)
