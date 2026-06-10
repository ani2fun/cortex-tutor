"""Ollama gate provider — keyless local dev over the OpenAI-compatible chat-completions endpoint.

Ollama models vary in tool-calling reliability, so the gate uses **JSON output** (`response_format`
json_object + the schema embedded in the prompt) rather than forced tool-use, and feeds the parsed
object into the same validate → repair → fail-safe path in `orchestration.gate`. The OpenAI wire also
makes a future BYOK provider a drop-in.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from tutor.models.base import ChatMessage


class OllamaGateProvider:
    def __init__(self, base_url: str, model: str, *, timeout: float = 60.0) -> None:
        # Accept either the host root or a `…/v1` URL; we always target `/v1/chat/completions`.
        self._base = base_url.rstrip("/").removesuffix("/v1")
        self._model = model
        self._timeout = timeout
        # Public identity for the gate_call audit log + eval reports (read via getattr).
        self.kind = "ollama"
        self.model_id = model

    async def gate(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        tool_schema: dict,
        tool_name: str,
    ) -> dict:
        schema = json.dumps(tool_schema, ensure_ascii=False)
        directed_system = (
            f"{system}\n\n## Output (strict)\n"
            "Respond with ONLY a single JSON object — no prose, no markdown fences — matching this "
            f"schema:\n{schema}"
        )
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": directed_system}, *messages],
            "stream": False,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)


class OllamaCoachProvider:
    """Streams the coach reply via Ollama's OpenAI-compatible chat-completions SSE."""

    def __init__(self, base_url: str, model: str, *, timeout: float = 180.0) -> None:
        self._base = base_url.rstrip("/").removesuffix("/v1")
        self._model = model
        self._timeout = timeout

    async def coach_stream(self, *, system: str, messages: list[ChatMessage]) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            "temperature": 0.4,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", f"{self._base}/v1/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
