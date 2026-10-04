-- Входящий номер (DID) для сессий голосового бота.
-- Идемпотентно.

ALTER TABLE voice_bot_sessions
  ADD COLUMN IF NOT EXISTS incoming_did VARCHAR(40);

COMMENT ON COLUMN voice_bot_sessions.incoming_did IS 'Входящий номер/DID, на который клиент позвонил (например 697070)';
