-- Узкая когорта для оценки по скрипту записи на ТО (закладка «Звонки»):
-- STO_TO_IN / STO_TO_OUT. Широкая классификация остаётся в call_type (STO_IN / STO_OUT).

ALTER TABLE calls ADD COLUMN IF NOT EXISTS sto_to_rubric_type VARCHAR(20);
ALTER TABLE calls ADD COLUMN IF NOT EXISTS sto_to_rubric_reason VARCHAR(64);
ALTER TABLE calls ADD COLUMN IF NOT EXISTS sto_appointment_agreed BOOLEAN;

COMMENT ON COLUMN calls.sto_to_rubric_type IS 'STO_TO_IN | STO_TO_OUT — оценка по чек-листу ТО; NULL если не когорта рубрики';
COMMENT ON COLUMN calls.sto_to_rubric_reason IS 'Код причины, если sto_to_rubric_type IS NULL (отладка)';
COMMENT ON COLUMN calls.sto_appointment_agreed IS 'Эвристика: согласованы дата/время визита';

CREATE INDEX IF NOT EXISTS idx_calls_sto_to_rubric
    ON calls (call_date, sto_to_rubric_type)
    WHERE sto_to_rubric_type IS NOT NULL;
