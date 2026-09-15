"""Output contracts for the three LLM jobs (BUILD_PLAN §8). These are the ONLY shapes the
model may return; everything is post-validated against the KB / tool registry by the
engine-side code in parse.py / decide.py — the model's word is never final.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

YesNoUnknown = Literal["yes", "no", "unknown"]


class ParseOutput(BaseModel):
    """parse: pilot free text → structured update (+ confidence)."""
    model_config = ConfigDict(extra="forbid")

    fault_guess: Optional[str] = Field(
        default=None,
        description="fault_id from the provided list, or null if none fits / not stated.")
    fault_confidence: float = Field(ge=0.0, le=1.0,
                                    description="0–1 confidence in fault_guess.")
    confirms_fault: YesNoUnknown = Field(
        default="unknown",
        description="If the assistant just asked 'is it fault X?', the pilot's answer.")
    fault_presenting: YesNoUnknown = Field(
        default="unknown",
        description="Does THIS message report the relay/fault acting NOW (dropped, locked, "
                    "tripped, acted, target down, red) — as opposed to merely referring to it "
                    "or reporting on checks? 'yes' only for a present-tense report of the fault.")
    fault_recurred: YesNoUnknown = Field(
        default="unknown",
        description="'yes' if the pilot says the relay/fault has acted AGAIN after a reset "
                    "(again, once more, re-locked, second time, tripped after reset).")
    claimed_steps: list[str] = Field(
        default_factory=list,
        description="step ids from the provided checklist the pilot says they have DONE.")
    abnormality_found: YesNoUnknown = Field(
        default="unknown",
        description="Abnormality (smoke/smell/fire/heat/red-hot/oil leak/abnormal oil level) "
                    "reported from the FEEDING-POWER-CIRCUIT checks only: HT-2 compartment, "
                    "transformer vent/oil, TFP/GR oil levels, CGR arc chutes, RGR/RPGR, TFR "
                    "terminals, bushings, HT cable. 'no' only if they say those were normal/OK. "
                    "Abnormality in OTHER circuits (traction / auxiliary equipment) goes in "
                    "'facts' under the fault-specific key, NOT here.")
    was_reset_earlier_this_trip: YesNoUnknown = Field(
        default="unknown",
        description="Did the pilot state whether this relay was reset earlier this trip?")
    other_relays_acted: Optional[list[str]] = Field(
        default=None,
        description="Other relay targets the pilot says have ALSO dropped (e.g. QOP-1, QRSI-2, "
                    "QLA, QOA). null if not mentioned.")
    intended_action: Optional[Literal["reset_QLM", "work_on_roof"]] = Field(
        default=None,
        description="The pilot's stated NEXT move, from the provided action list, or null. "
                    "'work_on_roof' = about to climb on to the loco roof (pantograph work).")
    unmapped_claims: list[str] = Field(
        default_factory=list,
        description="Things the pilot says they did that do not map to any checklist step "
                    "(short phrases, verbatim-ish).")
    fault_resolved: YesNoUnknown = Field(
        default="unknown",
        description="'yes' only if the pilot says the fault is now cleared / equipment working "
                    "again (e.g. 'sanders working now', 'resumed traction').")
    facts: dict[str, YesNoUnknown] = Field(
        default_factory=dict,
        description="Additional yes/no facts listed under 'Fault-specific facts' in the "
                    "instructions, keyed exactly by the fact name. Omit facts not stated.")


class DecideOutput(BaseModel):
    """agent_decide: choose the next Class-A diagnostic tool, or 'none' to stop looping."""
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(description="One tool name from the provided registry, or 'none'.")
    reason: str = Field(description="One sentence: what you still need to know.")
