--liquibase formatted sql

--changeset cortex-tutor:0002-gate-call
--comment: append-only per-invocation gate audit log (P6 evals — the production→dataset loop)

-- One row PER gate invocation. tutor.gate is an upsert keyed (session_id, step) — only the LAST
-- verdict per step survives there — so this table is what makes flakiness measurable after the
-- fact. raw_json is the UNVALIDATED model output (the field that quantifies schema fragility;
-- NULL when the provider itself errored) and outcome classifies the validate → repair → fail-safe
-- path taken. See evals/README.md.
CREATE TABLE tutor.gate_call (
    session_id           uuid        NOT NULL REFERENCES tutor.session (id) ON DELETE CASCADE,
    seq                  bigint      GENERATED ALWAYS AS IDENTITY,
    turn_id              uuid,
    step                 text        NOT NULL
                             CHECK (step IN ('clarify','examples','approach','plan','implement','test')),
    answer_seq           int         NOT NULL,
    rubric_version       text        NOT NULL,
    provider             text        NOT NULL,
    model                text        NOT NULL,
    outcome              text        NOT NULL
                             CHECK (outcome IN ('valid','coerced','failsafe_schema','failsafe_provider')),
    raw_json             jsonb,
    verdict              text        NOT NULL CHECK (verdict IN ('pass','retry','off_topic','question')),
    score                int         NOT NULL DEFAULT 0,
    missing_json         jsonb,
    hint                 text        NOT NULL DEFAULT '',
    problem_context_hash text        NOT NULL,
    latency_ms           int         NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, seq)
);

-- The dataset-extraction scan: all calls for a session in answer order.
CREATE INDEX idx_gate_call_extract ON tutor.gate_call (session_id, answer_seq);
