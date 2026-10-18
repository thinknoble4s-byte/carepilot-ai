-- Use Case 2T (v1.2): the test cycle — from "test due" to the doctor's decision on the report.
-- Test cards go Due -> Scheduled -> Completed -> Report received -> Report verified (Ordered is skipped in this
-- sprint). A hospital report is linked by MRN and test code; an outside report is held Unverified until a
-- coordinator verifies it. A verified report becomes "Report ready for review" for the doctor.

-- The test code (with the MRN it links a hospital report to the card), the "Patient says booked (no date)"
-- label, the Completed date, and where the report came from (hospital or outside).
ALTER TABLE order_card
    ADD COLUMN test_code           text,
    ADD COLUMN patient_says_booked boolean NOT NULL DEFAULT false,
    ADD COLUMN completed_on        date,
    ADD COLUMN report_road         text CHECK (report_road IN ('hospital', 'outside'));

-- The simulated lab and the test rows of the appointment list are import batches too.
ALTER TABLE import_batch DROP CONSTRAINT IF EXISTS import_batch_source_type_check;
ALTER TABLE import_batch ADD CONSTRAINT import_batch_source_type_check
    CHECK (source_type IN ('register', 'appointments', 'test_appointments', 'lab_reports'));

-- A patient can send a document (photo or PDF of an outside report) from the simulator.
ALTER TABLE inbound_message DROP CONSTRAINT IF EXISTS inbound_message_kind_check;
ALTER TABLE inbound_message ADD CONSTRAINT inbound_message_kind_check
    CHECK (kind IN ('button', 'text', 'voice', 'document'));

-- One report file and what happened to it. The file itself is kept in audio_blob (any stored file).
CREATE TABLE test_report (
    report_id        bigserial PRIMARY KEY,
    mrn              text NOT NULL REFERENCES patient (mrn),   -- the patient it arrived for (lab order / sender)
    road             text NOT NULL CHECK (road IN ('hospital', 'outside')),
    report_ref       text UNIQUE,                              -- hospital: the lab's report id (sent twice = once)
    report_mrn       text,                                     -- the MRN printed on the report
    test_code        text,                                     -- the test code on the report
    blob_id          bigint REFERENCES audio_blob (audio_id),
    file_name        text,
    mime             text,
    status           text NOT NULL CHECK (status IN ('verified', 'mismatch', 'exception', 'unverified',
                                                     'rejected')),
    card_id          bigint REFERENCES order_card (card_id),
    received_at_demo timestamp NOT NULL,
    inbound_id       bigint REFERENCES inbound_message (inbound_id),
    channel          text,                                     -- lab feed (simulated), WhatsApp, coordinator upload
    identifiers      jsonb,                                    -- outside road: identifiers read (never results)
    ai_model         text,
    ai_failed        boolean NOT NULL DEFAULT false,
    proposed_card_id bigint REFERENCES order_card (card_id),
    matched          text[],                                   -- identifiers the coordinator checked
    verified_by      text,
    verified_at_demo timestamp,
    rejected_by      text,
    reject_reason    text,
    mismatch         jsonb,                                    -- report value vs card value, side by side
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX test_report_mrn ON test_report (mrn);

-- "Report ready for review" in the doctor's Needs my decision list: two buttons, nothing pre-selected.
CREATE TABLE report_review (
    review_id          bigserial PRIMARY KEY,
    card_id            bigint NOT NULL REFERENCES order_card (card_id),
    report_id          bigint NOT NULL REFERENCES test_report (report_id),
    mrn                text NOT NULL REFERENCES patient (mrn),
    road               text NOT NULL,
    doctor             text NOT NULL,                          -- the doctor it is assigned to (user name)
    created_at_demo    timestamp NOT NULL,
    due_at_demo        timestamp NOT NULL,                     -- 1 working day, then the doctor's cover
    moved_at_demo      timestamp,
    moved_from         text,
    opened_by          text,
    opened_at_demo     timestamp,
    status             text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'awaiting_instruction', 'decided')),
    decision           text CHECK (decision IN ('continue', 'discussion')),
    decided_by         text,
    decided_at_demo    timestamp,
    instruction        text,
    instruction_days   int,
    instruction_card   bigint REFERENCES order_card (card_id),
    message_id         bigint REFERENCES outbox_message (message_id),
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX report_review_one_per_report ON report_review (report_id);
