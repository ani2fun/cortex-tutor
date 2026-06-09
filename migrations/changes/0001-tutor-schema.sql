--liquibase formatted sql

--changeset cortex-tutor:0001-tutor-schema
--comment: tutor schema + core session/message/gate/grounding tables

-- Value sets are enforced with text + CHECK rather than native PG ENUMs: the app already validates
-- (the FSM and pydantic own the value sets), text columns avoid asyncpg/ORM enum-cast friction, and
-- a CHECK is trivially extendable vs. ALTER TYPE. CHECK keeps DB-level integrity.

CREATE SCHEMA IF NOT EXISTS tutor;

-- One coaching session = one learner working one problem. The FSM state lives here (source of truth).
CREATE TABLE tutor.session (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub        text           NOT NULL,
    problem_id      text           NOT NULL,
    origin          text           NOT NULL DEFAULT 'your_turn'
                        CHECK (origin IN ('your_turn', 'problem_page')),
    status          text           NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'completed', 'abandoned')),
    current_step    text           NOT NULL DEFAULT 'clarify'
                        CHECK (current_step IN ('clarify','examples','approach','plan','implement','test')),
    step_index      int            NOT NULL DEFAULT 0,
    attempts        int            NOT NULL DEFAULT 0,
    hint_level      int            NOT NULL DEFAULT 0,
    coach_model     text,
    gate_model      text,
    rubric_version  text           NOT NULL,
    running_summary text,
    summary_msg_seq int            NOT NULL DEFAULT 0,
    byok            boolean        NOT NULL DEFAULT false,
    model_hint      text,
    input_tokens    bigint         NOT NULL DEFAULT 0,
    output_tokens   bigint         NOT NULL DEFAULT 0,
    cost_usd        numeric(12, 6) NOT NULL DEFAULT 0,
    version         int            NOT NULL DEFAULT 0,  -- optimistic-concurrency token
    last_turn_id    uuid,                                -- idempotency: last committed turn
    created_at      timestamptz    NOT NULL DEFAULT now(),
    updated_at      timestamptz    NOT NULL DEFAULT now(),
    expires_at      timestamptz    NOT NULL DEFAULT now() + interval '90 days'
);

-- At most one ACTIVE session per (user, problem) — resumes hit this instead of duplicating.
CREATE UNIQUE INDEX one_active_session
    ON tutor.session (user_sub, problem_id)
    WHERE status = 'active';

CREATE INDEX session_expires_at ON tutor.session (expires_at);

-- The transcript. role='system' rows are never sent back to the client.
CREATE TABLE tutor.message (
    session_id      uuid           NOT NULL REFERENCES tutor.session (id) ON DELETE CASCADE,
    seq             int            NOT NULL,
    role            text           NOT NULL CHECK (role IN ('system', 'user', 'coach')),
    step            text           NOT NULL
                        CHECK (step IN ('clarify','examples','approach','plan','implement','test')),
    content         text           NOT NULL,
    content_json    jsonb,
    input_tokens    bigint         NOT NULL DEFAULT 0,
    output_tokens   bigint         NOT NULL DEFAULT 0,
    cost_usd        numeric(12, 6) NOT NULL DEFAULT 0,
    turn_id         uuid,                  -- the learner-answer turn this message belongs to
    summarized_into int,                   -- seq of the running-summary row that subsumed this, if any
    redacted        boolean        NOT NULL DEFAULT false,
    created_at      timestamptz    NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, seq)
);

-- Idempotency: a learner answer's turn_id is unique within a session (re-POST replays).
CREATE UNIQUE INDEX message_turn_id
    ON tutor.message (session_id, turn_id)
    WHERE turn_id IS NOT NULL;

-- Newest-first scan for the bounded verbatim context window.
CREATE INDEX idx_message_window ON tutor.message (session_id, seq DESC);

-- One row per cleared/attempted gate (<=6 rows/session) — the step-completion funnel backbone.
CREATE TABLE tutor.gate (
    session_id   uuid        NOT NULL REFERENCES tutor.session (id) ON DELETE CASCADE,
    step         text        NOT NULL
                     CHECK (step IN ('clarify','examples','approach','plan','implement','test')),
    verdict      text        NOT NULL CHECK (verdict IN ('pass', 'retry', 'off_topic', 'question')),
    score        int         NOT NULL DEFAULT 0,
    attempts     int         NOT NULL DEFAULT 0,
    missing_json jsonb,
    judge_kind   text        NOT NULL DEFAULT 'llm'
                     CHECK (judge_kind IN ('llm', 'deterministic', 'hybrid')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, step)
);

-- RAG provenance: which corpus snippets grounded which step.
CREATE TABLE tutor.grounding_ref (
    session_id   uuid        NOT NULL REFERENCES tutor.session (id) ON DELETE CASCADE,
    seq          bigint      GENERATED ALWAYS AS IDENTITY,
    step         text        NOT NULL
                     CHECK (step IN ('clarify','examples','approach','plan','implement','test')),
    tool         text        NOT NULL,
    citation_url text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, seq)
);

--rollback DROP SCHEMA tutor CASCADE;
