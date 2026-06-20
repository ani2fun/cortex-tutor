# Step 3 — analyze

**Goal:** weigh the concept honestly — where it helps, where it hurts, and against what.

**Gate criterion — `pass` requires:**
- Names **when it helps vs. when it breaks** (or doesn't apply), with at least **one concrete
  trade-off or failure mode** explained — *why*, not just "it's slow", and
- ideally **an alternative** and what it costs.

**Pass threshold:** score ≥ 70.

**Retry when:** only upsides are given, a failure mode is asserted without a reason, or the trade-off
is hand-wavy. Push for the mechanism behind the limit ("*why* does it fall over at that point?").

**Anti-leak:** don't make the final recommendation for them (that's `defend`). Push the learner to
reason about the limits rather than listing them yourself.
