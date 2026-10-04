-- Миграция 010: убрать устаревший call_type STO_OUT (→ STO_IN).
-- Выполнить на существующей БД: psql ... -f database/migrations/010_drop_sto_out_call_type.sql
--
-- Тип STO_OUT не используется в продукте: все такие звонки — СТО входящие (STO_IN).
UPDATE calls
SET call_type = 'STO_IN'
WHERE call_type = 'STO_OUT' AND department = 'STO';

COMMENT ON COLUMN calls.call_type IS 'OP_IN, OP_OUT, STO_IN, OTHER — определяется по транскрипту (STO_OUT устарел, см. миграцию 010)';
