--liquibase formatted sql

--changeset cortex-tutor:0006-conceptual-track
--comment: add the conceptual coaching track (four understanding steps) alongside the six coding steps

-- A session now runs one of two ladders: the six-step coding interview ('problem', the original) or
-- the four-step conceptual understanding check ('conceptual', for prose lessons). Existing rows are
-- coding sessions, so 'problem' is the default + the backfill for them.
ALTER TABLE tutor.session
    ADD COLUMN track text NOT NULL DEFAULT 'problem'
        CHECK (track IN ('problem', 'conceptual'));

-- The conceptual ladder's steps (explain → apply → analyze → defend) must be admissible everywhere a
-- step value is persisted. Extend each step CHECK to the full ten-value set (the six coding steps +
-- the four conceptual steps). Constraint names are Postgres's inline-CHECK auto-names (table_column_check).
ALTER TABLE tutor.session DROP CONSTRAINT session_current_step_check;
ALTER TABLE tutor.session ADD CONSTRAINT session_current_step_check
    CHECK (current_step IN ('clarify','examples','approach','plan','implement','test',
                            'explain','apply','analyze','defend'));

ALTER TABLE tutor.message DROP CONSTRAINT message_step_check;
ALTER TABLE tutor.message ADD CONSTRAINT message_step_check
    CHECK (step IN ('clarify','examples','approach','plan','implement','test',
                    'explain','apply','analyze','defend'));

ALTER TABLE tutor.gate DROP CONSTRAINT gate_step_check;
ALTER TABLE tutor.gate ADD CONSTRAINT gate_step_check
    CHECK (step IN ('clarify','examples','approach','plan','implement','test',
                    'explain','apply','analyze','defend'));

ALTER TABLE tutor.grounding_ref DROP CONSTRAINT grounding_ref_step_check;
ALTER TABLE tutor.grounding_ref ADD CONSTRAINT grounding_ref_step_check
    CHECK (step IN ('clarify','examples','approach','plan','implement','test',
                    'explain','apply','analyze','defend'));
