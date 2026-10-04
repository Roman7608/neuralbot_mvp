-- Миграция: добавить поля по машине в telegram_leads (для слесарного/кузовного цеха).
-- Выполнить на уже существующей БД: psql -U analytics_user -d vikingi_analytics -f database/migrations/001_add_telegram_leads_car_columns.sql

ALTER TABLE telegram_leads ADD COLUMN IF NOT EXISTS car_brand VARCHAR(255);
ALTER TABLE telegram_leads ADD COLUMN IF NOT EXISTS car_model VARCHAR(255);
ALTER TABLE telegram_leads ADD COLUMN IF NOT EXISTS car_year VARCHAR(20);
ALTER TABLE telegram_leads ADD COLUMN IF NOT EXISTS car_mileage VARCHAR(50);
ALTER TABLE telegram_leads ADD COLUMN IF NOT EXISTS work_wishes TEXT;
