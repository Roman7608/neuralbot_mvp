-- Миграция: дополнительный номер телефона (если клиент ввёл один, а кнопкой подтвердил другой).
-- Выполнить: sudo -u postgres psql -d vikingi_analytics -f database/migrations/002_add_telegram_leads_phone_alt.sql

ALTER TABLE telegram_leads ADD COLUMN IF NOT EXISTS phone_alt VARCHAR(50);
