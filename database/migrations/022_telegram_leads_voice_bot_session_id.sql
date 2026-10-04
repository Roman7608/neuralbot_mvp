-- Связь лида голосового бота с сессией аналитики (страница «Лиды» — колонка «Перевод / сброс»).
-- Применение: psql -U analytics_user -d vikingi_analytics -f database/migrations/022_telegram_leads_voice_bot_session_id.sql

ALTER TABLE telegram_leads
    ADD COLUMN IF NOT EXISTS voice_bot_session_id BIGINT REFERENCES voice_bot_sessions (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_telegram_leads_voice_bot_session_id
    ON telegram_leads (voice_bot_session_id)
    WHERE voice_bot_session_id IS NOT NULL;

COMMENT ON COLUMN telegram_leads.voice_bot_session_id IS
    'voice_bot_sessions.id при сохранении лида из CallSession; прямой JOIN в list_leads вместо только эвристики по телефону/времени.';
