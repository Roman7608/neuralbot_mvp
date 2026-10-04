-- Прочие ОП: call_type = OP_MISC (отдел OP). Классификация: classify_by_transcript._coerce_other_other_to_op_misc.
-- Колонка без изменения длины (VARCHAR); новое значение добавляется приложением.

COMMENT ON COLUMN calls.call_type IS 'OP_IN, OP_OUT, OP_MISC (Прочие ОП), STO_IN, STO_OUT, OTHER — по транскрипту и правилам v2';
