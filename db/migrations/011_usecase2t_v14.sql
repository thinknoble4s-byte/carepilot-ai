-- Use Case 2T v1.4 (v1.3 changes, Shiv's review of 24 October): the test card has five states,
-- Due -> Booked -> Completed -> Report received -> Report verified (Ordered and Scheduled are removed; a date is
-- optional on Booked). Non-specimen tests (imaging) keep Due -> Booked -> Completed -> Verified (doctor).
-- The report check asks the patient once the test date has passed; an outside report is scored by plain rules
-- (seven hard checks and a score out of 100): 95 or more goes to the doctor as "auto-matched" with a copy for the
-- coordinator, below that a coordinator verifies it, a wrong or unclear file brings the "possible wrong report"
-- message. The threshold, the weights and Shadow / Live are a setting held by the clinical lead.

UPDATE order_card SET state = 'Booked' WHERE card_type = 'test' AND state IN ('Scheduled', 'Ordered');

-- The card's label: "Patient says booked (no date)", "Booked (confirmed by coordinator)", the report-check
-- answer, "Outside report (auto-matched 97, not yet confirmed by a person)", "Outside report (verified by ...)".
ALTER TABLE order_card ADD COLUMN label text;

-- Hard check G3 compares age and sex with the record (synthetic values in the demo data sets).
ALTER TABLE patient ADD COLUMN age int, ADD COLUMN sex text CHECK (sex IN ('F', 'M', 'X'));

-- The three report-check buttons.
ALTER TABLE inbound_message DROP CONSTRAINT IF EXISTS inbound_message_button_check;
ALTER TABLE inbound_message ADD CONSTRAINT inbound_message_button_check
    CHECK (button IN ('booked', 'help_book', 'cant_come', 'done_outside', 'done_hospital', 'not_done'));

-- The routing setting (threshold, weights, Shadow / Live), versioned and audited like the other settings.
ALTER TABLE config_versions DROP CONSTRAINT config_versions_kind_check;
ALTER TABLE config_versions ADD CONSTRAINT config_versions_kind_check
    CHECK (kind IN ('plan_template', 'rule_thresholds', 'message_template', 'autonomy_policy', 'deadline_policy',
                    'scenario_settings', 'report_routing'));

-- held: in the hospital data, not yet sent to CarePilot (found by the report check).
-- auto_matched: an outside report that scored 95 or more with every hard check passed (not verified).
-- flagged: an auto-matched report the coordinator flagged as wrong (file kept, Unverified).
ALTER TABLE test_report DROP CONSTRAINT IF EXISTS test_report_status_check;
ALTER TABLE test_report ADD CONSTRAINT test_report_status_check
    CHECK (status IN ('verified', 'mismatch', 'exception', 'unverified', 'rejected', 'held', 'auto_matched',
                      'flagged'));
ALTER TABLE test_report
    ADD COLUMN read_confidence    jsonb,     -- how surely each identifier was read (0 to 1)
    ADD COLUMN score              int,       -- rule score out of 100 (about the document, never the patient)
    ADD COLUMN score_parts        jsonb,
    ADD COLUMN hard_checks        jsonb,     -- G1 to G7: true / false
    ADD COLUMN match_facts        jsonb,     -- plain lines: "name: match"
    ADD COLUMN routing            text CHECK (routing IN ('auto_matched', 'verify', 'wrong_report')),
    ADD COLUMN routing_mode       text,      -- shadow / live, when it was worked out
    ADD COLUMN routing_applied    text,
    ADD COLUMN sender_phone       text,
    ADD COLUMN file_hash          text,
    ADD COLUMN group_id           bigint REFERENCES test_report (report_id),   -- photos sent together
    ADD COLUMN wrong_report       boolean NOT NULL DEFAULT false,              -- "possibly wrong or unclear report"
    ADD COLUMN prior_state        text,      -- the card's state before an outside report moved it
    ADD COLUMN report_date        date,
    ADD COLUMN patient_note       text,      -- the patient's own words sent with the file, verbatim
    ADD COLUMN note_translation   text,      -- AI translation (Kannada / Hindi)
    ADD COLUMN check_id           bigint,
    ADD COLUMN confirmed_by       text,
    ADD COLUMN confirmed_at_demo  timestamp,
    ADD COLUMN flagged_by         text,
    ADD COLUMN flag_reason        text,
    ADD COLUMN flagged_at_demo    timestamp;
CREATE INDEX test_report_hash ON test_report (file_hash);

ALTER TABLE report_review DROP CONSTRAINT IF EXISTS report_review_status_check;
ALTER TABLE report_review ADD CONSTRAINT report_review_status_check
    CHECK (status IN ('open', 'awaiting_instruction', 'decided', 'withdrawn'));
ALTER TABLE report_review
    ADD COLUMN withdrawn_reason       text,
    ADD COLUMN flagged_after_decision boolean NOT NULL DEFAULT false,
    ADD COLUMN notice_seen_by         text;

-- The report check: one per card, due date and round (Not done yet repeats it, at most 2 more times).
CREATE TABLE report_check (
    check_id          bigserial PRIMARY KEY,
    card_id           bigint NOT NULL REFERENCES order_card (card_id),
    mrn               text NOT NULL REFERENCES patient (mrn),
    due_date          date NOT NULL,
    round             int NOT NULL DEFAULT 1,
    raised_on         date NOT NULL,
    status            text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'blocked', 'sent', 'skipped', 'closed')),
    blocked_reason    text,
    message_id        bigint REFERENCES outbox_message (message_id),
    sent_on           date,
    answer            text CHECK (answer IN ('done_outside', 'done_hospital', 'not_done')),
    answered_on       date,
    answer_inbound_id bigint REFERENCES inbound_message (inbound_id),
    timer_from        date,                  -- upload timer: from the tap (or a wrong file); NULL = stopped
    nudge_due         boolean NOT NULL DEFAULT false,
    nudge_message_id  bigint REFERENCES outbox_message (message_id),
    nudged_on         date,
    report_id         bigint REFERENCES test_report (report_id),
    closed_reason     text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (card_id, due_date, round)
);
ALTER TABLE test_report ADD CONSTRAINT test_report_check_fk FOREIGN KEY (check_id) REFERENCES report_check (check_id);

-- "Copies for your information": no due date, no Overdue mark, so not a task.
CREATE TABLE info_copy (
    copy_id        bigserial PRIMARY KEY,
    mrn            text NOT NULL REFERENCES patient (mrn),
    report_id      bigint NOT NULL REFERENCES test_report (report_id),
    card_id        bigint REFERENCES order_card (card_id),
    kind           text NOT NULL CHECK (kind IN ('auto_matched', 'wrong_report')),
    owner_name     text NOT NULL,
    status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'confirmed', 'flagged', 'read')),
    acted_by       text,
    acted_at_demo  timestamp,
    reason         text,
    created_at_demo timestamp NOT NULL,
    UNIQUE (report_id, kind)
);
