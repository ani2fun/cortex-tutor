--liquibase formatted sql

--changeset cortex-tutor:0003-byok-judge-kind
--comment: admit 'byok' as a gate judge_kind (P7 — client-side gate, verdict recorded via byok-record)

-- A BYOK turn's verdict is computed in the learner's browser with their own key and merely
-- *recorded* here — a different trust boundary from the server-side 'llm' gate, so it gets its
-- own judge_kind rather than masquerading as 'llm'.
ALTER TABLE tutor.gate DROP CONSTRAINT gate_judge_kind_check;
ALTER TABLE tutor.gate ADD CONSTRAINT gate_judge_kind_check
    CHECK (judge_kind IN ('llm', 'deterministic', 'hybrid', 'byok'));
