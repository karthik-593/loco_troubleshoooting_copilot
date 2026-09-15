"""parse — pilot free text → StateUpdate (+ confidence). LLM job 1 of 3 (BUILD_PLAN §8).

Order of precedence, deterministic first:
1. exact alias match (engine/matcher) → fault matched AND confirmed, no model involved
   for the fault itself (the model still extracts claims / history);
2. otherwise the model guesses the fault from the KB's fault list, with a confidence:
   * below ``CLARIFY_THRESHOLD`` → no update; ask a one-line clarification (§8);
   * else the fault is set but ``fault_confirmed=False`` — a hard-gated fault must be
     confirmed by the pilot before any guidance (§5.5; engine/reassess step 2b).

Everything the model returns is validated against the KB: unknown fault → dropped;
claimed step ids not in the checklist → rejected and surfaced (§8, "never silently
accepted"). The model's output never enters state unvalidated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from engine.matcher import KnowledgeBase
from engine.state import (
    HF_ABNORMALITY,
    HF_RECURRED,
    HF_RESET_EARLIER,
    HF_RESET_INSTRUCTED,
    HF_RESET_PERFORMED,
    HF_RESOLVED,
    DiagnosisState,
    StateUpdate,
)
from engine.tools import HF_OTHER_RELAYS
from llm.interface import LLMProvider
from llm.schemas import ParseOutput

PROMPT_PATH = Path(__file__).with_name("prompts") / "parse.md"
CLARIFY_THRESHOLD = 0.6

CLARIFY_QUESTION = ("I didn't catch which fault this is. Which relay target has dropped — or what "
                    "exactly is wrong — and what have you checked so far?")


@dataclass(frozen=True)
class ParseResult:
    update: Optional[StateUpdate]          # None → do not act
    confidence: float
    needs_clarification: bool = False
    clarification: Optional[str] = None
    raw: Optional[ParseOutput] = None
    rejected_steps: tuple[str, ...] = field(default=())   # model claims not in the KB
    out_of_scope: bool = False             # §5.6: a real problem, not in the procedure set → defer


def _fault_name(fault_id: str) -> str:
    return fault_id.replace("_", " ")


def out_of_scope_reason(kb: KnowledgeBase) -> str:
    """§5.6 wording. Lists what IS covered so the pilot is not left guessing; no procedure content."""
    return ("This isn't in my procedure set. I can verify: "
            + ", ".join(_fault_name(f) for f in kb.fault_ids) + ". Refer to the TSD for it.")


def _unresolved(out: ParseOutput, state: DiagnosisState, kb: KnowledgeBase) -> ParseResult:
    """No fault resolved this turn. Out-of-scope (§5.6) if the pilot described a concrete
    problem clearly outside the list — or the model named one outside it — OR if
    a clarification was already asked and still nothing resolves (deterministic backstop:
    the same question is never asked twice). Otherwise a single clarify (§8)."""
    named_outside = bool(out.fault_guess) and out.fault_guess not in kb.fault_ids
    if (out.fault_guess is None and out.problem_outside_list == "yes") or named_outside or state.clarify_asked >= 1:
        return ParseResult(None, out.fault_confidence, False, None, out, out_of_scope=True)
    return ParseResult(None, out.fault_confidence, True, CLARIFY_QUESTION, out)


# ---------------------------------------------------------------------------
# prompt assembly — the KB vocabulary the model is allowed to map onto
# ---------------------------------------------------------------------------

def kb_vocabulary(kb: KnowledgeBase) -> str:
    lines = ["Faults (fault_id — aliases — presenting signs):"]
    for fid in kb.fault_ids:
        f = kb.get(fid)
        lines.append(f"- {fid} — {', '.join(f.aliases)} — {'; '.join(f.presenting_signs)}")
    lines.append("")
    lines.append("Checklist step ids (per fault; claim only if the pilot says it was done):")
    for fid in kb.fault_ids:
        f = kb.get(fid)
        for s in f.ordinary_steps:
            hint = _CLAIM_HINTS.get(s.id)
            lines.append(f"- {fid}.{s.id}: {s.text}" + (f"  [pilot may say: {hint}]" if hint else ""))
        for s in f.gated_steps:
            lines.append(f"- {fid}.{s.id}: (gated) the pilot says they already reset / performed the gated action")
    lines.append("")
    owners: dict[str, list[str]] = {}
    for fid in kb.fault_ids:
        for k in kb.get(fid).history_keys:
            if k not in (HF_RESET_EARLIER, HF_ABNORMALITY):
                owners.setdefault(k, []).append(fid)
    if owners:
        lines.append("Fault-specific facts (return under 'facts' only when the pilot states them; "
                     "each is valid only for the fault(s) in brackets):")
        for k in sorted(owners):
            lines.append(f"- {k} [{', '.join(owners[k])}]: " + _FACT_HINTS.get(k, "yes/no as stated by the pilot"))
        lines.append("")
    lines.append("Actions (intended_action): reset_QLM = about to reset the QLM relay target; "
                 "work_on_roof = about to climb on to the loco roof (pantograph work)")
    return "\n".join(lines)


# How pilots loosely refer to a step (mapping guidance only, not procedure text).
_CLAIM_HINTS = {
    "check_ht2_compartment": "checked the transformer / TFP / HT2 / HT-2 compartment / explosion vent / no smoke",
    "check_oil_levels": "oil ok / oil level normal / checked TFP and GR oil",
    "check_arc_chutes_and_terminals": "checked arc chutes / CGRs / RGR / terminals / bushings / HT cable",
    "check_traction_power_circuit": "checked traction circuit / RSI / line contactors / SLs / traction motors",
    "check_auxiliary_power_circuit": "checked aux circuit / ARNO / aux motors / CHBA / cab heaters",
    "check_psa_and_sander_cocs": "pressed pedal / PSA / checked or opened sander COCs",
    "check_lamps_and_ccls": "pressed BPT / LSP and LSRSI lamps / checked CCLS fuse / VESA energising",
    "lower_pantograph_immediately": "lowered panto / ZPT on 0 / pressed BPEMS / panto down",
    "check_bp_and_protect_train": "BP ok / BP fine / BP not dropped / checked BP / flasher on, ALP sent to protect",
    "obtain_emergency_power_block": "called TPC / asked for power block / emergency telephone",
    "secure_damaged_pantograph_on_roof": "climbed on roof / tied the panto / secured with rope",
    "earth_damaged_pantograph_hpt": "HPT in earthing clip / earthed the panto",
    "check_traction_circuit_1": "checked RSI-1 / TM1-3 / SL-1 / L1-L3 / circuit-1 / traction circuit",
    "try_hmcs1_positions_if_frequent": "tried HMCS-1 positions 2, 3, 4",
    "isolate_tm_of_bad_hmcs1_position": "isolated TM1 / TM2 / TM3 / that traction motor",
    "isolate_truck_1_if_all_positions": "isolated truck 1 / HVSI-1 HVMT-1 HVSL-1 on 0",
    "check_traction_circuit_2": "checked RSI-2 / TM4-6 / SL-2 / L4-L6 / circuit-2 / traction circuit",
    "try_hmcs2_positions_if_frequent": "tried HMCS-2 positions 2, 3, 4",
    "isolate_tm_of_bad_hmcs2_position": "isolated TM4 / TM5 / TM6 / that traction motor",
    "isolate_truck_2_if_all_positions": "isolated truck 2 / HVSI-2 HVMT-2 HVSL-2 on 0",
    "open_dj_lower_panto_hba_off_flasher_stop": "opened DJ / lowered panto / HBA off / flasher on / stopped the train",
    "take_extinguisher_to_equipment": "took the extinguisher to the equipment / wet cloth",
    "direct_jet_at_base_of_fire": "used the extinguisher / sprayed the base of the fire",
    "use_remaining_extinguishers_if_needed": "used a second / all the extinguishers",
    "isolate_equipment_inform_tlc_work_onwards": "isolated the equipment / informed TLC / working onwards",
    "logbook_remark": "made the log book remark",
}

# Plain-language hints for KB-declared fact keys (mapping guidance only, not procedure text).
_FACT_HINTS = {
    "traction_abnormality_found": "abnormality (smoke/smell/fire/heat/damage) found in the TRACTION power circuit equipment",
    "aux_abnormality_found": "abnormality found in the AUXILIARY power circuit equipment",
    "isolation_successful": "the pilot tried to isolate the abnormal equipment: 'yes' if isolation succeeded, 'no' if it could not be isolated",
    "arc_chute_terminal_abnormality": "the abnormality (smell/smoke/fire/red-hot/oil leak) was found in the arc chutes, RGR/RPGR, TFR terminals, bushings, HT cable, breathers, drain plug or oil trap box",
    "fault_recurred": "(use the top-level fault_recurred field instead)",
    "traction1_abnormality_found": "abnormality (smoke/smell/fire/heat/damage) found in traction power circuit-1 equipment (RSI-1, J1, SL-1, L1-L3, TM1-3, AM3 shunt, Q20/RQ20, QD1, SJ1-3, TFR terminals)",
    "ohe_power_block_obtained_and_earthed": "OHE/TRD staff have obtained the emergency power block AND earthed the contact wire on both sides of the loco",
    "loco_grounded": "the loco has been grounded (HOM operated)",
    "pantograph_not_lowered": "the pantograph did NOT lower when ZPT was put on 0 / BPEMS pressed",
    "both_pantographs_damaged": "BOTH pantographs are damaged",
    "load_and_road_do_not_permit": "the pilot says the load and road do not permit working onwards (with the load restriction)",
    "drops_after_long_interval": "the relay (QRSI-1/2) dropped again only after a LONG interval of running ('no' if it dropped again soon / repeatedly)",
    "drops_frequently": "the relay (QRSI-1/2) is dropping frequently / repeatedly / soon after each reset ('no' if only after a long interval)",
    "drops_in_particular_hmcs1_position": "with HMCS-1 tried in positions 2, 3, 4: it drops only in ONE particular position ('no' if in all)",
    "drops_in_all_hmcs1_positions": "with HMCS-1 tried in positions 2, 3, 4: it drops in ALL positions ('no' if only in one)",
    "traction2_abnormality_found": "abnormality (smoke/smell/fire/heat/damage) found in traction power circuit-2 equipment (RSI-2, J2, SL-2, L4-L6, TM4-6, AM4 shunt, RU5/RU6, QD-2, SJ4-6, TFR terminals)",
    "drops_in_particular_hmcs2_position": "with HMCS-2 tried in positions 2, 3, 4: it drops only in ONE particular position ('no' if in all)",
    "drops_in_all_hmcs2_positions": "with HMCS-2 tried in positions 2, 3, 4: it drops in ALL positions ('no' if only in one)",
    "fire_uncontrollable": "the pilot says the fire cannot be put out / is out of control / extinguishers exhausted and still burning",
}


def system_prompt(kb: KnowledgeBase) -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").replace("{kb_vocabulary}", kb_vocabulary(kb))


def user_prompt(text: str, state: DiagnosisState, last_assistant: Optional[str]) -> str:
    ctx = []
    if state.matched_fault:
        ctx.append(f"Current matched fault: {state.matched_fault} "
                   f"({'confirmed' if state.fault_confirmed else 'NOT yet confirmed'})")
    if state.steps_claimed_done:
        ctx.append(f"Steps already claimed: {sorted(state.steps_claimed_done)}")
    if last_assistant:
        ctx.append(f"Assistant's last message: {last_assistant}")
    if state.history(HF_RESET_PERFORMED) == "yes" or state.history(HF_RESET_INSTRUCTED) == "yes":
        ctx.append("Context: a first reset of this relay has already been performed or instructed in "
                   "this session. If the pilot now reports the relay has acted / dropped / locked "
                   "again, set fault_recurred = yes and fault_presenting = yes. If they merely report "
                   "that instructed reset as done ('reset done', 'resumed'), claim the reset step and "
                   "leave was_reset_earlier_this_trip unknown — that field means a reset BEFORE this "
                   "conversation.")
    ctx.append(f"Pilot's message: {text}")
    return "\n".join(ctx)


# ---------------------------------------------------------------------------
# validation against the KB
# ---------------------------------------------------------------------------

def _yn(v: str) -> Optional[str]:
    return None if v == "unknown" else v


def _validate(out: ParseOutput, fault_id: Optional[str], kb: KnowledgeBase
              ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the model's claimed steps into (accepted, rejected) against the KB."""
    if not fault_id or fault_id not in kb.fault_ids:
        return (), tuple(out.claimed_steps)
    known = set(kb.get(fault_id).step_ids)
    accepted, rejected = [], []
    for s in out.claimed_steps:
        sid = s
        for fid in kb.fault_ids:                       # model may prefix with ANY fault id
            if s.startswith(fid + "."):
                sid = s[len(fid) + 1:]
                break
        (accepted if sid in known else rejected).append(sid)
    return tuple(accepted), tuple(rejected)


def same_family(kb: KnowledgeBase, a: Optional[str], b: Optional[str]) -> bool:
    """Same relay family: identical, or one is a combination-rule reroute target of the other
    (QLM_dropped ↔ QLM_with_QOP_QRSI). A re-presentation of QLM after a reroute is a
    recurrence of the RESOLVED fault, not a new QLM_dropped."""
    if not a or not b:
        return False
    if a == b:
        return True
    routes = lambda f: {r.route_to for r in kb.get(f).combination_rules} if f in kb.fault_ids else set()
    return b in routes(a) or a in routes(b)


def parse_turn(text: str, state: DiagnosisState, kb: KnowledgeBase, provider: LLMProvider,
               last_assistant: Optional[str] = None) -> ParseResult:
    # 1. deterministic alias match — no model needed for the fault.
    alias_hit = kb.match_alias(text)

    out = provider.structured(system_prompt(kb), user_prompt(text, state, last_assistant), ParseOutput)

    # 2. resolve the fault: alias > current state > model guess (validated).
    if alias_hit is not None:
        fault_id, confirmed, confidence = alias_hit.fault_id, True, 1.0
        if same_family(kb, alias_hit.fault_id, state.matched_fault):
            fault_id = state.matched_fault          # keep the resolved (rerouted) identity
    elif state.matched_fault:
        fault_id, confirmed, confidence = state.matched_fault, None, 1.0
        if out.confirms_fault == "yes":
            confirmed = True
        elif out.confirms_fault == "no":
            # pilot says it is NOT that fault: drop it; fall through to a model guess
            guess = out.fault_guess if out.fault_guess in kb.fault_ids else None
            if guess and guess != state.matched_fault:
                fault_id, confirmed, confidence = guess, False, out.fault_confidence
            else:
                return _unresolved(out, state, kb)
    else:
        guess = out.fault_guess if out.fault_guess in kb.fault_ids else None
        if guess is None or out.fault_confidence < CLARIFY_THRESHOLD:
            return _unresolved(out, state, kb)
        fault_id, confirmed, confidence = guess, False, out.fault_confidence

    accepted, rejected = _validate(out, fault_id, kb)

    history: dict = {}
    if _yn(out.abnormality_found):
        history[HF_ABNORMALITY] = out.abnormality_found
    if _yn(out.was_reset_earlier_this_trip):
        history[HF_RESET_EARLIER] = out.was_reset_earlier_this_trip
    if out.other_relays_acted is not None:
        history[HF_OTHER_RELAYS] = [r.upper() for r in out.other_relays_acted]
    if _yn(out.fault_resolved):
        history[HF_RESOLVED] = out.fault_resolved
    if out.fault_recurred == "yes":                 # parser fast path; the engine backstop is primary
        history[HF_RECURRED] = "yes"
    # KB-declared keys of the resolved fault's family only (the fault + its reroute targets);
    # a key belonging to another fault is dropped, never re-mapped.
    allowed = {k for fid in kb.fault_ids if same_family(kb, fid, fault_id) for k in kb.get(fid).history_keys}
    for k, v in out.facts.items():
        if k in allowed and _yn(v):
            history[k] = v

    # Does this message PRESENT the fault (relay acted now)? Deterministic on an alias hit —
    # every alias is a presenting-sign phrase — else a confident model guess that says so.
    presenting = alias_hit is not None or (
        out.fault_presenting == "yes"
        and out.fault_confidence >= CLARIFY_THRESHOLD
        and same_family(kb, out.fault_guess, fault_id)
    )

    update = StateUpdate(
        fault_id=fault_id if fault_id != state.matched_fault else None,
        fault_confirmed=confirmed,
        config=out.loco_config,
        loco_type=out.loco_type,
        claimed_steps=accepted + rejected,   # rejected ones are surfaced by update_state/diff
        history=history,
        intended_action=out.intended_action,
        fault_presenting=presenting,
    )
    return ParseResult(update, confidence, False, None, out, rejected_steps=rejected + tuple(out.unmapped_claims))
