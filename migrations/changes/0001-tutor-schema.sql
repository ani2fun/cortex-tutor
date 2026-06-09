--liquibase formatted sql

--changeset cortex-tutor:0001-tutor-schema
--comment: tutor schema, enum types, and the core session/message/gate/grounding tables

CREATE SCHEMA IF NOT EXISTS tutor;

CREATE TYPE tutor.step AS ENUM ('clarify', 'examples', 'approach', 'plan', 'implement', 'test');
CREATE TYPE tutor.verdict AS ENUM ('pass', 'retry', 'off_topic', 'question');
CREATE TYPE tutor.session_status AS ENUM ('active', 'completed', 'abandoned');
CREATE TYPE tutor.session_origin AS ENUM ('your_turn', 'problem_page');
CREATE TYPE tutor.msg_role AS ENUM ('system', 'user', 'coach');
CREATE TYPE tutor.judge_kind AS ENUM ('llm', 'deterministic', 'hybrid');

-- One coaching session = one learner working one problem. The FSM state lives here (source of truth).
CREATE TABLE tutor.session (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub        text                  NOT NULL,
    problem_id      text                  NOT NULL,
    origin          tutor.session_origin  NOT NULL DEFAULT 'your_turn',
    status          tutor.session_status  NOT NULL DEFAULT 'active',
    current_step    tutor.step            NOT NULL DEFAULT 'clarify',
    step_index      int                   NOT NULL DEFAULT 0,
    attempts        int                   NOT NULL DEFAULT 0,
    hint_level      int                   NOT NULL DEFAULT 0,
    coach_model     text,
    gate_model      text,
    rubric_version  text                  NOT NULL,
    running_summary text,
    summary_msg_seq int                   NOT NULL DEFAULT 0,
    byok            boolean               NOT NULL DEFAULT false,
    model_hint      text,
    input_tokens    bigint                NOT NULL DEFAULT 0,
    output_tokens   bigint                NOT NULL DEFAULT 0,
    cost_usd        numeric(12, 6)        NOT NULL DEFAULT 0,
    version         int                   NOT NULL DEFAULT 0,  -- optimistic-concurrency token
    last_turn_id    uuid,                                       -- idempotency: last committed turn
    created_at      timestamptz           NOT NULL DEFAULT now(),
    updated_at      timestamptz           NOT NULL DEFAULT now(),
    expires_at      timestamptz           NOT NULL DEFAULT now() + interval '90 days'
);

-- At most one ACTIVE session per (user, problem) — resumes hit this instead of creating a duplicate.
CREATE UNIQUE INDEX one_active_session
    ON tutor.session (user_sub, problem_id)
    WHERE status = 'active';

CREATE INDEX session_expires_at ON tutor.session (expires_at);

-- The transcript. role='system' rows are never sent back to the client.
CREATE TABLE tutor.message (
    session_id      uuid           NOT NULL REFERENCES tutor.session (id) ON DELETE CASCADE,
    seq             int            NOT NULL,
    role            tutor.msg_role NOT NULL,
    step            tutor.step     NOT NULL,
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
    session_id   uuid             NOT NULL REFERENCES tutor.session (id) ON DELETE CASCADE,
    step         tutor.step       NOT NULL,
    verdict      tutor.verdict    NOT NULL,
    score        int              NOT NULL DEFAULT 0,
    attempts     int              NOT NULL DEFAULT 0,
    missing_json jsonb,
    judge_kind   tutor.judge_kind NOT NULL DEFAULT 'llm',
    created_at   timestamptz      NOT NULL DEFAULT now(),
    updated_at   timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, step)
);

-- RAG provenance: which corpus snippets grounded which step.
CREATE TABLE tutor.grounding_ref (
    session_id   uuid        NOT NULL REFERENCES tutor.session (id) ON DELETE CASCADE,
    seq          bigint      GENERATED ALWAYS AS IDENTITY,
    step         tutor.step  NOT NULL,
    tool         text        NOT NULL,
    citation_url text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, seq)
);

--rollback DROP SCHEMA tutor CASCADE;
