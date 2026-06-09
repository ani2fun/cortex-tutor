"""Loads the version-controlled Socratic coaching rubric (``.claude/skills/socratic-tutor/``).

This is *externalized prompt content* (NOT the Anthropic Agent Skills API). The loader composes the
stable system block (cache breakpoint #1) and exposes the per-step gate criterion + the verdict
contract. The eval runner reads the SAME files the service loads, so a rubric edit is exactly what
CI gates, and ``rubric_version()`` stamps each session for auditability + eval-baseline pinning.
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
def system_prompt() -> str:
    """The stable system block: ``rubric/system.md`` + the verdict contract. Cacheable prefix that
    does not change within (or across) sessions, so it sits at cache breakpoint #1."""
    return _read("rubric/system.md") + "\n\n---\n\n" + _read("verdict-contract.md")


@lru_cache(maxsize=len(Step))
def step_guide(step: Step) -> str:
    """The gate criterion + pass threshold + anti-leak guidance for one step."""
    return _read(f"steps/{step.value}.md")
