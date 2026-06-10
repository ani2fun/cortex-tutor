"""Anthropic gate provider — forced strict tool-use → the ``GateVerdict`` tool input.

Uses the stable Messages tools API (robust across SDK versions): a single tool whose ``input_schema``
is ``GateVerdict.model_json_schema()`` (score as an enum, ``additionalProperties: false``), with
``tool_choice`` forcing it. **No ``thinking`` / ``effort``** — the gate runs on Haiku, which supports
neither.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from tutor.models.base import ChatMessage


class AnthropicGateProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_tokens: int = 512,
        timeout: float = 60.0,
    ) -> None:
        # max_retries=4 (SDK default 2): the gate shares the org's per-model TPM budget, and a
        # burst of turns (or an eval run) hits 429s the SDK can ride out via retry-after. Measured
        # 2026-06-10: at SDK defaults a 130-call eval run lost 20.8% of calls to 429-driven
        # fail-safe RETRYs (evals/out/haiku-default-temp).
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=4)
        self._model = model
        self._max_tokens = max_tokens
        # Public identity for the gate_call audit log + eval reports (read via getattr).
        self.kind = "anthropic"
        self.model_id = model

    async def gate(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        tool_schema: dict,
        tool_name: str,
    ) -> dict:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=0,  # a grader should sample as deterministically as possible (eval-verified)
            system=system,
            messages=messages,  # type: ignore[arg-type]
            tools=[
                {
                    "name": tool_name,
                    "description": "Record the gate verdict for the learner's latest answer.",
                    "input_schema": tool_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            # NB: Haiku gate — deliberately no `thinking` and no `effort`.
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                return dict(block.input)  # type: ignore[arg-type]
        raise RuntimeError(
            f"gate model emitted no '{tool_name}' tool_use block (stop_reason={resp.stop_reason})"
        )


class AnthropicCoachProvider:
    """Streams the coach reply (Sonnet) via the Messages streaming API."""

    def __init__(self, api_key: str, model: str, *, max_tokens: int = 1024, timeout: float = 120.0) -> None:
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens

    async def coach_stream(self, *, system: str, messages: list[ChatMessage]) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=messages,  # type: ignore[arg-type]
        ) as stream:
            async for text in stream.text_stream:
                yield text
