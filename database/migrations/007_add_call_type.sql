-- Миграция 007: колонка call_type для типа звонка (ОП вх./исх., СТО вх./исх., Прочие)
-- Выполнить: psql -h localhost -U analytics_user -d vikingi_analytics -f database/migrations/007_add_call_type.sql

ALTER TABLE calls ADD COLUMN IF NOT EXISTS call_type VARCHAR(20);

COMMENT ON COLUMN calls.call_type IS 'OP_IN, OP_OUT, STO_IN, STO_OUT, OTHER — определяется по транскрипту';

CREATE INDEX IF NOT EXISTS idx_calls_call_type ON calls(call_type);
