"""agent_decide — choose the next Class-A tool. LLM job 2 of 3; where agency lives (§4.2).

Bounded (§6): the model may name only a registered tool or "none". Anything else is
rejected by the engine — recorded, never attempted — and treated as "none" so the loop
proceeds to reassess instead of spinning. The safety reflex is not selectable here and
is not mentioned as an option; it has already run before this node (§5.1).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from engine.state import DiagnosisState
from engine.tools import REGISTRY, is_registered
from kb.schema import Fault
from llm.interface import LLMProvider
from llm.schemas import DecideOutput

PROMPT_PATH = Path(__file__).with_name("prompts") / "decide.md"
NONE = "none"


@dataclass(frozen=True)
class ToolChoice:
    tool: Optional[str]            # registered tool name, or None (= stop looping)
    reason: str
    rejected: Optional[str] = None  # what the model asked for, if it was not allowed


def tool_registry_text() -> str:
    return "\n".join(f"- {t.name}: {t.description}" for t in REGISTRY.values())


def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").replace("{tool_registry}", tool_registry_text())


def state_summary(state: DiagnosisState, fault: Fault) -> str:
    fresh = [k for k, v in state.tool_results.items()
             if isinstance(v, dict) and v.get("_snapshot") == state.snapshot()]
    stale = [k for k, v in state.tool_results.items()
             if isinstance(v, dict) and "_snapshot" in v and k not in fresh]
    lines = [
        f"matched_fault: {fault.fault_id} (confirmed={state.fault_confirmed})",
        f"config: {state.config}; config_dependency: {fault.config_dependency}",
        f"steps_required: {state.steps_required}",
        f"steps_claimed_done: {sorted(state.steps_claimed_done)}",
        f"history_facts: {state.history_facts}",
        f"intended_action: {state.intended_action}",
        f"stuck_at: {state.stuck_at}",
        f"combination_rules_exist: {bool(fault.combination_rules)}",
        f"tool results fresh for the current state: {fresh or 'none'}",
        f"tool results stale (state changed since): {stale or 'none'}",
        f"iteration: {state.iter_count}",
    ]
    return "\n".join(lines)


def agent_decide(state: DiagnosisState, fault: Fault, provider: LLMProvider) -> ToolChoice:
    out = provider.structured(system_prompt(), state_summary(state, fault), DecideOutput)
    name = out.tool.strip()
    if name.lower() == NONE or name == "":
        return ToolChoice(None, out.reason)
    if not is_registered(name):
        # Bounded toolset (§6): rejected by the engine, not attempted.
        return ToolChoice(None, out.reason, rejected=name)
    return ToolChoice(name, out.reason)
