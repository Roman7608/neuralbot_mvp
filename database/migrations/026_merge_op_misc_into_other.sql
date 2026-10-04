-- Слияние устаревшего call_type OP_MISC («Прочие ОП») с Прочими.
-- psql ... -f database/migrations/026_merge_op_misc_into_other.sql

UPDATE calls
SET department = 'OTHER', call_type = 'OTHER'
WHERE call_type = 'OP_MISC';

COMMENT ON COLUMN calls.call_type IS 'OP_IN, OP_OUT, STO_IN, STO_OUT, OTHER — по транскрипту (OP_MISC удалён, см. миграцию 026)';
