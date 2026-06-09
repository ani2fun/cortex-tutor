"""Model routing: which model serves which role.

Encodes the **hard invariant** that the gate model (Haiku) never uses extended thinking / ``effort``
(it doesn't support them). Tier-based *provider* selection (homelab Claude vs wk-1 Ollama fallback vs
BYOK client-direct) lands with those phases (P7); this is the role→model + capability layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    COACH = "coach"
    GATE = "gate"


@dataclass(frozen=True)
class ModelChoice:
    model: str
    #: The GATE must never use extended thinking/effort (Haiku constraint); the coach may.
    allow_thinking: bool


def choose(role: Role, *, coach_model: str, gate_model: str) -> ModelChoice:
    if role is Role.GATE:
        return ModelChoice(model=gate_model, allow_thinking=False)
    return ModelChoice(model=coach_model, allow_thinking=True)
