--liquibase formatted sql

--changeset cortex-tutor:0007-conceptual-track-gate-call
--comment: extend the gate_call step CHECK to the conceptual steps (gate_call is defined in 0002, so 0006 missed it)

-- 0006 widened the step CHECK on session/message/gate/grounding_ref, but the append-only audit table
-- `gate_call` (added in 0002, not 0001) has its OWN step CHECK that still rejected the conceptual steps —
-- so every conceptual turn failed at the gate-audit insert (CheckViolationError on gate_call_step_check),
-- rolling back the whole turn. Bring it to the same ten-value set as the others.
ALTER TABLE tutor.gate_call DROP CONSTRAINT gate_call_step_check;
ALTER TABLE tutor.gate_call ADD CONSTRAINT gate_call_step_check
    CHECK (step IN ('clarify','examples','approach','plan','implement','test',
                    'explain','apply','analyze','defend'));
