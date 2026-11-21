from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PHASE_SEQUENCE = [
    "run_initialization",
    "available_gate",
    "D1_documentation",
    "D2_environment",
    "D3_executability",
    "D4_claim_support",
    "D5_reusability",
    "final_scoring",
    "report_generation",
]


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentDecision:
    thought: str
    next_action: str
    tool_call: ToolCall | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    phase_finish: dict[str, Any] | None = None
    state_updates: dict[str, Any] = field(default_factory=dict)
    memory_note: str = ""
    questions: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class Observation:
    kind: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)


def next_phase_name(current_phase: str) -> str | None:
    try:
        index = PHASE_SEQUENCE.index(current_phase)
    except ValueError:
        return None
    if index + 1 >= len(PHASE_SEQUENCE):
        return None
    return PHASE_SEQUENCE[index + 1]
