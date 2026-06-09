"""Provider seams the orchestration depends on, so tests inject fakes and the real
Anthropic / Ollama / BYOK paths slot behind one interface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

#: An OpenAI/Anthropic-style chat message: ``{"role": "user"|"assistant", "content": "..."}``.
ChatMessage = dict[str, str]


@runtime_checkable
class GateProvider(Protocol):
    """Runs the gate model with **forced** tool-use and returns the tool's input dict (unvalidated).

    Must raise on transport / timeout / refusal so the caller can fail-safe to ``RETRY`` — the gate
    may only ever *withhold* progress.
    """

    async def gate(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        tool_schema: dict,
        tool_name: str,
    ) -> dict: ...


@runtime_checkable
class CoachProvider(Protocol):
    """Streams the coach reply token-by-token (an async generator of text deltas)."""

    def coach_stream(self, *, system: str, messages: list[ChatMessage]) -> AsyncIterator[str]: ...
