-- Схема базы данных для проекта Vikingi
-- База данных: vikingi_analytics
-- PostgreSQL 16

-- ============================================
-- 1. ТАБЛИЦЫ ДЛЯ АНАЛИТИКИ ЗВОНКОВ
-- ============================================

-- Таблица записей звонков
CREATE TABLE IF NOT EXISTS calls (
    id SERIAL PRIMARY KEY,
    file_path VARCHAR(500) NOT NULL UNIQUE,
    file_name VARCHAR(255) NOT NULL,
    internal_number INTEGER NOT NULL,  -- Внутренний номер менеджера (1-12)
    call_date DATE NOT NULL,
    call_time TIME NOT NULL,
    duration_seconds INTEGER,  -- Длительность звонка в секундах
    file_size_bytes BIGINT,  -- Размер файла в байтах
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индекс для быстрого поиска по дате и номеру
CREATE INDEX IF NOT EXISTS idx_calls_date_number ON calls(call_date, internal_number);
CREATE INDEX IF NOT EXISTS idx_calls_date ON calls(call_date);

-- Таблица транскрипций звонков
CREATE TABLE IF NOT EXISTS call_transcriptions (
    id SERIAL PRIMARY KEY,
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    transcription_text TEXT NOT NULL,  -- Полный текст транскрипции
    segments JSONB,  -- Сегменты транскрипции с временными метками
    diarization JSONB,  -- Результаты диаризации (кто говорил)
    manager_segments JSONB,  -- Сегменты менеджера
    client_segments JSONB,  -- Сегменты клиента
    stt_model VARCHAR(100),  -- Модель STT (например, "faster-whisper-large-v3")
    processing_time_seconds FLOAT,  -- Время обработки в секундах
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(call_id)
);

-- Индекс для поиска по тексту транскрипции (full-text search)
CREATE INDEX IF NOT EXISTS idx_transcriptions_text ON call_transcriptions USING gin(to_tsvector('russian', transcription_text));

-- Таблица оценок качества звонков
CREATE TABLE IF NOT EXISTS call_quality_scores (
    id SERIAL PRIMARY KEY,
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    transcription_id INTEGER REFERENCES call_transcriptions(id) ON DELETE SET NULL,
    
    -- Оценки по критериям (0.0 - 1.0)
    greeting_score FLOAT DEFAULT 0.0,  -- Приветствие
    professionalism_score FLOAT DEFAULT 0.0,  -- Профессионализм
    clarity_score FLOAT DEFAULT 0.0,  -- Понятность речи
    listening_score FLOAT DEFAULT 0.0,  -- Умение слушать
    problem_solving_score FLOAT DEFAULT 0.0,  -- Решение проблем
    closing_score FLOAT DEFAULT 0.0,  -- Завершение разговора
    overall_score FLOAT DEFAULT 0.0,  -- Общая оценка
    
    -- Детальная оценка (JSON)
    detailed_evaluation JSONB,
    
    -- Метаданные оценки
    llm_model VARCHAR(100),  -- Модель LLM (например, "saiga-llama3")
    evaluation_prompt TEXT,  -- Промпт для оценки
    processing_time_seconds FLOAT,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(call_id)
);

-- Индекс для сортировки по оценкам
CREATE INDEX IF NOT EXISTS idx_quality_scores_overall ON call_quality_scores(overall_score DESC);
CREATE INDEX IF NOT EXISTS idx_quality_scores_date ON call_quality_scores(created_at DESC);

-- Таблица метаданных звонков
CREATE TABLE IF NOT EXISTS call_metadata (
    id SERIAL PRIMARY KEY,
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    
    -- Извлеченная информация
    client_name VARCHAR(255),  -- Имя клиента (если упоминалось)
    client_phone VARCHAR(50),  -- Телефон клиента (если упоминался)
    car_brand VARCHAR(100),  -- Марка автомобиля
    car_model VARCHAR(100),  -- Модель автомобиля
    service_type VARCHAR(255),  -- Тип услуги
    appointment_date DATE,  -- Дата записи (если была)
    appointment_time TIME,  -- Время записи (если было)
    
    -- Дополнительные метаданные
    metadata JSONB,  -- Произвольные метаданные в формате JSON
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(call_id)
);

-- ============================================
-- 2. ТАБЛИЦЫ ДЛЯ TELEGRAM-БОТА
-- ============================================

-- Таблица лидов из Telegram
CREATE TABLE IF NOT EXISTS telegram_leads (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,  -- ID пользователя в Telegram
    telegram_username VARCHAR(255),  -- Username в Telegram (если есть)
    client_fio VARCHAR(255) NOT NULL,
    client_phone VARCHAR(128) NOT NULL,
    phone_alt VARCHAR(128),  -- Доп. номер (если ввёл один, кнопкой подтвердил другой)
    need_type VARCHAR(50) NOT NULL,  -- chery_tenet, jetour, used_cars, service, body_repair, secretary
    need_text TEXT,  -- Текст потребности клиента
    department VARCHAR(100) NOT NULL,  -- Отдел, куда передан лид
    group_id BIGINT,  -- ID группы Telegram, куда отправлен лид
    working_hours BOOLEAN DEFAULT TRUE,  -- Было ли рабочее время при обращении
    response_message TEXT,  -- Сообщение, отправленное клиенту
    -- Детали по машине (для слесарного/кузовного цеха)
    car_brand VARCHAR(255),
    car_model VARCHAR(255),
    car_year VARCHAR(20),
    car_mileage VARCHAR(50),
    work_wishes TEXT,
    ics_notification_created BOOLEAN DEFAULT FALSE,  -- Создано ли уведомление в 1С
    ics_client_found BOOLEAN,  -- Найден ли клиент в 1С
    ics_client_id VARCHAR(100),  -- ID клиента в 1С (если найден)
    source VARCHAR(50) DEFAULT 'telegram',  -- Источник: telegram, phone, infolada_cdr, max
    voice_contact_outcome VARCHAR(32),  -- phone: см. VOICE_CONTACT_OUTCOME_CODES в postgresql_manager.py
    cdr_uniqueid VARCHAR(128),  -- Asterisk uniqueid при импорте CDR Инфолады (дедуп)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индекс для поиска по телефону и дате
CREATE INDEX IF NOT EXISTS idx_leads_phone ON telegram_leads(client_phone);
CREATE INDEX IF NOT EXISTS idx_leads_date ON telegram_leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_department ON telegram_leads(department);
CREATE INDEX IF NOT EXISTS idx_leads_source ON telegram_leads(source);

-- Таблица фото от клиентов
CREATE TABLE IF NOT EXISTS telegram_photos (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER REFERENCES telegram_leads(id) ON DELETE CASCADE,
    telegram_file_id VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,  -- Путь к сохраненному файлу
    file_size_bytes BIGINT,
    caption TEXT,  -- Подпись к фото
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индекс для связи с лидами
CREATE INDEX IF NOT EXISTS idx_photos_lead ON telegram_photos(lead_id);

-- Таблица напоминаний о записях
CREATE TABLE IF NOT EXISTS appointment_reminders (
    id SERIAL PRIMARY KEY,
    client_fio VARCHAR(255) NOT NULL,
    client_phone VARCHAR(50) NOT NULL,
    appointment_date DATE NOT NULL,
    appointment_time TIME NOT NULL,
    reminder_sent_at TIMESTAMP,  -- Когда отправлено напоминание
    reminder_scheduled_for TIMESTAMP NOT NULL,  -- Когда запланировано напоминание
    telegram_user_id BIGINT,  -- ID пользователя Telegram (если есть)
    sent BOOLEAN DEFAULT FALSE,  -- Отправлено ли напоминание
    ics_appointment_id VARCHAR(100),  -- ID записи в 1С
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индекс для поиска неотправленных напоминаний
CREATE INDEX IF NOT EXISTS idx_reminders_sent ON appointment_reminders(sent, reminder_scheduled_for);
CREATE INDEX IF NOT EXISTS idx_reminders_date ON appointment_reminders(appointment_date);

-- Таблица оценок после визита
CREATE TABLE IF NOT EXISTS post_visit_feedback (
    id SERIAL PRIMARY KEY,
    client_fio VARCHAR(255) NOT NULL,
    client_phone VARCHAR(50) NOT NULL,
    visit_date DATE NOT NULL,
    visit_time TIME,
    department VARCHAR(100),  -- Отдел (сервис, продажи и т.д.)
    telegram_user_id BIGINT,  -- ID пользователя Telegram
    
    -- Оценки (1-5 или 1-10)
    service_quality INTEGER,  -- Качество обслуживания
    staff_attitude INTEGER,  -- Отношение персонала
    cleanliness INTEGER,  -- Чистота
    waiting_time INTEGER,  -- Время ожидания
    overall_rating INTEGER,  -- Общая оценка
    
    -- Текстовые отзывы
    comment TEXT,  -- Комментарий клиента
    suggestions TEXT,  -- Предложения по улучшению
    
    -- Метаданные
    feedback_sent_at TIMESTAMP,  -- Когда отправлен опрос
    feedback_completed_at TIMESTAMP,  -- Когда заполнен опрос
    ics_visit_id VARCHAR(100),  -- ID визита в 1С
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индекс для поиска по дате визита
CREATE INDEX IF NOT EXISTS idx_feedback_visit_date ON post_visit_feedback(visit_date DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_phone ON post_visit_feedback(client_phone);

-- ============================================
-- 3. ТАБЛИЦЫ ДЛЯ СТАТИСТИКИ И ЛОГОВ
-- ============================================

-- Таблица логов обработки звонков
CREATE TABLE IF NOT EXISTS call_processing_logs (
    id SERIAL PRIMARY KEY,
    call_id INTEGER REFERENCES calls(id) ON DELETE CASCADE,
    processing_stage VARCHAR(50) NOT NULL,  -- stt, diarization, llm, completed, error
    status VARCHAR(20) NOT NULL,  -- success, error, in_progress
    error_message TEXT,
    processing_time_seconds FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индекс для поиска ошибок
CREATE INDEX IF NOT EXISTS idx_logs_status ON call_processing_logs(status, created_at DESC);

-- Таблица статистики обработки
CREATE TABLE IF NOT EXISTS processing_statistics (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    total_calls INTEGER DEFAULT 0,  -- Всего звонков за день
    processed_calls INTEGER DEFAULT 0,  -- Обработано звонков
    failed_calls INTEGER DEFAULT 0,  -- Ошибок обработки
    total_duration_seconds INTEGER DEFAULT 0,  -- Общая длительность звонков
    total_processing_time_seconds FLOAT DEFAULT 0,  -- Общее время обработки
    average_quality_score FLOAT,  -- Средняя оценка качества
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 4. ФУНКЦИИ И ТРИГГЕРЫ
-- ============================================

-- Функция для обновления updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Триггеры для автоматического обновления updated_at
-- Таблица пользователей админки (роли 1-6)
CREATE TABLE IF NOT EXISTS admin_users (
    id SERIAL PRIMARY KEY,
    login VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role_id INTEGER NOT NULL CHECK (role_id >= 1 AND role_id <= 8 AND role_id != 7),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_admin_users_login ON admin_users(login);

CREATE TRIGGER update_calls_updated_at BEFORE UPDATE ON calls
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_call_metadata_updated_at BEFORE UPDATE ON call_metadata
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_admin_users_updated_at BEFORE UPDATE ON admin_users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_telegram_leads_updated_at BEFORE UPDATE ON telegram_leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_appointment_reminders_updated_at BEFORE UPDATE ON appointment_reminders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_post_visit_feedback_updated_at BEFORE UPDATE ON post_visit_feedback
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_processing_statistics_updated_at BEFORE UPDATE ON processing_statistics
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 5. ПРАВА ДОСТУПА
-- ============================================

-- Создание пользователя ics_user для 1С Альфа-Авто (только чтение)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ics_user') THEN
        CREATE ROLE ics_user WITH LOGIN PASSWORD 'ics_readonly_changeme';
    END IF;
END
$$;

-- Выдача прав analytics_user (полные права)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO analytics_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO analytics_user;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO analytics_user;

-- Выдача прав ics_user (только чтение)
GRANT USAGE ON SCHEMA public TO ics_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ics_user;

-- Права на будущие объекты
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO analytics_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ics_user;

-- ============================================
-- 6. КОММЕНТАРИИ К ТАБЛИЦАМ
-- ============================================

COMMENT ON TABLE calls IS 'Записи телефонных звонков';
COMMENT ON TABLE call_transcriptions IS 'Транскрипции звонков (STT результаты)';
COMMENT ON TABLE call_quality_scores IS 'Оценки качества звонков (LLM результаты)';
COMMENT ON TABLE call_metadata IS 'Метаданные звонков (извлеченная информация)';
COMMENT ON TABLE telegram_leads IS 'Лиды из Telegram-бота';
COMMENT ON TABLE telegram_photos IS 'Фото от клиентов через Telegram';
COMMENT ON TABLE appointment_reminders IS 'Напоминания о записях на сервис';
COMMENT ON TABLE post_visit_feedback IS 'Оценки после визита клиентов';
COMMENT ON TABLE call_processing_logs IS 'Логи обработки звонков';
COMMENT ON TABLE processing_statistics IS 'Статистика обработки звонков';
