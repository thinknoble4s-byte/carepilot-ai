-- Increments 2 and 3: the deterministic loop (gap rules, agent run, send-guard,
-- outbox, WhatsApp simulator, button replies) and the human safety path
-- (handover, Gates 1-3, deadlines with escalation to cover, closure, resume).

ALTER TABLE config_versions DROP CONSTRAINT config_versions_kind_check;
ALTER TABLE config_versions ADD CONSTRAINT config_versions_kind_check
    CHECK (kind IN ('plan_template', 'rule_thresholds', 'message_template', 'autonomy_policy', 'deadline_policy'));

-- What the agent or a person will do next for this patient (shown on the Patient page).
ALTER TABLE patient ADD COLUMN next_planned_action text;

-- Instruction cards come from a doctor's instruction, not the plan.
ALTER TABLE order_card ALTER COLUMN care_plan_id DROP NOT NULL;

-- ---------------------------------------------------------------------------
-- Gap events (07:00 rules). One per rule and card: running again adds nothing.
-- ---------------------------------------------------------------------------
CREATE TABLE gap_event (
    gap_id           bigserial PRIMARY KEY,
    rule             text NOT NULL CHECK (rule IN ('G0', 'G1')),
    card_id          bigint NOT NULL REFERENCES order_card (card_id),
    mrn              text NOT NULL REFERENCES patient (mrn),
    due_date         date NOT NULL,
    detected_on      date NOT NULL,
    status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acted', 'resolved')),
    last_skip_reason text,                    -- why the agent left it open (paused, handover open, ...)
    outcome          text,
    acted_on         date,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (rule, card_id)
);

-- ---------------------------------------------------------------------------
-- Handover: a person takes over; automated sends stop until it is closed.
-- ---------------------------------------------------------------------------
CREATE TABLE handover (
    handover_id      bigserial PRIMARY KEY,
    mrn              text NOT NULL REFERENCES patient (mrn),
    trigger          text NOT NULL CHECK (trigger IN ('unclassified_reply', 'help_keyword', 'manual', 'health_matter')),
    patient_words    text,
    translation      text,
    source           text,                    -- e.g. "WhatsApp reply 2026-11-09, inbound #3"
    stage            text NOT NULL DEFAULT 'awaiting_coordinator'
        CHECK (stage IN ('awaiting_coordinator', 'calling', 'awaiting_doctor', 'awaiting_instruction',
                         'ready_to_close', 'closed')),
    opened_by        text NOT NULL,
    opened_on        date NOT NULL,
    gate1_choice     text CHECK (gate1_choice IN ('send_to_doctor', 'call_first', 'nothing_clinical')),
    gate1_by         text,
    call_note        text,
    doctor           text REFERENCES app_user (user_id),
    gate2_decision   text CHECK (gate2_decision IN ('urgent', 'routine')),
    gate2_by         text,
    instruction      text,
    instruction_by   text,
    instruction_card bigint REFERENCES order_card (card_id),
    closed_by        text,
    closed_reason    text,
    closed_on        date,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (stage <> 'closed' OR (closed_by IS NOT NULL AND length(trim(closed_reason)) > 0))
);

CREATE UNIQUE INDEX handover_one_open ON handover (mrn) WHERE stage <> 'closed';

ALTER TABLE order_card ADD COLUMN handover_id bigint REFERENCES handover (handover_id);

-- ---------------------------------------------------------------------------
-- Tasks for people, with deadlines and escalation (extends the Increment 1 table).
-- ---------------------------------------------------------------------------
ALTER TABLE coordinator_task
    ADD COLUMN owner_name    text,
    ADD COLUMN cover_name    text,
    ADD COLUMN assigned_name text,
    ADD COLUMN due_date      date,
    ADD COLUMN escalation    text CHECK (escalation IN ('cover', 'reminder')),
    ADD COLUMN escalated_on  date,
    ADD COLUMN handover_id   bigint REFERENCES handover (handover_id),
    ADD COLUMN card_id       bigint REFERENCES order_card (card_id),
    ADD COLUMN gap_id        bigint REFERENCES gap_event (gap_id),
    ADD COLUMN dedupe_key    text UNIQUE,
    ADD COLUMN done_by       text,
    ADD COLUMN done_note     text,
    ADD COLUMN done_on       date;

-- ---------------------------------------------------------------------------
-- Outbox: every message, sent or blocked. The WhatsApp simulator reads it.
-- ---------------------------------------------------------------------------
CREATE TABLE outbox_message (
    message_id       bigserial PRIMARY KEY,
    mrn              text NOT NULL REFERENCES patient (mrn),
    recipient        text NOT NULL CHECK (recipient IN ('patient', 'caregiver')),
    to_phone         text,
    language         text NOT NULL,
    template_id      text NOT NULL,
    template_version text NOT NULL,
    body             text NOT NULL,           -- in the recipient's language
    body_en          text NOT NULL,           -- English, for staff
    buttons          jsonb NOT NULL DEFAULT '[]'::jsonb,
    card_id          bigint REFERENCES order_card (card_id),
    gap_id           bigint REFERENCES gap_event (gap_id),
    handover_id      bigint REFERENCES handover (handover_id),
    actor_type       text NOT NULL,
    actor            text NOT NULL,
    reason_code      text NOT NULL,
    status           text NOT NULL CHECK (status IN ('sent', 'blocked', 'pending_approval', 'rejected')),
    blocked_reason   text,
    approved_by      text,
    created_on       date NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX outbox_by_patient ON outbox_message (mrn, message_id);

CREATE TABLE inbound_message (
    inbound_id   bigserial PRIMARY KEY,
    mrn          text NOT NULL REFERENCES patient (mrn),
    from_role    text NOT NULL CHECK (from_role IN ('patient', 'caregiver')),
    in_reply_to  bigint REFERENCES outbox_message (message_id),
    button       text CHECK (button IN ('booked', 'help_book', 'cant_come')),
    text         text,
    handling     text NOT NULL,             -- what the gateway did with it
    handover_id  bigint REFERENCES handover (handover_id),
    received_on  date NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (button IS NOT NULL OR length(trim(coalesce(text, ''))) > 0)
);

CREATE INDEX inbound_by_patient ON inbound_message (mrn, inbound_id);
