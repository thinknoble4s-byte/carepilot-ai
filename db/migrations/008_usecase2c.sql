-- Use Case 2c (v1.2): the doctor picks Urgent, "Call the patient today", the call log and the cover.
-- The call-today task is the existing urgent_call task type, now with two times of day, a move to the
-- cover (by hand or by the system at the owner's deadline), an Overdue end state and the time to call.

ALTER TABLE coordinator_task
    ADD COLUMN owner_deadline_at timestamp,   -- demo time: the owner must have reached the patient by now (16:00)
    ADD COLUMN cover_deadline_at timestamp,   -- demo time: the cover's deadline (18:00)
    ADD COLUMN moved_at_demo     timestamp,   -- the same task moved to the cover (once only)
    ADD COLUMN moved_from        text,        -- the owner it moved from
    ADD COLUMN moved_by          text,        -- the person who passed it, or the system
    ADD COLUMN moved_reason      text,
    ADD COLUMN overdue_at_demo   timestamp,   -- the cover's deadline passed with no reached call
    ADD COLUMN minutes_to_call   integer;     -- minutes from the Urgent choice to the call that reached the patient

-- An urgent call closes with "reached" (there is no "not reached" for an urgent matter).
ALTER TABLE coordinator_task DROP CONSTRAINT IF EXISTS coordinator_task_result_check;
ALTER TABLE coordinator_task ADD CONSTRAINT coordinator_task_result_check
    CHECK (result IN ('booked', 'could_not_book', 'rebooked', 'reason_recorded', 'sorted', 'needs_follow_up',
                      'not_reached', 'reached'));

-- The doctor's choice is saved once with name and demo time (REQ-01).
ALTER TABLE handover ADD COLUMN gate2_at_demo timestamp;

-- One open call-today task per handover (REQ-05).
CREATE UNIQUE INDEX coordinator_task_one_open_urgent
    ON coordinator_task (handover_id) WHERE status = 'open' AND task_type = 'urgent_call';
