# cortex-tutor

A stateful **Socratic interview-coaching agent** for [Cortex](https://cortex.kakde.eu) — the "Your
Turn" feature. After a reader finishes a tutorial, the tutor coaches them through a related problem
using a fixed **six-step framework** — `clarify → examples → approach → plan → implement →
test/complexity` — **evaluating the answer at each gate before advancing**. A stateful agent, not a
quiz.

Standalone Python service (FastAPI), deployed on the homelab K3s alongside the Scala `cortex` app. The
cortex frontend calls it directly; it validates the same Keycloak JWTs.

## Architecture (one paragraph)

**Stateless FastAPI handlers + a Postgres FSM as the source of truth** — the six-step loop lives in
code+DB so the gate transition is deterministic and audited (the model can never fabricate an advance).
Each turn: **gate first** (Haiku, forced strict tool-use, non-streamed) → **coach second** (Sonnet,
streamed). Grounding comes from a separate read-only **MCP server** (Streamable HTTP) over the Cortex
corpus. See the full design in the cortex repo's design doc / `docs/adr/`.

## Why a separate Python/FastAPI service

LLM orchestration is **I/O-bound, low-RPS, and streaming** — a fit for FastAPI + `asyncio` — and the
Anthropic + MCP + eval ecosystem (and the CCA material) is Python-first. Keeping it a standalone,
**stateless** service (Postgres is the source of truth) decouples it from the Scala `cortex` app and
lets it scale horizontally and independently. Full rationale, alternatives, and the
scalability/trade-off analysis: **[ADR 0001](docs/adr/0001-fastapi-python-tutor-service.md)**.

## Model tiers

| Tier | Who | Backend | Cost |
|---|---|---|---|
| **Homelab** | `COACH_HOMELAB_USERS` allowlist (default `ani2fun`) | Claude (server key) + wk-1 Ollama fallback | owner |
| **BYOK** | any other signed-in user | their own provider key, **client-direct** (key never touches this server) | user |
| **Locked** | no key / not signed in | — (editor-only on the frontend) | — |

## Quickstart (dev)

```bash
uv sync                      # create the venv (installs Python 3.12 if needed)
cp .env.example .env         # fill in ANTHROPIC_API_KEY etc. (or AUTH_ENABLED=false to skip Keycloak)
make test                    # run the suite (the pure FSM has no external deps)

./bin/dev                    # ★ one command: Postgres (:5433) + migrate + FastAPI autoreload (:8000)
# — or the underlying make targets directly:
make dev                     # FastAPI autoreload on :8000 (assumes stores already up)
make up                      # full container stack (postgres + liquibase + tutor) on :8000
```

> **Run both stacks together** (this tutor + the Scala `cortex` app, so the SPA's live coach works
> end-to-end) with **`cortex/scripts/devcombined`** from the sibling `cortex` repo — it launches
> `cortex/bin/dev` and this `bin/dev` and wires `CORTEX_TUTOR_BASE_URL`. Requires cortex and cortex-tutor
> to be siblings under one parent dir.

## Layout

```
tutor/
  app.py config.py auth.py          # FastAPI app, settings, Keycloak JWT verify
  domain/{steps,verdict,fsm}.py     # pure six-step state machine (no IO)
  orchestration/                    # per-turn: assemble → gate → transition → coach → persist
  models/                           # provider router: Anthropic / Ollama / client-direct (BYOK)
  grounding/                        # MCP client + context assembly
  persistence/{models,repo}.py      # SQLAlchemy 2.0 async over the `tutor` Postgres schema
  observability/                    # metrics, structured logs, tracing
  skills/loader.py                  # loads the coaching rubric (below)
grounding_mcp/                      # the standalone read-only MCP grounding server
migrations/                         # Liquibase changelog (schema `tutor`)
api/tutor-openapi.yaml              # the API contract — single source of truth (Scala client vendors it)
.claude/skills/socratic-tutor/      # the six-step rubric + per-gate criteria + verdict contract (the core IP)
evals/                              # gate-judge + coach eval suites (CI-gated)
```

> Secrets (`ANTHROPIC_API_KEY`, `MCP_SERVICE_TOKEN`) are never committed and never logged. BYOK keys
> never reach this server.

## Learn more

A first-principles tour of this service — the gate/coach split, the six-step FSM, tiers & BYOK, the
turn lifecycle (SSE vs client-direct), and the grounding MCP + the `socratic-tutor` skill — lives in
the Cortex **Cortex Onboarding** book:

- **[Cortex Tutor section](https://cortex.kakde.eu/cortex-onboarding/cortex-tutor/what-the-tutor-is)** — what it is, architecture, tiers & BYOK, the turn lifecycle, grounding & the skill.
- **[Runbooks → Launch the Tutor](https://cortex.kakde.eu/cortex-onboarding/runbooks/local-dev/launch-the-tutor)** — wiring this service to the SPA locally (ports, env, the JWT detail).
- **[System Design → Cortex storage & cost](https://cortex.kakde.eu/system-design/capstones/cortex-storage-and-cost)** — the per-coached-session token-cost model and why BYOK is what makes the tutor scale.

Contract source of truth: [`api/tutor-openapi.yaml`](api/tutor-openapi.yaml) (the cortex Scala client hand-ports it into `shared/.../tutor/TutorContract.scala`).
