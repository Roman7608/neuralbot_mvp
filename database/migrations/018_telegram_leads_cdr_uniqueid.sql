-- Дедупликация импорта CDR Инфолады (Master.csv) в лиды.
-- Применение: psql -U analytics_user -d vikingi_analytics -f database/migrations/018_telegram_leads_cdr_uniqueid.sql

ALTER TABLE telegram_leads
    ADD COLUMN IF NOT EXISTS cdr_uniqueid VARCHAR(128) NULL;

COMMENT ON COLUMN telegram_leads.cdr_uniqueid IS
    'Уникальный id строки CDR (Asterisk uniqueid) — не дублировать при повторном импорте';

-- В PostgreSQL несколько NULL в UNIQUE допускаются — голосовые лиды без CDR не конфликтуют.
CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_leads_cdr_uniqueid
    ON telegram_leads (cdr_uniqueid);
