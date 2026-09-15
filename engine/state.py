"""Conversation state (BUILD_PLAN §10.1, field-for-field) + the state transition.

``update_state`` is the ONLY way state changes. Every call to it must be followed by
the safety reflex (``engine.gates.evaluate_gates``) — see §5.1. In M1 that sequencing
lives in ``engine.run_turn``; in M3 it is wired into the LangGraph graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from kb.schema import Fault

Config = Literal["siv", "arno", "unknown"]
LocoTypeOrUnknown = Literal["wag7", "wag5", "wap4", "unknown"]
YesNo = Literal["yes", "no"]


@dataclass
class LocoInfo:
    """One locomotive in the session. All three fields are ALWAYS carried; which of them a
    fault may consult is decided by that fault's declared dependencies (kb.schema.Fault)."""
    loco_number: str = ""
    type: LocoTypeOrUnknown = "unknown"          # class axis: layout / equipment set
    config: Config = "unknown"                   # auxiliary-build axis: SIV / ARNO

    def as_dict(self) -> dict[str, str]:
        return {"loco_number": self.loco_number, "type": self.type, "config": self.config}

# History-fact keys used by the QLM procedure. ``was_QLM_reset_earlier_this_trip`` is
# the reset_limit gate's ``needs_history`` key in kb/faults/qlm_dropped.yaml;
# ``abnormality_found`` is the pilot's verdict on §6.1.1(a)–(c).
HF_RESET_EARLIER = "was_QLM_reset_earlier_this_trip"
HF_ABNORMALITY = "abnormality_found"          # fault-wide default abnormality verdict
HF_RESOLVED = "fault_resolved"                 # pilot reports the fault cleared (gate-free faults)
HF_OTHER_RELAYS = "other_relays_acted"         # list of other relay targets the pilot reported
# Recurrence — §6.1.1(f)(ii) "QLM acts second time". Set by the ENGINE (update_state backstop)
# and, as a fast path only, by the parser.
HF_RECURRED = "fault_recurred"                 # the relay acted again after the first reset
HF_RESET_PERFORMED = "reset_performed_this_session"   # pilot claimed the gated reset step done
HF_RESET_INSTRUCTED = "reset_instructed_this_session" # engine emitted the first-reset caution

# Intended-action vocabulary (structured; the M2 parser maps free text onto these).
ACTION_RESET_QLM = "reset_QLM"
ACTION_WORK_ON_ROOF = "work_on_roof"          # pantograph_damaged hazard gate (§10.03 / §11.04)
ACTIONS = (ACTION_RESET_QLM, ACTION_WORK_ON_ROOF)


@dataclass
class DiagnosisState:
    matched_fault: Optional[str] = None            # fault_id | null
    fault_confirmed: bool = False
    config: Config = "unknown"                     # resolved lazily (§2.4)
    steps_required: list[str] = field(default_factory=list)   # from KB once matched
    steps_claimed_done: set[str] = field(default_factory=set)
    history_facts: dict[str, Any] = field(default_factory=dict)  # e.g. reset-earlier: yes/no
    intended_action: Optional[str] = None          # pilot's next move, for the reflex
    stuck_at: Optional[str] = None
    steps_declined: set[str] = field(default_factory=set)    # pilot said "not done" when asked
    tool_results: dict[str, Any] = field(default_factory=dict)  # idempotency / no-progress
    iter_count: int = 0                            # loop guard (§6)
    clarify_asked: int = 0                         # unresolved-fault turns so far (§5.6 backstop)
    last_terminal_sig: Optional[str] = None        # what the pilot was last told (repeat detection)
    # Session loco context ("session bar"): [leading] or [leading, trailing]. The fault is
    # attributed to locos[active_loco]. Kept alongside §10.1's ``config`` (which mirrors the
    # active loco's config for backward compatibility with BUILD_PLAN's state shape).
    locos: list[LocoInfo] = field(default_factory=lambda: [LocoInfo()])
    active_loco: int = 0

    # -- helpers -------------------------------------------------------------
    @property
    def loco(self) -> LocoInfo:
        return self.locos[self.active_loco] if self.locos else LocoInfo()

    def set_locos(self, locos: list[LocoInfo], active: int = 0) -> None:
        assert 1 <= len(locos) <= 2, "single loco, or leading + trailing"
        self.locos = list(locos)
        self.active_loco = min(max(active, 0), len(locos) - 1)
        self.config = self.loco.config

    def swap_locos(self) -> None:
        """Multi: swap leading and trailing; the attributed loco follows its row."""
        if len(self.locos) == 2:
            self.locos.reverse()
            self.active_loco = 1 - self.active_loco
            self.config = self.loco.config

    def axis_value(self, axis_fact: str, fault: "Fault") -> Optional[str]:
        """Resolve a loco-axis pseudo-fact for ``fault`` — ONLY if the fault declares that
        dependency; otherwise None ("not consulted"), even if the value is known."""
        if axis_fact == "loco_config" and fault.depends_on_config:
            return None if self.loco.config == "unknown" else self.loco.config
        if axis_fact == "loco_type" and fault.depends_on_type:
            return None if self.loco.type == "unknown" else self.loco.type
        return None
    def history(self, key: str) -> Optional[str]:
        """Tri-state read: 'yes' | 'no' | None (= not stated)."""
        v = self.history_facts.get(key)
        return None if v is None else str(v).lower()

    def snapshot(self) -> tuple:
        """Hashable view of the diagnostic fields, for the no-progress detector (§6)."""
        return (
            self.matched_fault,
            self.fault_confirmed,
            self.config,
            tuple((l.loco_number, l.type, l.config) for l in self.locos), self.active_loco,
            tuple(self.steps_required),
            tuple(sorted(self.steps_claimed_done)),
            tuple(sorted((k, str(v)) for k, v in self.history_facts.items())),
            self.intended_action,
            self.stuck_at,
        )


@dataclass(frozen=True)
class StateUpdate:
    """A structured update — what the M2 ``parse`` node will emit, built by hand in M1.

    Field semantics are *merge*: ``None`` / empty means "nothing new on this field".
    """
    fault_id: Optional[str] = None
    # None with a fault_id = "structured/deterministic match" → treated as confirmed.
    # The M2 parser passes an explicit False for an LLM-guessed hard-gated fault (§5.5).
    fault_confirmed: Optional[bool] = None
    config: Optional[Config] = None
    loco_type: Optional[LocoTypeOrUnknown] = None
    claimed_steps: tuple[str, ...] = ()
    history: dict[str, Any] = field(default_factory=dict)
    intended_action: Optional[str] = None
    clear_intended_action: bool = False
    # This message PRESENTS the fault (relay dropped / locked / acted) — as opposed to
    # merely referring to it. Set deterministically by parse on an alias hit, or on a
    # confident model guess that says the fault is presenting. Drives the recurrence backstop.
    fault_presenting: bool = False
    # The pilot says the check the assistant just asked about is NOT done (parser fast path;
    # the engine also treats a repeated ask on the same step as this).
    denies_asked_step: bool = False

    def brings_news(self, state: "DiagnosisState") -> bool:
        """Does this update change anything the engine acts on? False for "ok" / "anything
        else?" — a turn with no new claim, fact, intent, fault, config or presentation."""
        return bool(
            (self.fault_id and self.fault_id != state.matched_fault)
            or (self.fault_confirmed is not None and self.fault_confirmed != state.fault_confirmed)
            or self.config or self.loco_type or self.claimed_steps or self.history
            or self.intended_action or self.clear_intended_action or self.fault_presenting
            or self.denies_asked_step)


def update_state(state: DiagnosisState, update: StateUpdate, fault: Optional[Fault]) -> DiagnosisState:
    """Merge ``update`` into ``state`` in place and return it.

    * ``steps_required`` is (re)populated from the KB when a fault is matched.
    * Claimed steps are recorded **only if they exist in the matched fault's checklist**
      (BUILD_PLAN §8: unrecognised claims are surfaced by the diff, never silently
      accepted). Unrecognised claims are kept aside in ``tool_results['unrecognised_claims']``
      so the diff can report them.
    * ``intended_action`` persists across turns until explicitly cleared or replaced.
    * **Recurrence backstop (§6.1.1(f)(ii)) — the PRIMARY mechanism, independent of the
      parser:** if a reset was already performed or instructed in this session *before*
      this update, and this message presents the fault again, the engine itself records
      ``fault_recurred = yes``. Biased to over-refuse: a false positive costs a section;
      a false negative would let a real fault be reset again.
    """
    prior_reset = (str(state.history_facts.get(HF_RESET_PERFORMED, "")).lower() == "yes"
                   or str(state.history_facts.get(HF_RESET_INSTRUCTED, "")).lower() == "yes")

    if update.fault_id is not None:
        if update.fault_id != state.matched_fault:
            state.fault_confirmed = False
        state.matched_fault = update.fault_id
        if update.fault_confirmed is None:
            state.fault_confirmed = True      # structured / alias match is trusted
    if fault is not None and state.matched_fault == fault.fault_id:
        state.steps_required = list(fault.step_ids)
    if update.fault_confirmed is not None:
        state.fault_confirmed = update.fault_confirmed
    if update.config is not None:                 # pilot stated the config in free text
        state.config = update.config
        if state.locos:
            state.loco.config = update.config
    if update.loco_type is not None and state.locos:
        state.loco.type = update.loco_type

    known = set(state.steps_required)
    unrecognised = [s for s in update.claimed_steps if s not in known]
    state.steps_claimed_done.update(s for s in update.claimed_steps if s in known)
    state.steps_declined.difference_update(update.claimed_steps)      # now done → no longer declined
    if update.denies_asked_step and state.stuck_at and state.stuck_at not in state.steps_claimed_done:
        state.steps_declined.add(state.stuck_at)
    # KB-declared implications of a claim (branch facts the step's position establishes)
    if fault is not None:
        for s in fault.steps:
            if s.id in update.claimed_steps and s.id in known:
                for k, v in s.implies.items():
                    state.history_facts.setdefault(k, v)
    if unrecognised:
        state.tool_results["unrecognised_claims"] = unrecognised
    else:
        state.tool_results.pop("unrecognised_claims", None)

    incoming = dict(update.history)
    # The reset the ENGINE instructed, now reported done, is `reset_performed_this_session`
    # — not "reset earlier this trip" (that means a reset BEFORE this occurrence). Seen live:
    # "reset done, resumed" was parsed as was_QLM_reset_earlier_this_trip=yes and refused as
    # a second reset. Rule: while a first reset is instructed and the relay is not presenting
    # again, a same-turn 'yes' on the prior-reset fact is taken as the instructed reset.
    claims_reset = fault is not None and any(
        s.gate and s.gate.type == "reset_limit" and s.id in update.claimed_steps for s in fault.steps)
    if (str(state.history_facts.get(HF_RESET_INSTRUCTED, "")).lower() == "yes"
            and not update.fault_presenting
            and str(state.history_facts.get(HF_RESET_EARLIER, "")).lower() != "yes"):
        for key in [k for k, v in incoming.items()
                    if k == HF_RESET_EARLIER or (fault and any(s.gate and s.gate.needs_history == k for s in fault.steps))]:
            if str(incoming[key]).lower() == "yes":
                incoming.pop(key)
                state.history_facts[HF_RESET_PERFORMED] = "yes"
    # Un-instructed case of the same thing (seen live 2026-09-16: "qlm dropped, i resetted,
    # now working fine" → parsed as BOTH the gated step done AND was_reset_earlier=yes, and
    # refused under (f)(ii) although one reset was reported). Rule: the gated reset step
    # claimed in the SAME turn as a 'yes' on the prior-reset fact AND fault_resolved=yes
    # (that reset CLEARED the current occurrence, so it is this occurrence's reset), with no
    # reset performed or instructed in the session before this turn and no recurrence stated,
    # is ONE reset — the one just performed. The prior-reset fact is left unstated, so the
    # reflex ASKS (rule 4) rather than assumes. Without fault_resolved the fact stands and the
    # reflex refuses ("QLM locked. yes, reset it once already" — the relay is live NOW and
    # the reset was before: bias to over-refuse); fault_recurred always refuses.
    if (claims_reset and not prior_reset
            and str(incoming.get(HF_RESET_EARLIER, "")).lower() == "yes"
            and str(incoming.get(HF_RESOLVED, "")).lower() == "yes"
            and str(incoming.get(HF_RECURRED, state.history_facts.get(HF_RECURRED, ""))).lower() != "yes"):
        incoming.pop(HF_RESET_EARLIER)
        state.history_facts[HF_RESET_PERFORMED] = "yes"
    state.history_facts.update(incoming)

    # gated reset step claimed → a reset has been performed this session
    if fault is not None and any(s.gate and s.gate.type == "reset_limit" and s.id in state.steps_claimed_done
                                 for s in fault.steps):
        state.history_facts[HF_RESET_PERFORMED] = "yes"

    # engine backstop: reset already done/instructed before this turn + fault presents again
    if prior_reset and update.fault_presenting:
        state.history_facts[HF_RECURRED] = "yes"

    if update.clear_intended_action:
        state.intended_action = None
    elif update.intended_action is not None:
        state.intended_action = update.intended_action
    return state


def resolve_combination(state: DiagnosisState, fault: Optional[Fault], lookup) -> Optional[str]:
    """DETERMINISTIC fault-identity resolution, run right after every state update and
    BEFORE the reflex: if the pilot has reported other relays that match one of the matched
    fault's combination rules and the target fault is in the KB, switch ``matched_fault``
    to it (claimed steps carry over — the combination faults reuse the step ids).

    Why here and not left to the agent's ``check_combination`` tool: the reflex evaluates
    gates against ``matched_fault``; if identity waited on a discretionary tool, a gate
    could fire (and short-circuit the loop) under the WRONG procedure — observed live
    2026-09-15. ``check_combination`` remains available to the agent as a query.
    ``lookup(fault_id) -> Fault | None`` is the KB accessor. Returns the new fault_id or None."""
    if fault is None or state.matched_fault != fault.fault_id:
        return None
    reported = {str(r).upper() for r in (state.history_facts.get(HF_OTHER_RELAYS) or [])}
    if not reported:
        return None
    for rule in fault.combination_rules:
        if reported & {r.upper() for r in rule.if_also}:
            target = lookup(rule.route_to)
            if target is None:
                return None                              # not encoded → stays; reassess defers to TLC
            state.matched_fault = target.fault_id
            state.steps_required = list(target.step_ids)
            state.steps_claimed_done &= set(target.step_ids)
            return target.fault_id
    return None


def checks_complete(state: DiagnosisState, fault: Fault) -> bool:
    """True when every ORDINARY (gate-null) step of ``fault`` is claimed done."""
    return all(s.id in state.steps_claimed_done for s in fault.ordinary_steps)
