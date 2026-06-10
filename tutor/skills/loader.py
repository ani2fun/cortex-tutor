"""Loads the version-controlled Socratic coaching rubric (``.claude/skills/socratic-tutor/``).

This is *externalized prompt content* (NOT the Anthropic Agent Skills API). The loader keeps the
**coach** prompt (persona, prose) and the **gate** prompt (lean grader) separate, so the coach is
never handed the verdict schema it must not emit — only the gate is, and it does so via forced
tool-use, not this markdown. The eval runner reads the SAME files the service loads, so a rubric edit
is exactly what CI gates, and ``rubric_version()`` stamps each session for auditability + eval-baseline
pinning.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from tutor.domain.steps import Step

#: Repo-root-relative location of the skill (``tutor/skills/loader.py`` → repo root is parents[2]).
SKILL_DIR = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "socratic-tutor"


def _read(rel: str) -> str:
    return (SKILL_DIR / rel).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def rubric_version() -> str:
    """A short content hash over every rubric file — stamped onto each session so a prompt change is
    auditable and the eval baseline can be pinned to a specific rubric."""
    h = hashlib.sha256()
    for path in sorted(SKILL_DIR.rglob("*.md")):
        h.update(path.relative_to(SKILL_DIR).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


@lru_cache(maxsize=1)
def coach_prompt() -> str:
    """The coach's stable system block: ``rubric/system.md`` (persona + the six-step process) — and
    deliberately **not** the verdict contract. The coach speaks prose; only the GATE emits the
    structured verdict (via forced tool-use). Handing the coach the verdict schema makes it copy that
    JSON instead of coaching (cortex P5 #28). Cacheable prefix → cache breakpoint #1."""
    return _read("rubric/system.md")


@lru_cache(maxsize=1)
def gate_prompt() -> str:
    """The lean grader prompt for the GATE call (``rubric/gate.md``) — far smaller than the coach
    system prompt, which matters a lot for CPU-bound (Ollama) inference latency."""
    return _read("rubric/gate.md")


@lru_cache(maxsize=len(Step))
def step_guide(step: Step) -> str:
    """The gate criterion + pass threshold + anti-leak guidance for one step."""
    return _read(f"steps/{step.value}.md")
