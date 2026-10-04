-- Таблица непринятых входящих звонков (CDR от Инфолады)
-- Данные импортируются скриптом из выгрузки CDR
-- Выполнить: sudo -u postgres psql -d vikingi_analytics -f database/migrations/009_add_incoming_calls.sql

CREATE TABLE IF NOT EXISTS incoming_calls (
    id SERIAL PRIMARY KEY,
    call_date DATE NOT NULL,
    call_time TIME NOT NULL,
    caller_phone VARCHAR(50),
    did VARCHAR(20),
    status VARCHAR(50) DEFAULT 'unanswered',
    reason VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_incoming_calls_date ON incoming_calls(call_date);
CREATE INDEX IF NOT EXISTS idx_incoming_calls_caller ON incoming_calls(caller_phone);

COMMENT ON TABLE incoming_calls IS 'Непринятые входящие (бот занят, сброс до ответа и т.д.)';
