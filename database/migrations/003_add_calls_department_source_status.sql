-- Миграция: отдел (ОП/СТО), источник (auto/manual), статус обработки для аналитики звонков.
-- Выполнить: psql -U analytics_user -d vikingi_analytics -f database/migrations/003_add_calls_department_source_status.sql

ALTER TABLE calls ADD COLUMN IF NOT EXISTS department VARCHAR(10) DEFAULT 'OP';
ALTER TABLE calls ADD COLUMN IF NOT EXISTS source_type VARCHAR(10) DEFAULT 'manual';
ALTER TABLE calls ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending';

COMMENT ON COLUMN calls.department IS 'OP или STO';
COMMENT ON COLUMN calls.source_type IS 'auto или manual';
COMMENT ON COLUMN calls.status IS 'pending, transcribed, analyzed, error';

CREATE INDEX IF NOT EXISTS idx_calls_department ON calls(department);
CREATE INDEX IF NOT EXISTS idx_calls_source_type ON calls(source_type);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
