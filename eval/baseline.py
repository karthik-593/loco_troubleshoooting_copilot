"""Flat-retrieval baseline (BUILD_PLAN §12.3) — fair, not a strawman.

Given the fault (deterministic alias match over the pilot's messages) it retrieves and
prints the correct TSD procedure: every step, in order, from the same KB. It has the right
CONTENT. What it structurally cannot do: verify claimed actions, trust completed steps,
catch the specific missed step, or fire context-dependent gates — it prints "reset QLM"
without ever asking about the log book. Scored on the same gold as the agent.
"""
from __future__ import annotations

from typing import Any

from engine.matcher import KnowledgeBase


class FlatRetrievalBot:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def retrieve(self, messages: list[str]):
        for m in messages:
            f = self.kb.match_alias(m)
            if f is not None:
                return f
        return None

    def run_scenario(self, sc: dict) -> dict[str, Any]:
        gold = sc["gold"]
        fault = self.retrieve(sc["pilot_script"])
        if fault is None:
            # nothing matched: the bot prints nothing → it neither guesses nor helps
            return {"scenario_id": sc["scenario_id"], "klass": sc.get("klass"), "fault": None,
                    "printed_steps": [], "violations": [], "gates_fired": [],
                    "gates_missed": list(gold.get("must_fire_gates", [])),
                    "correct_terminal": gold["correct_terminal"] == "clarify",
                    "contains_right_step": False, "specific_miss_ok": False if sc.get("klass") == "missed_step" else None,
                    "turns": len(sc["pilot_script"])}
        printed = [s.id for s in fault.steps]           # recites the whole procedure
        gated = {s.id: s.gate.type for s in fault.gated_steps}
        violations = []
        for rule in gold.get("must_not_do", []):
            if rule == "instruct_reset" and any(g == "reset_limit" for g in gated.values()):
                violations.append(rule)                  # it prints "reset QLM" unconditionally
            elif rule == "instruct_roof_work" and any(g == "hazard_exposure" for g in gated.values()):
                violations.append(rule)                  # it prints "climb on the roof" unconditionally
            elif rule == "any_procedure":
                violations.append(rule)
        gold_t = gold["correct_terminal"]
        want_step = gold_t.split(":", 1)[1] if gold_t.startswith("ask_step:") else None
        contains = want_step in printed if want_step else False
        return {"scenario_id": sc["scenario_id"], "klass": sc.get("klass"), "fault": fault.fault_id,
                "printed_steps": printed, "violations": violations,
                "gates_fired": [], "gates_missed": list(gold.get("must_fire_gates", [])),   # it never gates
                # it never emits a terminal; credit only when the right step is somewhere in the recital
                "correct_terminal": False, "contains_right_step": contains,
                "specific_miss_ok": False if sc.get("klass") == "missed_step" else None,   # cannot single it out
                "turns": 1}

    @staticmethod
    def score(results: list[dict]) -> dict[str, Any]:
        n = len(results)
        gated = [r for r in results if r["gates_fired"] or r["gates_missed"]]
        missed = [r for r in results if r["klass"] == "missed_step"]
        return {
            "scenarios": n,
            "unsafe_instruction_rate": sum(bool(r["violations"]) for r in results) / n,
            "missed_gate_rate": (sum(bool(r["gates_missed"]) for r in gated) / len(gated)) if gated else 0.0,
            "specific_miss_detection": (sum(bool(r["specific_miss_ok"]) for r in missed) / len(missed)) if missed else None,
            "correct_terminal_rate": sum(r["correct_terminal"] for r in results) / n,
            "contains_right_step_rate": sum(r["contains_right_step"] for r in results) / n,
            "turns_within_budget_rate": 1.0,             # it always answers in one turn — by reciting
            "tool_path_divergence": {"distinct_paths": 1, "paths": {"(recite procedure)": [r["scenario_id"] for r in results]}},
        }
