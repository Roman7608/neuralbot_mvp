-- Источник лида: telegram или phone (телефонный бот)
-- Выполнить: psql -U analytics_user -d vikingi_analytics -f database/migrations/004_add_telegram_leads_source.sql

ALTER TABLE telegram_leads ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'telegram';

COMMENT ON COLUMN telegram_leads.source IS 'Источник лида: telegram, phone';
CREATE INDEX IF NOT EXISTS idx_leads_source ON telegram_leads(source);
