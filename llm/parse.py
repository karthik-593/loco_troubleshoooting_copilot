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

import re
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
    reset_history_key,
    HF_RESOLVED,
    DiagnosisState,
    StateUpdate,
)
from engine.tools import HF_OTHER_RELAYS
from llm.interface import LLMProvider
from llm.schemas import ParseOutput
from kb.schema import INTAKE_PRECEDENCE

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
    names = dict.fromkeys(kb.get(f).listed_as or _fault_name(f) for f in kb.fault_ids)
    return ("This isn't in my procedure set. I can verify: "
            + ", ".join(names) + ". Refer to the TSD for it.")


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
                 "reset_QLA = about to reset the QLA relay target; "
                 "work_on_roof = about to climb on to the loco roof (pantograph work); "
                 "enter_HT_compartment = about to open or enter the HT compartment; "
                 "wedge_Q118 / wedge_Q44 / wedge_Q45 = about to wedge that relay (in energised "
                 "condition); wedge_contactor = about to wedge C105 / C106 / C107. An intent is "
                 "what they are ABOUT to do — a wedge already done is a claimed step, not an intent")
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
    "loco_grounded": "'yes' if the loco has been grounded (HOM operated); 'no' if the pilot says it is NOT grounded / HOM not operated / grounding not done",
    "pantograph_not_lowered": "the pantograph did NOT lower when ZPT was put on 0 / BPEMS pressed",
    "both_pantographs_damaged": "BOTH pantographs are damaged",
    "load_and_road_do_not_permit": "the pilot says the load and road do not permit working onwards (with the load restriction)",
    "drops_after_long_interval": "the relay dropped again only after a LONG interval of running ('no' if it dropped again soon / within minutes / repeatedly). When the pilot says how soon it recurred, set BOTH this and drops_frequently (opposite values)",
    "drops_frequently": "the relay is dropping frequently / repeatedly / again within minutes of a reset ('no' if only after a long interval). When the pilot says how soon it recurred, set BOTH this and drops_after_long_interval (opposite values)",
    "drops_in_particular_hmcs1_position": "with HMCS-1 tried in positions 2, 3, 4: it drops only in ONE particular position ('no' if in all)",
    "drops_in_all_hmcs1_positions": "with HMCS-1 tried in positions 2, 3, 4: it drops in ALL positions ('no' if only in one)",
    "target_resets": "'no' if the pilot says the relay target does NOT reset / cannot be reset / is not resetting; 'yes' if it reset",
    "banding_failure_seen": "poly-glass material projecting out through a traction motor vent mesh (banding failure)",
    "target_resets_after_isolation": "after isolating the defective equipment, the relay target reset ('yes') or still did not ('no')",
    "target_resets_with_j1_neutral": "with HQOP-1 normalised and reverser J1 in neutral, the target reset ('yes') or did not ('no')",
    "target_resets_with_j2_neutral": "with HQOP-2 normalised and reverser J2 in neutral, the target reset ('yes') or did not ('no')",
    "bit_packing_unsuccessful": "'yes' ONLY when all three prescribed reverser bits have been tried and none reset the relay",
    "not_resetting_with_hqop_off": "the relay is still not resetting even with HQOP-1/HQOP-2 in OFF",
    "drops_with_hqop_off": "the relay is still dropping even with HQOP-1/HQOP-2 kept in OFF",
    "stops_after_isolating_particular_tm": "'yes' if the relay stopped dropping after isolating one particular traction motor",
    "target_resets_after_isolating": "after isolating the auxiliary equipment one switch at a time, the QOA target reset ('yes') or still did not ('no')",
    "target_resets_after_releasing_contactor": "after releasing a welded EM contactor, the QOA target reset ('yes') or not ('no')",
    "not_resetting_with_hqoa_0": "the QOA target is dropping or not resetting even with HQOA on 0",
    "drops_in_all_hmcs2_positions": "with HMCS-2 tried in positions 2, 3, 4: it drops in ALL positions ('no' if only in one)",
    "traction2_abnormality_found": "abnormality (smoke/smell/fire/heat/damage) found in traction power circuit-2 equipment (RSI-2, J2, SL-2, L4-L6, TM4-6, AM4 shunt, RU5/RU6, QD-2, SJ4-6, TFR terminals)",
    "drops_in_particular_hmcs2_position": "with HMCS-2 tried in positions 2, 3, 4: it drops only in ONE particular position ('no' if in all)",
    "fire_uncontrollable": "the pilot says the fire cannot be put out / is out of control / extinguishers exhausted and still burning",
    # ── batch 2 (Ch.7 tripping failures) ──
    "trip_sign": "the abnormal sign observed when re-closing DJ (close BLDJ, press BLRDJ): "
                 "'icdj' = LSDJ does not extinguish / DJ does not close at all; "
                 "'no_tension' = DJ closes but the UA needle does not deviate, no auxiliary sound, LSCHBA stays on, trips after ~5.6 s before BLRDJ is released; "
                 "'op_a_beginning' = DJ closes and trips immediately / LSDJ flickers; "
                 "'op_a_ending' = UA needle deviates but LSCHBA does not extinguish and DJ trips after ~5.6 s before BLRDJ is released; "
                 "'reglows_on_release' = everything normal (UA deviates, aux sound, LSCHBA goes off) but LSDJ re-glows the moment BLRDJ is released; "
                 "'op_b_part1' = trips within 15 s AFTER BLRDJ is released (LSCHBA had gone off); "
                 "'op_o' = trips within 30 s after closing BLVMT; "
                 "'op_1' = trips on taking the first notch; "
                 "'op_2' = trips on the sixth notch (within 15 s); "
                 "'twac' = trips at random / on and off with none of these fixed signs. Leave unknown if the pilot has not described the sign",
    "bp_dropped_with_trip": "BP pressure dropped suddenly along with the DJ trip ('no' if BP held)",
    "lsgr_not_glowing_and_panto_lowered": "LSGR is NOT glowing AND the panto has also lowered",
    "battery_voltage_zero": "the battery voltage / UBA reads zero",
    "panto_lowered_with_normal_battery_voltage": "the panto lowered although battery voltage is normal and CCBA is good",
    "rs_pressure_low": "RS (reservoir) pressure is less / low",
    "lssit_glowing": "the LSSIT lamp is glowing (SIV loco)",
    "battery_voltage_low_or_zero": "UBA / battery voltage is below 90 V or zero ('no' if normal, above 90 V)",
    "air_pressure_low": "RS/MR air pressure is below 6.5 kg/cm2 ('no' if above)",
    "c118_closing": "contactor C118 closes when BLDJ is closed and BLRDJ pressed ('no' if C118 does not close)",
    "q118_energised": "relay Q118 energises / clicks when HBA is put on 1 ('no' if it does not energise)",
    "q45_energised": "relay Q45 energises when BLDJ is closed and BLRDJ pressed ('no' if not)",
    "q44_energised": "relay Q44 energises after Q118 and Q45 ('no' if not)",
    "uba_zero": "the battery voltmeter UBA indicates exactly '0' ('no' if it shows a low but non-zero voltage)",
    "signal_lamps_glowing": "the signal lamps are glowing although UBA reads low / zero",
    "mcpa_working_but_pressure_not_building": "MCPA (auxiliary compressor) is running but the air pressure is not building up",
    "ccba_or_ccpt_melted": "one or both of the fuses CCBA / CCPT is found melted",
    "dj_closes_with_manual_q118": "with Q118 pressed manually: 'yes' if DJ closed AND held after Q118 was released; 'no' if DJ did not close at all. Leave unknown if it closed but tripped after release (use trips_after_releasing_q118)",
    "trips_after_releasing_q118": "with Q118 pressed manually DJ closed but tripped when Q118 was released",
    "all_em_contactors_open": "the pilot confirms all EM contactors (C105, C106, C107) are opened (asked before wedging Q118)",
    "ccdj_melted_third_time": "the CCDJ fuse has melted a third time (once again, after renewing it twice)",
    "dj_closes_with_manual_q45": "with Q45 pressed manually: 'yes' if DJ closed and held; 'no' if DJ still did not close. Leave unknown if it closed but tripped (use trips_with_manual_q45)",
    "trips_with_manual_q45": "with Q45 pressed manually DJ closed but tripped before or after releasing Q45",
    "dj_closes_with_manual_q44": "with Q44 pressed manually: 'yes' if DJ closed and held; 'no' if DJ did not close",
    "tlc_permission_for_wedging_q44": "TLC has given permission to wedge Q44 ('no' if TLC refused)",
    "gr_efficiency_test_done": "the GR efficiency test was conducted (GR travel 1-32 or 32-1 in 11-13 s, in LT) ('no' if not done / failed)",
    "roof_foreign_body_or_ohe_cut": "a foreign body on the roof, roof equipment touching the roof, a cut roof bar, or a cut in the OHE is noticed",
    "panto_not_touching_contact_wire": "the pantograph is not touching the contact wire",
    "heavy_flash_noticed": "a heavy flash was noticed while raising the other panto or closing DJ",
    "power_restored_after_5_min": "OHE power came back after about 5 minutes with no information of an OHE failure",
    "arno_abnormality_found": "smoke / no auxiliary sound / any abnormality from ARNO while closing DJ ('no' if ARNO is working properly)",
    "vcb_5_branch_loco": "'yes' if the loco has a VCB type 5-branch DJ; 'no' if it is a 6-branch VCB or ABCB loco; 'not_known' if the pilot says they do not know / are not sure. Leave unknown if not addressed",
    "no_operation_a_ending_trouble": "'yes' if the pilot confirms the loco does NOT have Operation 'A' ending trouble (LSCHBA extinguishes normally, DJ does not trip after 5.6 s before BLRDJ is released); 'no' if that trouble IS present",
    "both_mvsl_not_working": "BOTH MVSL-1 and MVSL-2 are not working",
    "one_mvsl_not_working": "exactly one of MVSL-1 / MVSL-2 is not working ('no' if both are working)",
    "mph_equalising_pipe_leaking": "the MPH equalising pipe (between the transformer oil conservator and MPH) is leaking",
    "mph_equalising_pipe_broken": "the MPH equalising pipe is broken",
    "dj_trips_with_hvsl1_on_1": "with HVSL-1 put back on '1' (15 s wait): DJ tripped ('yes') or held ('no')",
    "dj_trips_with_hvsl2_on_1": "with HVSL-2 put back on '1' (15 s wait): DJ tripped ('yes') or held ('no')",
    "dj_trips_with_hph_on_1": "with HPH put back on '1' (15 s wait): DJ tripped ('yes') or held ('no')",
    "mvrh_working": "MVRH blower is working ('no' if MVRH is not working)",
    "mvmt1_working": "MVMT-1 blower is working ('no' if not)",
    "mvmt2_working": "MVMT-2 blower is working ('no' if not)",
    "dj_trips_with_hvrh_on_1": "with HVRH put back on '1' (30 s wait after BLVMT): DJ tripped ('yes') or held ('no')",
    "dj_trips_with_hvmt1_on_1": "with HVMT-1 put back on '1' (30 s wait after BLVMT): DJ tripped ('yes') or held ('no')",
    "mvsi1_working": "MVSI-1 blower is working ('no' if not)",
    "mvsi2_working": "MVSI-2 blower is working ('no' if not)",
    "dj_trips_on_first_notch_with_hvsi_on_3": "with HVSI-1 & HVSI-2 on '3', taking one notch: DJ tripped ('yes') or held ('no')",
    "dj_trips_with_hvsi1_on_1": "with HVSI-1 put back on '1', one notch, 15 s wait: DJ tripped ('yes') or held ('no')",
    "contactors_closed": "what C105, C106, C107 do when BLVMT is closed: 'none' (none of them closes), 'some' (one or more does not close but not all), 'all' (all three close)",
    "cca_melts_with_hoba_off": "the CCA fuse melts even with HOBA off",
    "load_permits_5_notches": "the load permits clearing the block section within 5 notches ('no' if the load does not permit)",
    "concerned_switch_on_3": "for the wedged contactor(s) the concerned switch (HVRH for C107, HVMT-1 for C105, HVMT-2 for C106) is kept on the '3' position",
    "c107_not_closed_ac_mvrf_loco": "this is an AC MVRF loco and C107 is the contactor not closing",
    "still_trips_with_hvmt_in_3": "with HVMT-1 & HVMT-2 in '3' and DJ closed: the trouble still exists / DJ still trips on the sixth notch ('yes') or DJ holds ('no')",
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
    # the resolved fault's family: itself and its route / combination targets — the reroute
    # happens after parse, and a claim naming the target procedure's step must not be lost
    known = {sid for fid in kb.fault_ids if same_family(kb, fid, fault_id) for sid in kb.get(fid).step_ids}
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
    routes = lambda f: ({r.route_to for r in kb.get(f).combination_rules}
                        | {r.route_to for r in kb.get(f).route_rules}) if f in kb.fault_ids else set()
    return b in routes(a) or a in routes(b)


def parse_turn(text: str, state: DiagnosisState, kb: KnowledgeBase, provider: LLMProvider,
               last_assistant: Optional[str] = None) -> ParseResult:
    # 1. deterministic alias match — no model needed for the fault.
    alias_hit = kb.match_alias(text)

    out = provider.structured(system_prompt(kb), user_prompt(text, state, last_assistant), ParseOutput)

    # An intake hub's alias ("DJ tripped") is a generic entry phrase: if the model names a
    # specific KB fault in the same message ("DJ tripped, QLM is locked"), that fault wins —
    # unconfirmed, so a hard-gated one is confirmed before guidance (§5.5).
    yielded_hub_steps: set[str] = set()
    if (alias_hit is not None and alias_hit.precedence <= INTAKE_PRECEDENCE
            and out.fault_guess in kb.fault_ids and out.fault_guess != alias_hit.fault_id
            and out.fault_confidence >= CLARIFY_THRESHOLD
            and kb.get(out.fault_guess).precedence > alias_hit.precedence):
        yielded_hub_steps = set(alias_hit.step_ids)   # the hub's own drill, if claimed, is simply done
        alias_hit = None

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
    if yielded_hub_steps:
        accepted = tuple(s for s in accepted if s not in yielded_hub_steps)
        rejected = tuple(s for s in rejected if s not in yielded_hub_steps)

    history: dict = {}
    # KB-declared route phrases ("not resetting", "cannot be reset"): deterministic, like an
    # alias hit — the reroute to the other procedure must not depend on the model.
    fault_obj = kb.get(fault_id) if fault_id in kb.fault_ids else None
    if fault_obj is not None:
        low = " ".join(text.lower().split())
        for rr in fault_obj.route_rules:
            if any(re.search(rf"(?<![\w-]){re.escape(ph.lower())}(?![\w-])", low) for ph in rr.phrases):
                history[rr.if_fact] = rr.equals
    if _yn(out.abnormality_found):
        history[HF_ABNORMALITY] = out.abnormality_found
    if _yn(out.was_reset_earlier_this_trip):
        history[reset_history_key(kb.get(fault_id) if fault_id in kb.fault_ids else None)] = out.was_reset_earlier_this_trip
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
        loco_rb=out.loco_rb,
        claimed_steps=accepted + rejected,   # rejected ones are surfaced by update_state/diff
        history=history,
        intended_action=out.intended_action,
        fault_presenting=presenting,
        denies_asked_step=(out.denies_asked_step == "yes"),
        wants_detail=(out.asks_for_detail == "yes"),
    )
    return ParseResult(update, confidence, False, None, out, rejected_steps=rejected + tuple(out.unmapped_claims))
