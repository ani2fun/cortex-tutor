---
name: socratic-tutor
description: The six-step Socratic coaching rubric for the Cortex "Your Turn" tutor — per-step gate criteria, anti-leak directives, and the strict verdict contract. Loaded by the tutor service and read by the eval runner.
---

# Socratic Tutor — coaching rubric

This directory is the **single source of truth for the tutor's coaching behaviour**. It is
*externalized, version-controlled prompt content* — composed into the model calls by
`tutor/skills/loader.py` and read by the eval runner (`evals/`), so a change here is exactly what CI
gates.

> **Not** the Anthropic Agent Skills API (no `/v1/skills`, no model-side auto-load). We borrow the
> `.claude/skills/` convention purely to keep the prompt content reviewable and diffable.

## The six steps (gates), always in order

`clarify → examples → approach → plan → implement → test`

The learner advances only when their answer **clears the current step's gate**. The model EVALUATES
the gate; the **server** decides advancement (`pass` → advance) — the model can never fabricate it.

## Prime directives (override everything)

- **Coach, don't solve** — leading questions; the learner does the thinking and the typing.
- **Gate before advance** — a gate is earned, never given to be nice; honest scores.
- **Never spoil** — no approach before `approach`, no code before `implement`.
- **Escalate hints gradually** — nudge → bigger hint → worked hint (level 3, `implement` only).
- **Make them articulate** — spoken-style, full-sentence reasoning, not keywords.
- **Complexity is non-negotiable** — tie to time/space; never let a hand-wavy Big-O pass.
- **Stay grounded** — only the problem + the provided corpus context; invent nothing.

## Files

| File | Role |
|---|---|
| `rubric/system.md` | The stable system prompt — the coach persona + the process. **Cache breakpoint #1.** |
| `verdict-contract.md` | The strict structured verdict the GATE emits. |
| `steps/<step>.md` | Per-step gate criterion + pass threshold + retry guidance + anti-leak. |

## How it's loaded

Each turn the server composes: `system.md` + `verdict-contract.md` (stable, cached) → the active
`steps/<step>.md` → the grounded problem context → the bounded transcript. The "current step = N" is
injected as a mid-conversation system message so the cached prefix is never invalidated. See
`tutor/skills/loader.py` and the design doc §4.3.
