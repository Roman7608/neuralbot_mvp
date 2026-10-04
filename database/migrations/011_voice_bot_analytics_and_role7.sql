-- Голосовой бот: журнал звонков, реплик и решений о переводе (для страницы «Переводы голосового бота»).
-- Исторически: роль 7 (Roman). Снята с поддержки — см. 017_retire_admin_role7.sql; переводы бота только у роли 8.
-- Выполнить: psql ... -f database/migrations/011_voice_bot_analytics_and_role7.sql

-- Расширить допустимые роли в admin_users (1–7)
ALTER TABLE admin_users DROP CONSTRAINT IF EXISTS admin_users_role_id_check;
ALTER TABLE admin_users ADD CONSTRAINT admin_users_role_id_check CHECK (role_id >= 1 AND role_id <= 7);

CREATE TABLE IF NOT EXISTS voice_bot_sessions (
    id BIGSERIAL PRIMARY KEY,
    call_uuid VARCHAR(64) NOT NULL UNIQUE,
    greeting_type VARCHAR(32),
    asterisk_channel_id VARCHAR(256),
    state_final VARCHAR(64),
    audio_storage_path TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_voice_bot_sessions_started ON voice_bot_sessions (started_at DESC);

CREATE TABLE IF NOT EXISTS voice_bot_transcript_turns (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES voice_bot_sessions(id) ON DELETE CASCADE,
    seq INT NOT NULL,
    role VARCHAR(16) NOT NULL CHECK (role IN ('client', 'bot')),
    text TEXT,
    meta JSONB,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_voice_bot_transcript_session ON voice_bot_transcript_turns (session_id, seq);

CREATE TABLE IF NOT EXISTS voice_bot_transfer_events (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES voice_bot_sessions(id) ON DELETE CASCADE,
    transfer_category VARCHAR(64) NOT NULL,
    admin_reason VARCHAR(32),
    playback_wav VARCHAR(128),
    exten VARCHAR(32),
    tts_snippet TEXT,
    client_need VARCHAR(64),
    payload JSONB,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_voice_bot_transfer_session ON voice_bot_transfer_events (session_id);
CREATE INDEX IF NOT EXISTS idx_voice_bot_transfer_logged ON voice_bot_transfer_events (logged_at DESC);

COMMENT ON TABLE voice_bot_sessions IS 'Сессия звонка голосового бота (AudioSocket UUID)';
COMMENT ON TABLE voice_bot_transcript_turns IS 'Реплики клиента и бота (полный журнал)';
COMMENT ON TABLE voice_bot_transfer_events IS 'Структурированное решение о переводе (WAV, категория, причина для админа)';
