-- Имя клиента из state_machine при завершении сессии (для списка / экспорта, если лид без ФИО или не сопоставился).
ALTER TABLE voice_bot_sessions
  ADD COLUMN IF NOT EXISTS client_spoken_name VARCHAR(255);

COMMENT ON COLUMN voice_bot_sessions.client_spoken_name IS
  'Имя/ФИО из бота на момент завершения звонка (COALESCE с telegram_leads.client_fio в list_sessions)';
