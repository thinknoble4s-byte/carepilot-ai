-- Patient self-booking (5 Nov): "Booked elsewhere" replaces "Done at an outside lab" on the reminders, and
-- "Please help me book" offers four appointment slots the patient taps (slot_1 .. slot_4).
ALTER TABLE inbound_message DROP CONSTRAINT IF EXISTS inbound_message_button_check;
ALTER TABLE inbound_message ADD CONSTRAINT inbound_message_button_check
    CHECK (button IN ('booked', 'help_book', 'cant_come', 'booked_elsewhere', 'slot_1', 'slot_2', 'slot_3', 'slot_4',
                      'done_outside', 'done_hospital', 'not_done'));

-- A booking made from WhatsApp is written to the appointment list too, with its time, the order card it books and
-- how it was booked; it has no import batch. Imported rows keep their one-per-patient-day-department rule.
ALTER TABLE appointment
    ADD COLUMN appointment_time time,
    ADD COLUMN card_id  bigint REFERENCES order_card (card_id),
    ADD COLUMN item     text,
    ADD COLUMN source   text NOT NULL DEFAULT 'appointment_list'
        CHECK (source IN ('appointment_list', 'patient_button', 'patient_elsewhere', 'patient_slot')),
    ALTER COLUMN batch_id DROP NOT NULL;
ALTER TABLE appointment DROP CONSTRAINT IF EXISTS appointment_mrn_appointment_date_department_key;
CREATE UNIQUE INDEX appointment_imported_key ON appointment (mrn, appointment_date, department) WHERE card_id IS NULL;
CREATE UNIQUE INDEX appointment_card_key ON appointment (card_id) WHERE card_id IS NOT NULL;
ALTER TABLE appointment ADD CONSTRAINT appointment_has_origin CHECK (batch_id IS NOT NULL OR card_id IS NOT NULL);
