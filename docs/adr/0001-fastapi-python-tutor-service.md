# ADR 0001 — The tutor is a standalone Python / FastAPI service

- **Status:** Accepted
- **Date:** 2026-06-09
- **Context repo:** `cortex-tutor` (the host app `cortex` is Scala 3 / ZIO / zio-http / Scala.js)

## Context

The Cortex "Your Turn" tutor is a **stateful, LLM-orchestrating agent**: a six-step FSM with per-gate
evaluation, MCP grounding, streamed coach replies, Postgres-backed session memory, and BYOK. We had
to choose its language/runtime and whether it lives *inside* the Scala `cortex` server or as its own
service.

## Decision

Build the tutor as a **standalone Python (FastAPI) service in its own repo**, integrating with
`cortex` over HTTP and sharing only Keycloak (auth) and Postgres. The Scala server stays **out of the
LLM path**.

## Why

- **Workload fit.** LLM orchestration is **I/O-bound, low-RPS, long-latency, streaming**. FastAPI +
  `asyncio` is purpose-built for this — one worker juggles many concurrent in-flight turns/SSE streams
  while they await the model. Python's GIL is a non-issue here (the heavy work is in the Anthropic API
  + Postgres, not in Python).
- **Ecosystem.** The Anthropic SDK, the **MCP Python SDK (Streamable HTTP)**, structured-output /
  strict-tool-use helpers, eval + codegen tooling, and ~all docs/examples are Python-first and
  fastest-moving. This project also doubles as **Claude Certified Architect** prep, whose material is
  Python-centric — building it where the reference material lives is a feature.
- **Decoupling.** Keeping the slow LLM path out of the request-serving Scala app lets the tutor
  deploy, scale, and iterate independently, and not bloat the Scala build.
- **Author fluency.** A JVM/Python engineer.
- **For the record — JVM is *capable*** (official Anthropic Java SDK + a Java MCP SDK exist). The
  deciders were ecosystem density/velocity, cert alignment, fluency, and decoupling — **not**
  capability.

## Scalability

The service is **stateless** — Postgres is the source of truth and the FSM lives in the DB, so there
are no sticky sessions. It therefore **scales horizontally** on K3s (add replicas behind the ingress,
HPA on in-flight turns), and **independently of `cortex`** (separate deployment; they share only
Postgres + Keycloak and don't contend — `cortex` serves many cheap content reads, the tutor serves few
expensive LLM turns). The first real ceilings are **LLM cost / latency / rate-limits**, **wk-1 Ollama
capacity**, and **Postgres connections** — none of which are FastAPI's doing, and all addressed in the
design (cost caps, edge rate-limit, pooling).

Python/FastAPI would be the *wrong* choice for a CPU-bound, sub-millisecond, high-RPS hot path (e.g.
the `go-judge` code executor). This is not that.

## Alternatives considered

- **Tutor inside the Scala/ZIO `cortex` server** — one stack, shared types, direct auth/runner reuse;
  but a thinner, slower-moving agent ecosystem, couples the LLM path into the request app, and diverges
  from the Python-centric cert material.
- **Node/TypeScript** — excellent MCP/Anthropic SDKs; but the author is JVM/Python and the SPA is
  Scala.js (not a Node shop).
- **Anthropic Managed Agents** — rejected: it puts the loop + state on Anthropic's side, conflicting
  with gate determinism, Postgres-as-source-of-truth, audit, and the BYOK split.

## Consequences

- **(+)** Right ecosystem, clean decoupling, independent horizontal scaling, cert-aligned.
- **(−)** **Polyglot cost:** two stacks, two CI pipelines, two Dockerfiles, and a **cross-repo OpenAPI
  contract seam** (`tutor-openapi.yaml`) — drift risk, mitigated by a CI drift-check on both repos.
- It is **cheap to reverse early**: the FSM (pure logic), rubric (markdown), contract (YAML), and
  schema (SQL) are language-agnostic; only the FastAPI app + async repo are Python-specific.
