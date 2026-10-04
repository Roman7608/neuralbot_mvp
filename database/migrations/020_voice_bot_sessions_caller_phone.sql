-- Номер звонящего (ANI) для сессий голосового бота — для отображения в админке и отработки.
-- Идемпотентно.

ALTER TABLE voice_bot_sessions
  ADD COLUMN IF NOT EXISTS caller_phone VARCHAR(40);

COMMENT ON COLUMN voice_bot_sessions.caller_phone IS 'Номер клиента (нормализованный CLI), из AstDB/AMI';
