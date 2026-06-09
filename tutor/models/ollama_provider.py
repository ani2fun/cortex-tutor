"""Ollama gate provider — keyless local dev over the OpenAI-compatible chat-completions endpoint.

Ollama models vary in tool-calling reliability, so the gate uses **JSON output** (`response_format`
json_object + the schema embedded in the prompt) rather than forced tool-use, and feeds the parsed
object into the same validate → repair → fail-safe path in `orchestration.gate`. The OpenAI wire also
makes a future BYOK provider a drop-in.
"""

from __future__ import annotations

import json

import httpx

from tutor.models.base import ChatMessage


class OllamaGateProvider:
    def __init__(self, base_url: str, model: str, *, timeout: float = 60.0) -> None:
        # Accept either the host root or a `…/v1` URL; we always target `/v1/chat/completions`.
        self._base = base_url.rstrip("/").removesuffix("/v1")
        self._model = model
        self._timeout = timeout

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
