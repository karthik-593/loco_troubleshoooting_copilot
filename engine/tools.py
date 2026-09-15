"""Class-A DISCRETIONARY diagnostic tools (BUILD_PLAN §7) — the bounded set the agent
may select from. Every tool is deterministic, idempotent, and KB-backed.

Two things are deliberately NOT here:
* the safety reflex (Class B) — it is not a tool; see engine/gates.py and §5.1;
* anything an LLM generates — tools compute from state + KB only.

Idempotency (§6): ``run_tool`` refuses to recompute a tool whose fresh result is already
in ``state.tool_results`` for the same state snapshot; it returns the cached result and
reports ``cached=True`` so the router's no-progress detector can see it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from engine.diff import diff_steps
from engine.state import HF_OTHER_RELAYS, DiagnosisState
from kb.schema import Fault

ToolFn = Callable[[DiagnosisState, Fault], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str        # shown to agent_decide; describes WHAT it returns, never procedure content
    fn: ToolFn


@dataclass(frozen=True)
class ToolResult:
    name: str
    result: dict[str, Any]
    cached: bool


# ---------------------------------------------------------------------------
# implementations
# ---------------------------------------------------------------------------

def diff_completed_steps(state: DiagnosisState, fault: Fault) -> dict[str, Any]:
    d = diff_steps(state, fault)
    state.stuck_at = d.next_unmet
    return {"next_unmet": d.next_unmet, "missing": list(d.missing),
            "complete": d.complete, "unrecognised": list(d.unrecognised)}


def lookup_procedure(state: DiagnosisState, fault: Fault) -> dict[str, Any]:
    return {"fault_id": fault.fault_id, "source": fault.source,
            "steps": [{"id": s.id, "gated": s.is_gated, "source": s.citation} for s in fault.steps]}


def check_combination(state: DiagnosisState, fault: Fault) -> dict[str, Any]:
    """Reroute if the pilot reported other relays that a combination rule names."""
    reported = {str(r).upper() for r in (state.history_facts.get(HF_OTHER_RELAYS) or [])}
    for rule in fault.combination_rules:
        hit = sorted(reported & {r.upper() for r in rule.if_also})
        if hit:
            return {"applies": True, "matched_relays": hit, "route_to": rule.route_to,
                    "source": rule.source}
    return {"applies": False, "matched_relays": [], "route_to": None, "source": None}


def get_required_observations(state: DiagnosisState, fault: Fault) -> dict[str, Any]:
    """What relay observations are still needed to rule combination rules in or out."""
    reported = {str(r).upper() for r in (state.history_facts.get(HF_OTHER_RELAYS) or [])}
    stated = HF_OTHER_RELAYS in state.history_facts
    needed = sorted({r for rule in fault.combination_rules for r in rule.if_also} - reported)
    return {"relays_to_observe": [] if stated else needed, "already_stated": stated}


def resolve_config(state: DiagnosisState, fault: Fault) -> dict[str, Any]:
    """§2.4: report which loco axes THIS fault depends on and whether the active loco's
    values for them are known. Axes the fault does not declare are not consulted at all."""
    loco = state.loco
    out: dict[str, Any] = {"loco_number": loco.loco_number, "active_loco": state.active_loco,
                           "locos": len(state.locos), "axes": {}}
    if fault.depends_on_config:
        out["axes"]["loco_config"] = {"dependency": fault.config_dependency, "value": loco.config,
                                      "needed": loco.config == "unknown"}
    if fault.depends_on_type:
        out["axes"]["loco_type"] = {"dependency": list(fault.type_axes), "value": loco.type,
                                    "needed": loco.type == "unknown"}
    out["needed"] = any(a["needed"] for a in out["axes"].values())
    return out


def read_siv_screen(state: DiagnosisState, fault: Fault) -> dict[str, Any]:
    """The one coded input (SIV converter display). Not applicable to QLM; M4+."""
    return {"applicable": False, "reason": "no SIV branch in the matched procedure"}


REGISTRY: dict[str, ToolSpec] = {t.name: t for t in (
    ToolSpec("diff_completed_steps",
             "Compare the steps the pilot has claimed against the matched fault's ordinary "
             "checklist; returns the next unmet step and everything still missing.",
             diff_completed_steps),
    ToolSpec("lookup_procedure",
             "Return the matched fault's step list (ids, which are safety-gated, citations).",
             lookup_procedure),
    ToolSpec("check_combination",
             "Check whether other relays the pilot reported trigger a combination rule that "
             "reroutes to a different fault.",
             check_combination),
    ToolSpec("get_required_observations",
             "List which other relay targets still need to be observed before a combination "
             "rule can be ruled in or out.",
             get_required_observations),
    ToolSpec("resolve_config",
             "Report which loco axes (config SIV/ARNO, class WAG-7/WAG-5/WAP-4) the matched "
             "fault depends on and whether they are known for the attributed loco.",
             resolve_config),
    ToolSpec("read_siv_screen",
             "Report whether the SIV converter's own displayed fault is applicable here.",
             read_siv_screen),
)}

TOOL_NAMES: tuple[str, ...] = tuple(REGISTRY)


def is_registered(name: str) -> bool:
    return name in REGISTRY


def run_tool(name: str, state: DiagnosisState, fault: Fault) -> ToolResult:
    """Execute a registered Class-A tool. Unregistered names are rejected, never attempted."""
    if not is_registered(name):
        raise KeyError(f"{name!r} is not a registered Class-A tool")
    key = name
    snap = state.snapshot()
    cached = state.tool_results.get(key)
    if cached is not None and cached.get("_snapshot") == snap:
        return ToolResult(name, cached["result"], cached=True)
    result = REGISTRY[name].fn(state, fault)
    state.tool_results[key] = {"result": result, "_snapshot": state.snapshot()}
    return ToolResult(name, result, cached=False)
