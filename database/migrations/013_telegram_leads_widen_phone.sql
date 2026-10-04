-- Увеличить длину телефона: в БД не должны попадать сырые vCard; на случай краевых форматов — запас.
ALTER TABLE telegram_leads
    ALTER COLUMN client_phone TYPE VARCHAR(128),
    ALTER COLUMN phone_alt TYPE VARCHAR(128);
