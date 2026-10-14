-- Screen rebuild (prototype v7.2): named coordinator per patient, pause with an end
-- date, logged calls, and the dates each gate was decided (for the timeline,
-- concern chain and response-time indicator).

ALTER TABLE patient
    ADD COLUMN coordinator  text,
    ADD COLUMN pause_until  date,
    ADD COLUMN pause_reason text;

ALTER TABLE care_plan ADD COLUMN confirmed_on date;

ALTER TABLE handover
    ADD COLUMN gate1_on       date,
    ADD COLUMN gate2_on       date,
    ADD COLUMN instruction_on date,
    ADD COLUMN called_on      date;

CREATE TABLE call_log (
    call_id     bigserial PRIMARY KEY,
    mrn         text NOT NULL REFERENCES patient (mrn),
    who         text NOT NULL CHECK (who IN ('patient', 'caregiver')),
    outcome     text NOT NULL CHECK (outcome IN ('noans', 'book', 'concern', 'away', 'ok', 'wrong')),
    note        text,
    called_by   text NOT NULL REFERENCES app_user (user_id),
    called_on   date NOT NULL,
    handover_id bigint REFERENCES handover (handover_id),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX call_log_by_patient ON call_log (mrn, call_id);
