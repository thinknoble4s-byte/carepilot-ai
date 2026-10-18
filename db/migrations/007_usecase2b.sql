-- Use Case 2b (v1.2): button replies and HELP become tasks for a person, with call attempts and results.
-- Tasks: help_booking ("Please help me book"), cant_come ("I can't come"), help_request (typed HELP).

-- A coordinator records the booking with a date and a time (REQ-09/10/12).
ALTER TABLE order_card ADD COLUMN appointment_time time;

-- The result of a task, recorded by a person (REQ-09 to REQ-11, REQ-17, REQ-18).
ALTER TABLE coordinator_task
    ADD COLUMN result           text CHECK (result IN ('booked', 'could_not_book', 'rebooked', 'reason_recorded',
                                                       'sorted', 'needs_follow_up', 'not_reached')),
    ADD COLUMN result_code      text,          -- can't come: cost, pre_authorisation, transport, work_family, other
    ADD COLUMN appointment_date date,
    ADD COLUMN appointment_time time,
    ADD COLUMN done_at_demo     timestamp,     -- demo date and time the task was closed
    ADD COLUMN inbound_id       bigint REFERENCES inbound_message (inbound_id),
    ADD COLUMN created_at_demo  timestamp;

-- One phone call by the coordinator for a task (REQ-17).
CREATE TABLE task_call_attempt (
    attempt_id     bigserial PRIMARY KEY,
    task_id        bigint NOT NULL REFERENCES coordinator_task (task_id),
    called_at_demo timestamp NOT NULL,
    called_by      text NOT NULL,
    result         text NOT NULL CHECK (result IN ('reached', 'no_answer')),
    note           text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX task_call_attempt_task ON task_call_attempt (task_id);

-- At most one open task of a kind per patient and card (REQ-13); patient-level tasks have no card.
CREATE UNIQUE INDEX coordinator_task_one_open_help
    ON coordinator_task (mrn, task_type, coalesce(card_id, 0))
    WHERE status = 'open' AND task_type IN ('help_booking', 'cant_come', 'help_request');
