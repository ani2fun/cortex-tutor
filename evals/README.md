# Gate evals — design + harness (P6, plan §8)

The gate is the tutor's only progress decision ("evaluate, then advance"), so its failure modes are
the product's failure modes. This package quantifies them. The design was derived from ONE recorded
production session (`2afb4b05`, two-sum, 2026-06-09 — 21 messages, gates across all six steps) which
exhibits every known defect class at once:

| Exhibit (msg seq) | Defect class | What happened |
|---|---|---|
| 10→12 | **Schema fragility** | The identical plan submitted twice; the model scored it ~95, but 95 isn't in the score enum (and `missing` came back as a `""` string) → strict validation + the old repair both failed → fail-safe `RETRY/0`. A passing answer was silently graded as a failure. |
| 21 | **Fail-safe leaks to the learner** | The coach reply is literally `"Let's take another pass at that."` — the `retry_failsafe()` default surfacing verbatim, twice in a row at `test`. |
| 16–20 | **Evidence gap** | implement/test answers say "I ran it in the editor"; `submit_turn` drops `code`/`language`/`runResult`, so the gate retries with "claims correct output" — it can never verify a claim it never sees. |
| 3–19 | **Coach JSON leak** (fixed in `cb592a3`) | Every coach bubble is a fenced verdict JSON. NB: that JSON is the *coach's own hallucination*, not the gate verdict (seq 3 says `retry/40` where the gate recorded `pass/90`) — recorded coach text is **not** gate ground truth. |
| (observed live) | **Step hallucination** | The gate graded as if at `implement` while the FSM was at `plan`. |

The point-fixes in `cb592a3` (`_coerce` hardening, coach/gate prompt split) address exhibits 1 and 4
*anecdotally*. This harness exists to make such claims **measurable**: every gate change ships with
before/after numbers, and a frozen baseline catches regressions.

## 1. What to record (production → dataset loop)

The `tutor.gate` table is an upsert keyed `(session_id, step)` — only the *last* verdict per step
survives, and nothing pre-validation is kept. So production recording gains an **append-only**
`tutor.gate_call` table, one row per gate invocation, written by `apply_turn` in the same
transaction as the turn:

- `step`, `answer_seq` (FK-ish into `message.seq` — the answer + transcript are reconstructable),
  `turn_id`, `rubric_version` (prompt provenance), `provider` + `model` (which judge),
  `problem_context_hash` (grounding provenance without storing ~20 kB per call)
- `raw_json` — the **unvalidated** tool output, the field that quantifies schema fragility
  (`NULL` when the provider itself errored)
- `outcome` — `valid | coerced | failsafe_schema | failsafe_provider`
- final `verdict`/`score`/`missing_json`/`hint` (post-validation), `latency_ms`

The seam: `gate.evaluate` returns a `GateEvaluation` (verdict + audit fields) instead of a bare
`GateVerdict`. Orchestration uses `.verdict`; persistence and the eval runner get the audit record
from the **same code path** — the runner measures the production gate, not a copy of it.

## 2. The dataset (cases)

`evals/datasets/*.jsonl`, one case per line:

```jsonc
{
  "case_id": "two-sum-2afb4b05-s10",
  "kind": "recorded",                  // recorded | cross_step_probe | synthetic
  "problem_id": "data-structures-and-algorithms/.../problems/two-sum",
  "step": "plan",
  "transcript": [{"role": "user", "content": "…"}],  // faithful: what the gate saw, incl. flawed coach turns
  "answer": "…",
  "problem_context": "…",              // FROZEN at extraction (reproducible forever)
  "expected": {
    "verdict": "pass",                 // exact-match target
    "min_score": 70,                   // score band, not exact score (exact is over-brittle)
    "rationale": "complete ordered plan incl. guard, sort caveat acknowledged…"
  },
  "labeller": "fable-5 draft — PENDING HUMAN REVIEW"
}
```

Decisions:
- **Faithful transcripts.** Cases record what the gate actually saw — including the leaked-JSON
  coach turns. Cleaning history would measure a gate nobody ran.
- **Frozen context.** `problem_context` is embedded at extraction (the grounding corpus drifts with
  content edits; a frozen suite must not). `gate_call.problem_context_hash` reveals drift between
  recording and extraction.
- **Cross-step probes** are synthesized from recorded *passing* answers replayed at the **wrong**
  step (e.g. the approach answer submitted at `plan`). Expected: never `pass`. A `pass` here is a
  counted step-hallucination.
- **Labels**: machine-drafted, human-reviewed (the plan's κ-calibration starts when a second judge
  exists; for now the label file is the single source of truth and is versioned).

## 3. Metrics (what "flaky" means, numerically)

Each case is replayed **N times (default 5)** against the live provider. Three independent axes:

1. **Schema health** — of all raw outputs: `% valid` first-try, `% coerced` (repair saved it),
   `% failsafe_schema`, `% failsafe_provider`; plus a **failure-shape histogram** (score-out-of-enum,
   list-field-as-string/null, unknown-verdict, extra-keys, not-JSON) so a prompt/schema fix targets
   the actual shapes. Exhibit 1 is `failsafe_schema` + shape `score-out-of-enum, missing-as-string`.
2. **Decision quality vs labels** — confusion matrix over {pass, retry, off_topic, question},
   **false-pass weighted ×5** (an unearned advance is the worst failure; a false retry is friction).
   Headlines: `false_pass_rate`, `false_retry_rate`, `weighted_error`. Score-band violations
   (`pass` below `min_score`) are tracked separately, not folded into verdict accuracy.
3. **Stability (flakiness proper)** — per case across N replays: modal-verdict agreement, the
   **decision-flip rate** (fraction of cases where pass↔non-pass varies across replays — the number
   that directly measures "same answer, different fate", exhibit 1's user experience), and score
   spread (max−min). Plus `latency_ms` p50/p95 per provider.

Every report stamps `rubric_version`, provider, model, git SHA, N — comparisons are only valid
within a stamp.

**Known lever to test first:** `AnthropicGateProvider` sets no `temperature` → the gate samples at
the SDK default (1.0). The Ollama gate pins 0. Measure flip-rate at default, pin `temperature=0`,
re-measure — the first fix-with-proof through the harness.

## 4. Runner + regression gate

- `uv run python -m evals.extract --session <uuid> --out evals/datasets/<name>.jsonl` — pg → cases
  (labels then edited by hand).
- `uv run python -m evals.gate_runner evals/datasets/<name>.jsonl --replays 5` — builds the provider
  via the **same factory + env** as the service (`FORCE_LOCAL` picks Ollama vs Claude), assembles
  prompts via the **same `tutor.skills.loader`** files prod loads, calls the **same
  `gate.evaluate`**. Emits `evals/out/<run>/metrics.json` + `report.md`.
- `--baseline evals/baselines/<name>.json` — compare mode: exits non-zero when `false_pass_rate` or
  `flip_rate` rise, or `schema_valid_rate` falls, beyond the thresholds stored in the baseline file.
  This is the CI hook (plan §8's `regression.py` role).

Out of scope for this slice (deliberate): coach-quality LLM-judge suite, κ calibration (needs a
second judge), funnel/Prometheus metrics (separate P6 sub-slice), and the `submit_turn`
code/runResult threading (exhibit 3 — the harness's first *downstream customer*: add evidence-bearing
cases, then change the prompt, then prove it).
