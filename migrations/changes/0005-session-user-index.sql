--liquibase formatted sql

--changeset cortex-tutor:0005-session-user-index
--comment: index (user_sub, status) so per-user session-count quota checks are an index scan, not a seq scan

-- Storage-quota enforcement counts a user's (active, completed) sessions on every create; without a
-- user_sub index that is a sequential scan. The existing one_active_session index is partial
-- (status='active' only), so it cannot serve the (active + completed) count.
CREATE INDEX idx_session_user ON tutor.session (user_sub, status);
