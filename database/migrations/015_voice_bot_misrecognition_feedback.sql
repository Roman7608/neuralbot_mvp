-- Отметки «бот неверно распознал речь» из админки (журнал переводов голосового бота).
-- Применение: psql с тем же DSN, что и остальные миграции.

CREATE TABLE IF NOT EXISTS voice_bot_misrecognition_feedback (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES voice_bot_sessions(id) ON DELETE CASCADE,
    transcript_seq INT,
    stt_text_snapshot TEXT,
    reporter_login VARCHAR(128) NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    expected_tag VARCHAR(128),
    status VARCHAR(32) NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vbmf_session ON voice_bot_misrecognition_feedback(session_id);
CREATE INDEX IF NOT EXISTS idx_vbmf_created ON voice_bot_misrecognition_feedback(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vbmf_status ON voice_bot_misrecognition_feedback(status);

COMMENT ON TABLE voice_bot_misrecognition_feedback IS 'ОКК: ошибка STT/понимания по реплике клиента (голосовой бот)';
