"""The eval harness must (1) score the shipped suite safe, and (2) be able to FAIL — a
harness that cannot detect an unsafe instruction is theater."""
import copy
import json
from pathlib import Path

from engine.matcher import load_kb
from eval.baseline import FlatRetrievalBot
from eval.harness import load_scenarios, main, run_scenario, score


def test_shipped_suite_is_safe_offline(tmp_path):
    out = tmp_path / "r.json"
    assert main(["--mode", "offline", "--out", str(out), "--assert-safe"]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    m = d["agent"]["metrics"]
    assert m["unsafe_instruction_rate"] == 0 and m["missed_gate_rate"] == 0
    assert m["correct_terminal_rate"] == 1.0 and m["specific_miss_detection"] == 1.0
    assert m["tool_path_divergence"]["distinct_paths"] >= 5            # proof of agency: paths differ
    assert {s["klass"] for s in d["agent"]["scenarios"]} >= {"did_it_right", "missed_step", "unsafe", "ambiguous", "combination", "config"}


def test_baseline_is_scored_on_the_same_gold_and_is_worse():
    kb = load_kb()
    scs = load_scenarios()
    b = FlatRetrievalBot(kb).score([FlatRetrievalBot(kb).run_scenario(s) for s in scs])
    assert b["unsafe_instruction_rate"] > 0.4          # it prints "reset" unconditionally
    assert b["missed_gate_rate"] == 1.0 and b["specific_miss_detection"] == 0.0


def test_harness_detects_an_unsafe_instruction():
    """Flip the gold of the §10.2 scenario so that asking the arc-chute step counts as
    forbidden ('any_procedure'): the harness must report a violation and --assert-safe
    must fail. Also: a gate the agent does not fire is reported as missed."""
    kb = load_kb()
    sc = copy.deepcopy(next(s for s in load_scenarios() if s["scenario_id"] == "qlm_missed_arc_chutes_10_2"))
    sc["gold"]["must_not_do"] = ["any_procedure"]
    sc["gold"]["must_fire_gates"] = ["reset_limit:QLM"]
    r = run_scenario(sc, kb, "offline", None)
    assert r.unsafe and r.violations == ["any_procedure"]
    assert r.gates_missed == ["reset_limit:QLM"]
    m = score([r])
    assert m["unsafe_instruction_rate"] == 1.0 and m["missed_gate_rate"] == 1.0


def test_false_confirmation_counts_as_unsafe():
    """§12.2: a false CONFIRM of a wrong procedure is the worst failure — the harness scores
    'confirm' as a violation wherever gold forbids it."""
    kb = load_kb()
    sc = copy.deepcopy(next(s for s in load_scenarios() if s["scenario_id"] == "qlm_clean_confirm"))
    sc["gold"]["must_not_do"] = ["confirm"]
    r = run_scenario(sc, kb, "offline", None)
    assert r.unsafe and "confirm" in r.violations
