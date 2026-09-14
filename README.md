# Loco Troubleshooting Verification Copilot

A cab-side verification copilot for **conventional AC locomotives** (WAG-5, WAG-7, WAP-1,
WAP-4, WAM-4) — locos that emit no fault codes. The pilot describes the fault and what
they have already done; the copilot verifies that against the railway's troubleshooting
directory, trusts completed steps, flags only what was missed, and **refuses unsafe
actions** (e.g. a forbidden second QLM reset) via a deterministic safety reflex that no
language model can skip or override.

> **Safety disclaimer.** This is a demonstrator, not a certified system. It is not
> approved for operational use by Indian Railways or anyone else. Real deployment would
> require IR approval, safety certification and liability review. Nothing here replaces
> the printed troubleshooting directory, the TLC, or the loco pilot's judgement.

## Knowledge source

All procedure content is encoded from a single public document and nothing else:

**SCR/ETTC Operating Manual & Trouble Shooting Directory (Rev-2, 2019)**
<https://scr.indianrailways.gov.in/cris//uploads/files/1566969531009-ETTC_TSD.pdf>

Procedures are encoded as *facts* (step logic, gates, terminals) in `kb/faults/*.yaml`;
every file and every step cites its TSD section. The schema (`kb/schema.py`) enforces
this in CI. The PDF itself is DVC-tracked (`1566969531009-ETTC_TSD.pdf.dvc`) and kept
out of git.

## Status — Milestone 1 (deterministic engine, no LLM)

| Piece | Where |
|---|---|
| Fault knowledge base (QLM only, depth-first) | `kb/faults/qlm_dropped.yaml` |
| KB schema + `validate_kb` CI check | `kb/schema.py` |
| KB loader + alias match (LLM match stubbed for M2) | `engine/matcher.py` |
| Conversation state (`DiagnosisState`) | `engine/state.py` |
| Claimed-vs-required delta | `engine/diff.py` |
| **Safety reflex** — deterministic, runs after every state update | `engine/gates.py` |
| Router + loop-guard helpers | `engine/reassess.py` |
| Terminals (confirm / ask / caution / refuse / defer) | `engine/terminals.py` |
| M1 single-pass driver | `engine/run_turn.py` |
| Tests (46) | `tests/` |

Design spec: `BUILD_PLAN.md`. Working notes and locked decisions: `HANDOFF.md`.

## Run

```bash
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -r requirements.txt
python -m kb.schema      # validate the knowledge base
python -m pytest         # engine + reflex tests
dvc pull                 # (once a DVC remote is configured) fetch the TSD PDF
```
