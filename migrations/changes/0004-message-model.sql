--liquibase formatted sql

--changeset cortex-tutor:0004-message-model
--comment: per-message coach model id — makes a mixed-model transcript auditable (model re-point mid-session)

-- A session's coach_model is now mutable (the operator may switch models mid-session — dual-mode), so
-- this records which model actually produced each coach message; a transcript that mixes models (local
-- qwen + cloud) stays attributable. Nullable: user/system rows and pre-migration rows have no model;
-- populated on append for coach rows on BOTH transports (homelab SSE + BYOK record).
ALTER TABLE tutor.message ADD COLUMN model text;
