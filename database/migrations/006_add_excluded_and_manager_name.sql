-- Миграция 006: скрытие звонков из аналитики + имя менеджера
-- Выполнить: psql -h localhost -U analytics_user -d vikingi_analytics -f database/migrations/006_add_excluded_and_manager_name.sql

-- 1. Флаг исключения звонка из аналитики
ALTER TABLE calls ADD COLUMN IF NOT EXISTS excluded BOOLEAN DEFAULT FALSE;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS excluded_at TIMESTAMP;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS excluded_by VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_calls_excluded ON calls(excluded);

-- 2. Имя менеджера в call_metadata
ALTER TABLE call_metadata ADD COLUMN IF NOT EXISTS manager_name VARCHAR(200);
ALTER TABLE call_metadata ADD COLUMN IF NOT EXISTS manager_name_source VARCHAR(20) DEFAULT 'auto';

-- 3. Также добавим manager_name прямо в calls для быстрого доступа без JOIN
ALTER TABLE calls ADD COLUMN IF NOT EXISTS manager_name VARCHAR(200);
ALTER TABLE calls ADD COLUMN IF NOT EXISTS manager_name_source VARCHAR(20) DEFAULT 'auto';

CREATE INDEX IF NOT EXISTS idx_calls_manager_name ON calls(manager_name);

COMMENT ON COLUMN calls.excluded IS 'Звонок скрыт из аналитики (нерепрезентативный)';
COMMENT ON COLUMN calls.excluded_at IS 'Когда звонок был скрыт';
COMMENT ON COLUMN calls.excluded_by IS 'Кто скрыл (логин пользователя)';
COMMENT ON COLUMN calls.manager_name IS 'Имя менеджера (auto из транскрипции или manual)';
COMMENT ON COLUMN calls.manager_name_source IS 'Источник имени: auto или manual';
