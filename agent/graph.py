"""The agentic control loop — BUILD_PLAN §4, wired in LangGraph.

    parse (LLM) → update_state → REFLEX → [fired] → phrase
                                        ↘ agent_decide (LLM, bounded) → execute_tool → update_state
                                                 ↑                                        ↓
                                                 └──── reassess ◄──────── REFLEX (again) ─┘
                                                          ↓
                                                        phrase → END

Two properties are enforced by the graph's SHAPE, not by any node's good behaviour:

* **§5.1** — the reflex node is on every path out of a state update (after parse and after
  every tool). It is not a tool the agent can pick; ``agent_decide`` cannot reach ``phrase``
  except through it. If it fires, the loop short-circuits: ``agent_decide`` is never entered.
* **§6** — the ``agent_decide → execute_tool`` cycle is bounded: ``MAX_ITER`` cap, idempotent
  tools (a cached result counts as no progress), a no-progress detector on the state
  snapshot, and a bounded toolset (unregistered names are rejected, never attempted).
  Every guard degrades to the ENGINE's deterministic terminal via ``reassess`` — never to
  silence, never to model-invented content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from engine import terminals as T
from engine.gates import GateVerdict, evaluate_gates
from engine.matcher import KnowledgeBase, default_kb
from engine.reassess import MAX_ITER, max_iter_reached, no_progress, reassess
from engine.state import HF_OTHER_RELAYS, DiagnosisState, StateUpdate, resolve_combination, update_state
from engine.tools import run_tool
from kb.schema import Fault
from llm.decide import agent_decide
from llm.interface import Providers
from llm.parse import out_of_scope_reason, parse_turn
from llm.phrase import phrase

REFLEX_SHORT_CIRCUIT = "(reflex short-circuit)"


class GraphState(TypedDict, total=False):
    # persistent across turns (owned by the session; mutated in place)
    diag: DiagnosisState
    last_assistant: Optional[str]
    # per turn
    pilot_text: str
    update: Optional[StateUpdate]
    verdict: Optional[GateVerdict]
    terminal: Optional[T.Terminal]
    reply: Optional[str]
    phrase_fallback: bool
    tool_path: list[str]           # what the agent chose / what the reflex did — the trace
    reflex_runs: int               # how many times the reflex evaluated this turn
    stop_reason: Optional[str]     # why the loop ended: gate | none | max_iter | no_progress |
                                   #   rejected | reassess | clarify | reroute
    rejected_tools: list[str]
    snapshot_before_tool: Optional[tuple]
    last_tool_cached: bool
    rerouted: bool                 # reassess changed matched_fault → must pass the reflex again


@dataclass(frozen=True)
class TurnResult:
    reply: str
    terminal: T.Terminal
    tool_path: tuple[str, ...]
    reflex_runs: int
    stop_reason: str
    phrase_fallback: bool
    rejected_tools: tuple[str, ...] = field(default=())
    iterations: int = 0


class Copilot:
    """One graph, many sessions. Each session owns a DiagnosisState."""

    def __init__(self, providers: Providers, kb: Optional[KnowledgeBase] = None, max_iter: int = MAX_ITER):
        self.providers = providers
        self.kb = kb or default_kb()
        self.max_iter = max_iter
        self.graph = self._build()

    # ---- helpers ---------------------------------------------------------
    def _fault(self, diag: DiagnosisState) -> Optional[Fault]:
        fid = diag.matched_fault
        return self.kb.get(fid) if fid and fid in self.kb.fault_ids else None

    # ---- nodes -----------------------------------------------------------
    def parse_node(self, s: GraphState) -> GraphState:
        diag = s["diag"]
        r = parse_turn(s["pilot_text"], diag, self.kb, self.providers.parse, last_assistant=s.get("last_assistant"))
        if r.out_of_scope:                          # §5.6: refuse gracefully, never guess
            return {"update": None, "terminal": T.defer_to_TLC(out_of_scope_reason(self.kb)), "stop_reason": "out_of_scope"}
        if r.needs_clarification:
            diag.clarify_asked += 1                 # backstop: the next unresolved turn defers
            return {"update": None, "terminal": T.clarify(r.clarification or ""), "stop_reason": "clarify"}
        diag.clarify_asked = 0
        return {"update": r.update}

    def update_state_node(self, s: GraphState) -> GraphState:
        diag = s["diag"]
        upd = s.get("update")
        if upd is not None:
            fid = upd.fault_id or diag.matched_fault
            fault = self.kb.get(fid) if fid and fid in self.kb.fault_ids else None
            update_state(diag, upd, fault)
        # Deterministic identity resolution BEFORE the reflex (see engine.state.resolve_combination).
        rerouted = resolve_combination(diag, self._fault(diag),
                                       lambda f: self.kb.get(f) if f in self.kb.fault_ids else None)
        out: GraphState = {"update": None}
        if rerouted:
            out["tool_path"] = s.get("tool_path", []) + [f"(reroute→{rerouted})"]
        return out

    def reflex_node(self, s: GraphState) -> GraphState:
        """MANDATORY. Deterministic. Runs after every state update. Not an agent choice."""
        diag = s["diag"]
        verdict = evaluate_gates(diag, self._fault(diag))
        out: GraphState = {"verdict": verdict, "reflex_runs": s.get("reflex_runs", 0) + 1,
                           "rerouted": False}
        if verdict.fired:
            d = reassess(diag, self._fault(diag))      # re-derives the same verdict → terminal
            assert d.route == "gate_terminal"
            out.update({"terminal": d.terminal, "stop_reason": "gate",
                        "tool_path": s.get("tool_path", []) + [REFLEX_SHORT_CIRCUIT]})
        return out

    def agent_decide_node(self, s: GraphState) -> GraphState:
        diag = s["diag"]
        fault = self._fault(diag)
        if fault is None:                                  # nothing to decide about → engine terminal
            return {"stop_reason": "reassess"}
        if diag.fault_confirmed is False and fault.gated_steps:
            return {"stop_reason": "reassess"}             # §5.5: confirm first; no tools yet
        if max_iter_reached(diag, self.max_iter):
            return {"stop_reason": "max_iter"}
        choice = agent_decide(diag, fault, self.providers.decide)
        if choice.rejected:
            return {"stop_reason": "rejected",
                    "rejected_tools": s.get("rejected_tools", []) + [choice.rejected]}
        if choice.tool is None:
            return {"stop_reason": "none"}
        return {"tool_path": s.get("tool_path", []) + [choice.tool], "stop_reason": None,
                "snapshot_before_tool": diag.snapshot()}

    def execute_tool_node(self, s: GraphState) -> GraphState:
        diag = s["diag"]
        fault = self._fault(diag)
        assert fault is not None
        name = s["tool_path"][-1]
        res = run_tool(name, diag, fault)                  # idempotent; rejects unregistered
        diag.iter_count += 1
        return {"last_tool_cached": res.cached}

    def reassess_node(self, s: GraphState) -> GraphState:
        diag = s["diag"]
        fault = self._fault(diag)
        # §6 no-progress: the cycle produced nothing new — the tool's result was already in
        # state (idempotency hit) and the diagnostic snapshot did not change.
        if s.get("last_tool_cached") and no_progress(s["snapshot_before_tool"], diag.snapshot()):
            return {"stop_reason": "no_progress", "terminal": self._engine_terminal(diag, fault)}
        # combination reroute: the KB says this is a different fault
        comb = diag.tool_results.get("check_combination")
        if (comb and comb["result"].get("applies") and comb.get("_snapshot") == diag.snapshot()
                and comb["result"]["route_to"] != diag.matched_fault):
            route_to = comb["result"]["route_to"]
            if route_to in self.kb.fault_ids:            # normally already done by update_state
                diag.matched_fault = route_to               # a STATE UPDATE → back through the reflex
                diag.steps_required = list(self.kb.get(route_to).step_ids)
                return {"stop_reason": None, "rerouted": True,
                        "tool_path": s["tool_path"] + [f"(reroute→{route_to})"]}
            t = T.defer_to_TLC(
                f"{fault.fault_id.replace('_', ' ')} together with {', '.join(comb['result']['matched_relays'])} "
                f"is a different procedure ({route_to}) that is not in my set.",
                fault_id=fault.fault_id)
            t = T.Terminal(**{**t.__dict__, "source": comb["result"]["source"]})
            return {"stop_reason": "reroute", "terminal": t}
        # need another tool? — only if the agent hasn't got a fresh diff yet and relays were
        # reported without a combination check. Otherwise the engine can answer now.
        fresh = {k for k, v in diag.tool_results.items()
                 if isinstance(v, dict) and v.get("_snapshot") == diag.snapshot()}
        relays_pending = (HF_OTHER_RELAYS in diag.history_facts and diag.history_facts[HF_OTHER_RELAYS]
                          and "check_combination" not in fresh and bool(fault and fault.combination_rules))
        if "diff_completed_steps" in fresh and not relays_pending:
            return {"stop_reason": "reassess", "terminal": self._engine_terminal(diag, fault)}
        if relays_pending or "diff_completed_steps" not in fresh:
            return {"stop_reason": None}                   # loop back to agent_decide
        return {"stop_reason": "reassess", "terminal": self._engine_terminal(diag, fault)}

    def _engine_terminal(self, diag: DiagnosisState, fault: Optional[Fault]) -> T.Terminal:
        return reassess(diag, fault).terminal

    def terminal_node(self, s: GraphState) -> GraphState:
        """Guards / 'none' / §5.5 all land here: the ENGINE's deterministic terminal."""
        if s.get("terminal") is not None:
            return {}
        diag = s["diag"]
        return {"terminal": self._engine_terminal(diag, self._fault(diag))}

    def phrase_node(self, s: GraphState) -> GraphState:
        t = s["terminal"]
        assert t is not None
        r = phrase(t, self.providers.phrase)
        return {"reply": r.text, "phrase_fallback": r.used_fallback, "last_assistant": r.text}

    # ---- routing ---------------------------------------------------------
    @staticmethod
    def after_parse(s: GraphState) -> Literal["update_state", "phrase"]:
        return "phrase" if s.get("terminal") is not None else "update_state"

    @staticmethod
    def after_reflex(s: GraphState) -> Literal["phrase", "agent_decide"]:
        return "phrase" if s.get("terminal") is not None else "agent_decide"   # fired → short-circuit

    @staticmethod
    def after_reflex_tool(s: GraphState) -> Literal["phrase", "reassess"]:
        return "phrase" if s.get("terminal") is not None else "reassess"

    @staticmethod
    def after_decide(s: GraphState) -> Literal["execute_tool", "terminal"]:
        return "execute_tool" if s.get("stop_reason") is None else "terminal"

    @staticmethod
    def after_reassess(s: GraphState) -> Literal["agent_decide", "terminal", "reflex_after_tool"]:
        if s.get("rerouted"):
            return "reflex_after_tool"                     # §5.1: every state update is guarded
        return "agent_decide" if s.get("stop_reason") is None else "terminal"

    def _build(self):
        g = StateGraph(GraphState)
        g.add_node("parse", self.parse_node)
        g.add_node("update_state", self.update_state_node)
        g.add_node("reflex", self.reflex_node)
        g.add_node("agent_decide", self.agent_decide_node)
        g.add_node("execute_tool", self.execute_tool_node)
        g.add_node("reflex_after_tool", self.reflex_node)   # same function: the reflex, again
        g.add_node("reassess", self.reassess_node)
        g.add_node("terminal", self.terminal_node)
        g.add_node("phrase", self.phrase_node)

        g.add_edge(START, "parse")
        g.add_conditional_edges("parse", self.after_parse)
        g.add_edge("update_state", "reflex")
        g.add_conditional_edges("reflex", self.after_reflex)
        g.add_conditional_edges("agent_decide", self.after_decide)
        g.add_edge("execute_tool", "reflex_after_tool")     # every tool result is guarded
        g.add_conditional_edges("reflex_after_tool", self.after_reflex_tool)
        g.add_conditional_edges("reassess", self.after_reassess)
        g.add_edge("terminal", "phrase")
        g.add_edge("phrase", END)
        return g.compile()

    # ---- public ----------------------------------------------------------
    def turn(self, diag: DiagnosisState, pilot_text: str, last_assistant: Optional[str] = None) -> TurnResult:
        diag.iter_count = 0
        init: GraphState = {"diag": diag, "pilot_text": pilot_text, "last_assistant": last_assistant,
                            "tool_path": [], "reflex_runs": 0, "rejected_tools": [],
                            "snapshot_before_tool": None, "last_tool_cached": False, "rerouted": False,
                            "terminal": None, "stop_reason": None, "phrase_fallback": False}
        out = self.graph.invoke(init, config={"recursion_limit": 8 * (self.max_iter + 2)})
        return TurnResult(
            reply=out["reply"],
            terminal=out["terminal"],
            tool_path=tuple(out.get("tool_path", [])),
            reflex_runs=out.get("reflex_runs", 0),
            stop_reason=out.get("stop_reason") or "unknown",
            phrase_fallback=out.get("phrase_fallback", False),
            rejected_tools=tuple(out.get("rejected_tools", [])),
            iterations=diag.iter_count,
        )
