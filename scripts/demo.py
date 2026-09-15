"""Scripted demo walkthrough (BUILD_PLAN §14 M6): plays the showcase conversations through
the real system and writes the transcript, with the engine trace per turn, to
docs/walkthrough.md. Leads with the QLM second-reset refusal, then the recurrence path,
then a combination fault whose tool path differs from clean QLM.

    python -m scripts.demo            # live (needs the two credential env vars)
    python -m scripts.demo --offline  # engine-only replay with scripted parses (no credentials)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from agent.graph import Copilot
from engine.matcher import load_kb
from engine.state import DiagnosisState, LocoInfo
from eval.harness import PolicyDecider, ScriptedParse
from llm.interface import FakeProvider, Providers, providers_from_env

ORD = ["check_ht2_compartment", "check_oil_levels", "check_arc_chutes_and_terminals"]

SHOWCASE = [
    {
        "title": "1. The centrepiece — QLM second-reset refusal (BUILD_PLAN §10.2)",
        "why": "Turn 1: the engine trusts the two stated checks and asks only for the one it "
               "cannot see. Turn 2: the pilot volunteers a prior reset — the safety reflex "
               "fires BEFORE any tool is chosen and the agent is never consulted.",
        "locos": [LocoInfo("27312", "wag7", "unknown")],
        "turns": ["QLM dropped. I checked the transformer and oil level.",
                  "Checked, all normal. I reset QLM once earlier this trip."],
        "structured": [{"fault_guess": None, "fault_confidence": 0.0, "claimed_steps": ORD[:2], "fault_presenting": "yes"},
                       {"fault_guess": None, "fault_confidence": 0.0, "claimed_steps": [ORD[2]],
                        "abnormality_found": "no", "was_reset_earlier_this_trip": "yes", "intended_action": "reset_QLM"}],
    },
    {
        "title": "2. Recurrence — reset once per §6.1.1(d), then QLM re-locks",
        "why": "The permitted first reset is instructed with its monitoring conditions; the pilot "
               "reports it done and gets a confirmation; a terse 'QLM dropped' with no cue word "
               "is caught by the engine's own backstop as §6.1.1(f)(ii) and refused.",
        "locos": [LocoInfo("27312", "wag7", "unknown")],
        "turns": ["QLM locked, first time this trip. checked HT2, TFP/GR oil, arc chutes - all normal",
                  "reset done, resumed traction",
                  "QLM dropped"],
        "structured": [{"fault_guess": None, "fault_confidence": 0.0, "claimed_steps": ORD,
                        "abnormality_found": "no", "was_reset_earlier_this_trip": "no", "fault_presenting": "yes"},
                       {"fault_guess": None, "fault_confidence": 0.0, "claimed_steps": ["reset_decision"]},
                       {"fault_guess": None, "fault_confidence": 0.0}],
    },
    {
        "title": "3. Combination fault — a different tool path (proof of agency, §12.2)",
        "why": "QOP-1 reported with QLM: identity is resolved to the §6.1.2 procedure before the "
               "reflex, the pilot's three checks carry over, and the traction-circuit check is "
               "asked. RSI-1 smoking but isolated → the isolate-then-reset branch permits the reset.",
        "locos": [LocoInfo("22451", "wag5", "arno")],
        "turns": ["QLM locked and QOP-1 also dropped. checked ht2, tfp/gr oil, arc chutes - all normal",
                  "checked traction circuit, RSI-1 was smoking. isolated it successfully. not reset before this trip"],
        "structured": [{"fault_guess": None, "fault_confidence": 0.0, "claimed_steps": ORD, "abnormality_found": "no",
                        "other_relays_acted": ["QOP-1"], "fault_presenting": "yes"},
                       {"fault_guess": None, "fault_confidence": 0.0, "claimed_steps": ["check_traction_power_circuit"],
                        "was_reset_earlier_this_trip": "no",
                        "facts": {"traction_abnormality_found": "yes", "isolation_successful": "yes"}}],
    },
    {
        "title": "4. Hazard-exposure gate — pantograph roof work (§10.03 / §11.04)",
        "why": "The pilot's stated next move is the roof; the reflex fires a proactive caution "
               "naming both preconditions. Once the power block, earthing and grounding are "
               "stated, the engine asks the roof step itself.",
        "locos": [LocoInfo("30045", "wap4", "siv")],
        "turns": ["panto damaged by OHE, lowered it with ZPT 0, BP is fine, called TPC for power block. climbing on roof now",
                  "TPC gave power block, OHE staff earthed the wire both sides, I grounded loco with HOM"],
        "structured": [{"fault_guess": None, "fault_confidence": 0.0, "fault_presenting": "yes",
                        "claimed_steps": ["lower_pantograph_immediately", "check_bp_and_protect_train", "obtain_emergency_power_block"],
                        "intended_action": "work_on_roof"},
                       {"fault_guess": None, "fault_confidence": 0.0,
                        "facts": {"ohe_power_block_obtained_and_earthed": "yes", "loco_grounded": "yes"}}],
    },
    {
        "title": "5. Benign fault — sanders, no gates, no confirmation turn (§2.1 brevity)",
        "why": "A gate-free fault goes straight to the one missed check; the pilot's fix is "
               "trusted and the procedure closes on its resolved terminal.",
        "locos": [LocoInfo("27312", "wag7", "unknown")],
        "turns": ["sanders not working on both sides",
                  "pedal pressed, COCs were closed. opened them, sanders working now"],
        "structured": [{"fault_guess": "sanders_not_working", "fault_confidence": 0.9, "fault_presenting": "yes"},
                       {"fault_guess": None, "fault_confidence": 0.0, "claimed_steps": ["check_psa_and_sander_cocs"], "fault_resolved": "yes"}],
    },
]


def run(offline: bool) -> str:
    kb = load_kb()
    live = None if offline else providers_from_env()
    out = [f"# Walkthrough — {'offline replay (engine only)' if offline else 'live (DeepSeek parse/decide · Claude phrase)'}",
           "", "Generated by `python -m scripts.demo`. Each turn shows the pilot's message, the copilot's reply, "
           "and the engine trace: terminal kind, tool path, loop exit reason, reflex evaluations, TSD citation.", ""]
    for sc in SHOWCASE:
        out += [f"## {sc['title']}", "", f"_{sc['why']}_", "",
                f"Loco: {', '.join(f'{l.loco_number} ({l.type.upper()}, {l.config.upper()})' for l in sc['locos'])}", ""]
        if offline:
            decider = PolicyDecider()
            prov = Providers(parse=ScriptedParse(sc["structured"]), decide=decider, phrase=FakeProvider())
        else:
            decider = None
            prov = live
        cp = Copilot(prov, kb=kb)
        d = DiagnosisState(); d.set_locos(sc["locos"])
        last = None
        for text in sc["turns"]:
            if decider:
                decider.new_turn()
            t0 = time.time()
            r = cp.turn(d, text, last_assistant=last)
            last = r.reply
            out += [f"**Pilot:** {text}", "",
                    f"**Copilot:** {r.reply}", "",
                    f"> `{r.terminal.kind}`"
                    + (f" `{r.terminal.step_id}`" if r.terminal.step_id and r.terminal.kind == 'ask_step' else "")
                    + (f" reasons={list(r.terminal.reasons)}" if r.terminal.reasons else "")
                    + f" · path: `{' → '.join(r.tool_path) or '—'}` · stop: {r.stop_reason} · reflex×{r.reflex_runs}"
                    + (" · phrase fallback" if r.phrase_fallback else "")
                    + (f" · {time.time() - t0:.1f}s" if not offline else "")
                    + f"  \n> source: {r.terminal.source}", ""]
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default="docs/walkthrough.md")
    a = ap.parse_args(argv)
    text = run(a.offline)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\n→ {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
