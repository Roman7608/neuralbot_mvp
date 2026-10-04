-- Результат сигнала AMI после решения о переводе (обновляется после проигрывания WAV).
ALTER TABLE voice_bot_transfer_events
  ADD COLUMN IF NOT EXISTS ami_result VARCHAR(32);

COMMENT ON COLUMN voice_bot_transfer_events.ami_result IS
  'transfer_started | no_operator | no_ami_redirect; NULL — старые записи или не обновлено';
