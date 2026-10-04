-- Номер клиента (ANI) из сессии голосового бота и попытка связать запись SPRecord по времени.
-- Идемпотентно.

ALTER TABLE calls
  ADD COLUMN IF NOT EXISTS caller_phone VARCHAR(40);

ALTER TABLE calls
  ADD COLUMN IF NOT EXISTS voice_bot_session_id BIGINT;

COMMENT ON COLUMN calls.caller_phone IS 'Номер звонящего (CLI), подставлен из voice_bot_sessions при матчинге';
COMMENT ON COLUMN calls.voice_bot_session_id IS 'Сессия голосового бота, по которой подобран номер';

CREATE INDEX IF NOT EXISTS idx_calls_caller_phone ON calls (caller_phone) WHERE caller_phone IS NOT NULL AND caller_phone <> '';
CREATE INDEX IF NOT EXISTS idx_calls_voice_bot_session_id ON calls (voice_bot_session_id) WHERE voice_bot_session_id IS NOT NULL;
