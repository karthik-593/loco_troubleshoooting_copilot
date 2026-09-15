"""Eval report (BUILD_PLAN §12): agent-vs-baseline table on the §12.2 metrics, per-scenario
tool-path traces (proof of agency), and optional MLflow logging.

Usage:  python -m eval.report --in eval/out/results.json --out eval/out/report.md [--mlflow]
MLflow logs params (models, KB git SHA, scenario count, mode), the metrics, and the
results/report files as artifacts, to ./mlruns (git-ignored). Skipped without --mlflow so
CI and `dvc repro` stay dependency-light.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

METRICS = [
    ("unsafe_instruction_rate", "Unsafe-instruction rate (target 0)"),
    ("missed_gate_rate", "Missed-gate rate (target 0)"),
    ("specific_miss_detection", "Specific-miss detection"),
    ("correct_terminal_rate", "Correct-terminal rate"),
    ("turns_within_budget_rate", "Cleared within turn budget"),
    ("expected_tool_path_rate", "Expected tool path"),
    ("contains_right_step_rate", "Recites the right step (baseline only)"),
]


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def render(results: dict) -> str:
    a, b = results["agent"]["metrics"], results["baseline"]["metrics"]
    lines = [f"# Evaluation report — mode: {results['mode']}", "",
             f"{a['scenarios']} scripted-pilot scenarios · KB {_git_sha()}", "",
             "## Agent vs flat-retrieval baseline (same KB content, same gold)", "",
             "| Metric | Agent | Baseline |", "|---|---|---|"]
    for key, label in METRICS:
        lines.append(f"| {label} | {_fmt(a.get(key))} | {_fmt(b.get(key))} |")
    lines.append(f"| Distinct tool paths (proof of agency) | {a['tool_path_divergence']['distinct_paths']} | "
                 f"{b['tool_path_divergence']['distinct_paths']} |")
    lines += ["", "## Tool-path divergence (agent)", ""]
    for path, ids in a["tool_path_divergence"]["paths"].items():
        lines.append(f"- `{path}` — {', '.join(ids)}")
    lines += ["", "## Per-scenario traces", "",
              "| Scenario | Class | Final terminal | Tool path | Gates fired | Safe |", "|---|---|---|---|---|---|"]
    for r in results["agent"]["scenarios"]:
        lines.append(f"| {r['scenario_id']} | {r['klass']} | `{r['final_terminal']}` | "
                     f"`{' → '.join(r['tool_path']) or '—'}` | {', '.join(r['gates_fired']) or '—'} | "
                     f"{'✗ ' + ', '.join(r['violations']) if r['unsafe'] else '✓'} |")
    lines += ["", "## Honest notes", "",
              "- Clean linear faults take a single-tool path (`diff_completed_steps`); the agency is load-bearing "
              "in the branching cases — combination reroute, reflex short-circuits, resolved faults — as the "
              "distinct-paths count shows. Where the agent merely ties the baseline (it recites the right step "
              "somewhere), that is reported, not hidden.",
              "- The baseline's unsafe-instruction rate is not a strawman: it prints the correct procedure, "
              "which contains \"reset\" / \"climb on the roof\" unconditionally — the flat bot cannot ask about "
              "the log book or the power block.",
              "- Offline mode replays scripted parses (the engine's guarantees); live mode adds the real "
              "DeepSeek parse/decide and Claude phrase and is the number to quote for the whole system."]
    return "\n".join(lines) + "\n"


def log_mlflow(results: dict, report_path: Path, results_path: Path) -> str:
    import os
    # Local file store (git-ignored ./mlruns). MLflow 3 keeps it behind an opt-in flag and
    # mlflow-skinny has no sqlite backend; a file store is exactly right for a local eval log.
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow
    from llm.interface import ANTHROPIC_MODEL, DEEPSEEK_MODEL
    mlflow.set_tracking_uri(f"file:{(Path.cwd() / 'mlruns').as_posix()}")
    mlflow.set_experiment("loco-copilot-eval")
    with mlflow.start_run(run_name=f"eval-{results['mode']}") as run:
        a = results["agent"]["metrics"]
        mlflow.log_params({"mode": results["mode"], "kb_git_sha": _git_sha(), "scenarios": a["scenarios"],
                           "parse_decide_model": DEEPSEEK_MODEL, "phrase_model": ANTHROPIC_MODEL,
                           "max_iter": 6, "parse_confidence_threshold": 0.6})
        for key, _ in METRICS:
            if a.get(key) is not None:
                mlflow.log_metric(f"agent_{key}", float(a[key]))
            if results["baseline"]["metrics"].get(key) is not None:
                mlflow.log_metric(f"baseline_{key}", float(results["baseline"]["metrics"][key]))
        mlflow.log_metric("agent_distinct_tool_paths", a["tool_path_divergence"]["distinct_paths"])
        mlflow.log_artifact(str(report_path))
        mlflow.log_artifact(str(results_path))
        return run.info.run_id


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="eval/out/results.json")
    ap.add_argument("--out", default="eval/out/report.md")
    ap.add_argument("--metrics-out", default="eval/out/metrics.json")
    ap.add_argument("--mlflow", action="store_true")
    a = ap.parse_args(argv)
    results = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(render(results), encoding="utf-8")
    Path(a.metrics_out).write_text(json.dumps(
        {"agent": {k: v for k, v in results["agent"]["metrics"].items() if k != "tool_path_divergence"},
         "baseline": {k: v for k, v in results["baseline"]["metrics"].items() if k != "tool_path_divergence"},
         "agent_distinct_tool_paths": results["agent"]["metrics"]["tool_path_divergence"]["distinct_paths"]},
        indent=2), encoding="utf-8")
    print(f"report → {a.out}; metrics → {a.metrics_out}")
    if a.mlflow:
        print("mlflow run:", log_mlflow(results, Path(a.out), Path(a.inp)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
