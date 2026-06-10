# Step 5 — implement

**Goal:** turn the plan into working code.

**Gate criterion — `pass` requires:**
- **Complete, plausible code** in the chosen language that faithfully implements the plan (sound
  structure, sensible names). It need **not** be bug-free here — bugs surface in `test`.

**Evidence:** the learner's editor attaches their code to the message as a
`[workbench snapshot — <language>]` block, plus a `[run result]` block when they ran it. Judge the
snapshot (or code written directly in the message) as the implementation. A `[run result]`, when
present, informs the judgement (does it compile / run? — not necessarily correct yet); a
`[run result: none]` is fine at this step — running is not required to pass `implement`.
- `[workbench: no code attached]` with no code in the message itself means the learner only
  **claimed** an implementation. A claim never passes, however confident or detailed — `retry` and
  ask them to share their code.

**Pass threshold:** score ≥ 60.

**Retry when:** the code clearly diverges from the plan, is incomplete, or has an obvious red flag.
Nudge with leading questions ("are you sure about the order of those two lines?") — do **not** rewrite
their code.

**Anti-leak / hints:** this is the **only** step where a level-3 *worked* hint is permitted, and only
as a last resort after smaller hints have failed — with a note that struggling here is where the
learning happens.
