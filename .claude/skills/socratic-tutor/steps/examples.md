# Step 2 — examples

**Goal:** concrete input → expected-output cases that lock down behaviour, including edges.

**Gate criterion — `pass` requires:**
- At least one **ordinary** case **and** one meaningful **edge** case (empty, single element,
  duplicates, no valid answer, boundary value), each written as `input → expected output` and traced
  to the correct result.

**Pass threshold:** score ≥ 70.

**Retry when:** only happy-path cases are given, an example's expected output is wrong (trace it and
show why), or no edge case is covered.

**Anti-leak:** validate **their** cases; do not reveal the solution. You may point out an *untested*
edge class ("what about duplicates?") without giving any part of the algorithm.
