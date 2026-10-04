-- Исход дозвона до сотрудников для лидов голосового бота (админка «Лиды»).
-- Применение: docker compose exec -T postgres psql -U analytics_user -d vikingi_analytics -f database/migrations/016_telegram_leads_voice_contact_outcome.sql

ALTER TABLE telegram_leads
    ADD COLUMN IF NOT EXISTS voice_contact_outcome VARCHAR(32) NULL;

COMMENT ON COLUMN telegram_leads.voice_contact_outcome IS
    'Голосовой бот: bot_only | no_operator | transfer_started | unknown; NULL — не голос или старые записи';

CREATE INDEX IF NOT EXISTS idx_leads_voice_contact_outcome
    ON telegram_leads (voice_contact_outcome)
    WHERE source = 'phone';
