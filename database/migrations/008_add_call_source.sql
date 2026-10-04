-- Миграция: добавить call_source для различения звонков SPRecord и бота
-- Выполнить: psql -h localhost -U analytics_user -d vikingi_analytics -f database/migrations/008_add_call_source.sql

ALTER TABLE calls ADD COLUMN IF NOT EXISTS call_source VARCHAR(20) DEFAULT 'sprecord';
UPDATE calls SET call_source = 'sprecord' WHERE call_source IS NULL;
COMMENT ON COLUMN calls.call_source IS 'Источник записи: sprecord (server5), bot (голосовой бот)';
CREATE INDEX IF NOT EXISTS idx_calls_call_source ON calls(call_source);
