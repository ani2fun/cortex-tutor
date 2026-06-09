# Step 1 — clarify

**Goal:** confirm deep comprehension and surface the constraints before any solving.

**Gate criterion — `pass` requires:**
- Restates the problem in the learner's own words: the **input** (type and shape), the **output**,
  and the **core task**.
- Names the key **constraints / assumptions** (sizes, value ranges, duplicates, sortedness,
  "exactly one solution", in-place vs. new structure, …) — or asks the right clarifying questions to
  pin them down.

**Pass threshold:** score ≥ 70.

**Retry when:** the restatement misses the input, output, or core task, or no constraints are
surfaced. Name exactly what's missing ("you didn't mention the input is an array of integers", "you
didn't state the 'exactly one solution' guarantee").

**Anti-leak:** do **not** hint at the algorithm. Clarification is about understanding, not solving.
