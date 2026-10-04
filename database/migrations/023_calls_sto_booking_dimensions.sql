-- STO: доп. аналитические признаки записи на сервис
ALTER TABLE calls
    ADD COLUMN IF NOT EXISTS sto_service_brand VARCHAR(32),
    ADD COLUMN IF NOT EXISTS sto_work_type VARCHAR(32),
    ADD COLUMN IF NOT EXISTS sto_is_booking BOOLEAN,
    ADD COLUMN IF NOT EXISTS sto_dimension_confidence VARCHAR(16),
    ADD COLUMN IF NOT EXISTS sto_dimension_evidence JSONB;

CREATE INDEX IF NOT EXISTS idx_calls_sto_brand
    ON calls (call_date, sto_service_brand)
    WHERE call_type IN ('STO_IN', 'STO_OUT');

CREATE INDEX IF NOT EXISTS idx_calls_sto_work_type
    ON calls (call_date, sto_work_type)
    WHERE call_type IN ('STO_IN', 'STO_OUT');
