# Step 6 — test / complexity

**Goal:** verify the code against the cases and reason about complexity. **Passing this step
completes the session.**

**Gate criterion — `pass` requires:**
- Runs or dry-runs the code against the `examples` cases (including an edge case) and reaches the
  **correct outputs** — or identifies a failing case and fixes it — **and** states the final **time
  and space complexity**, justified, matching the chosen approach.

**Evidence:** the learner's editor attaches their current code as a `[workbench snapshot — …]`
block and the output of running it as a `[run result]` block. Check the run output against the
expected outputs from `examples`. A bare claim that the cases pass — no `[run result]` block and no
step-by-step dry run written out in the message — does not meet the criterion: `retry` and ask the
learner to run the cases (or dry-run one, including an edge case). A run result alone is not enough
either: the answer itself must state the final time and space complexity — complexity mentioned
only at an earlier step does not count. No complexity statement in this answer → `retry`.

**Pass threshold:** score ≥ 70.

**Retry when:** an example still fails, or the stated complexity is wrong or unjustified.

**Close-out (on `pass`):** give a one-line retro — what the learner did well, the single key lesson,
the final `Time: O(…)  Space: O(…)`, and a real-interviewer follow-up ("can you do better on space?",
"how would this change if the input were sorted?").
