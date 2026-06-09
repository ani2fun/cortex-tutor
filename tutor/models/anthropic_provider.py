"""Anthropic gate provider — forced strict tool-use → the ``GateVerdict`` tool input.

Uses the stable Messages tools API (robust across SDK versions): a single tool whose ``input_schema``
is ``GateVerdict.model_json_schema()`` (score as an enum, ``additionalProperties: false``), with
``tool_choice`` forcing it. **No ``thinking`` / ``effort``** — the gate runs on Haiku, which supports
neither.
"""

from __future__ import annotations

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
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens

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
