"""Loads the version-controlled Socratic coaching rubrics (``.claude/skills/*-tutor/``).

This is *externalized prompt content* (NOT the Anthropic Agent Skills API). The loader keeps the
**coach** prompt (persona, prose) and the **gate** prompt (lean grader) separate, so the coach is
never handed the verdict schema it must not emit — only the gate is, and it does so via forced
tool-use, not this markdown. The eval runner reads the SAME files the service loads, so a rubric edit
is exactly what CI gates, and ``rubric_version()`` stamps each session for auditability + eval-baseline
pinning.

There are two rubric trees, one per :class:`~tutor.domain.steps.Track`:
  * ``socratic-tutor/`` — the six-step coding interview (``Track.PROBLEM``);
  * ``conceptual-tutor/`` — the four-step understanding check (``Track.CONCEPTUAL``).

A step's tree is resolved from the step itself (``track_of``), so callers that hold a step never pass
a track; only the prompt-level helpers (``coach_prompt`` / ``gate_prompt``) take one, defaulting to the
PROBLEM track for backwards compatibility.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from tutor.domain.steps import Step, Track, track_of

#: Root holding both rubric trees (``tutor/skills/loader.py`` → repo root is parents[2]).
SKILLS_ROOT = Path(__file__).resolve().parents[2] / ".claude" / "skills"

#: The on-disk skill directory backing each track.
_DIR_BY_TRACK: dict[Track, str] = {
    Track.PROBLEM: "socratic-tutor",
    Track.CONCEPTUAL: "conceptual-tutor",
}


def _skill_dir(track: Track) -> Path:
    return SKILLS_ROOT / _DIR_BY_TRACK[track]


def _read(track: Track, rel: str) -> str:
    return (_skill_dir(track) / rel).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def rubric_version() -> str:
    """A short content hash over EVERY rubric file (both trees) — stamped onto each session so a
    prompt change is auditable and the eval baseline can be pinned to a specific rubric."""
    h = hashlib.sha256()
    for path in sorted(SKILLS_ROOT.rglob("*.md")):
        h.update(path.relative_to(SKILLS_ROOT).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


@lru_cache(maxsize=len(Track))
def coach_prompt(track: Track = Track.PROBLEM) -> str:
    """The coach's stable system block for ``track``: ``rubric/system.md`` (persona + the step
    process) — and deliberately **not** the verdict contract. The coach speaks prose; only the GATE
    emits the structured verdict (via forced tool-use). Handing the coach the verdict schema makes it
    copy that JSON instead of coaching (cortex P5 #28). Cacheable prefix → cache breakpoint #1."""
    return _read(track, "rubric/system.md")


@lru_cache(maxsize=len(Track))
def gate_prompt(track: Track = Track.PROBLEM) -> str:
    """The lean grader prompt for the GATE call (``rubric/gate.md``) for ``track`` — far smaller than
    the coach system prompt, which matters a lot for CPU-bound (Ollama) inference latency."""
    return _read(track, "rubric/gate.md")


@lru_cache(maxsize=len(Step))
def step_guide(step: Step) -> str:
    """The gate criterion + pass threshold + anti-leak guidance for one step (from the step's own
    track's tree)."""
    return _read(track_of(step), f"steps/{step.value}.md")
