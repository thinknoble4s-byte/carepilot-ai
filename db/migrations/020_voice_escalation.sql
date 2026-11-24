-- UC1-V voice escalation for unbooked follow-ups (spec v0.1, section 6.4): the line library, the slot list, the
-- event, the call record with its turns, and what the safety handover needs (the call and the 2-hour target).

-- L1 to L9 (voice) and W1 to W2 (WhatsApp): the only words the agent has (rule R1).
CREATE TABLE voice_line (
    line_id     text NOT NULL,
    language    text NOT NULL,
    channel     text NOT NULL CHECK (channel IN ('voice', 'whatsapp')),
    spoken_when text NOT NULL,
    text        text NOT NULL,
    text_en     text NOT NULL,
    status      text NOT NULL CHECK (status IN ('DRAFT', 'DRAFT-CLINICAL', 'APPROVED')),
    status_note text,
    approved_by text,
    approved_on date,
    version     text NOT NULL,
    PRIMARY KEY (line_id, language),
    CHECK (status <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_on IS NOT NULL))
);

-- One event per card + due date + V1.
CREATE TABLE voice_escalation (
    escalation_id bigserial PRIMARY KEY,
    rule          text NOT NULL DEFAULT 'V1',
    card_id       bigint NOT NULL REFERENCES order_card (card_id),
    mrn           text NOT NULL REFERENCES patient (mrn),
    due_date      date NOT NULL,
    reminder_at   timestamp NOT NULL,
    raised_at     timestamp NOT NULL,
    status        text NOT NULL DEFAULT 'due' CHECK (status IN ('due', 'held', 'in_call', 'retry', 'done')),
    held_reason   text,
    gate          jsonb,
    retry_at      timestamp,
    UNIQUE (card_id, due_date, rule)
);

CREATE TABLE voice_call (
    call_id       bigserial PRIMARY KEY,
    escalation_id bigint NOT NULL REFERENCES voice_escalation (escalation_id),
    mrn           text NOT NULL REFERENCES patient (mrn),
    card_id       bigint NOT NULL REFERENCES order_card (card_id),
    language      text NOT NULL,
    simulated     boolean NOT NULL DEFAULT true CHECK (simulated),
    script        text,                                  -- which recorded patient clips answer (demo: s2 or s3)
    status        text NOT NULL DEFAULT 'ringing' CHECK (status IN ('ringing', 'live', 'ended')),
    started_at    timestamp NOT NULL,
    ended_at      timestamp,
    ended_tx_at   timestamptz,                           -- real time, to order it with the WhatsApp thread
    stage         text NOT NULL DEFAULT 'identity' CHECK (stage IN ('identity', 'booking', 'slot')),
    identified    boolean NOT NULL DEFAULT false,
    identity_repeats int NOT NULL DEFAULT 0,
    explanations  int NOT NULL DEFAULT 0,
    reasks        int NOT NULL DEFAULT 0,
    slot_id       bigint,
    outcome       text CHECK (outcome IN ('booked', 'declined', 'safety_stop', 'no_answer', 'wrong_person',
                                          'slot_unsuitable', 'unclear')),
    reason_code   text,
    gate          jsonb NOT NULL,
    models        jsonb NOT NULL DEFAULT '{}',
    undone_by     text,
    undone_at     timestamp,
    undo_reason   text,
    CHECK (status <> 'ended' OR (outcome IS NOT NULL AND ended_at IS NOT NULL))
);
CREATE UNIQUE INDEX voice_call_one_open ON voice_call (mrn) WHERE status <> 'ended';

CREATE TABLE voice_turn (
    call_id         bigint NOT NULL REFERENCES voice_call (call_id),
    seq             int NOT NULL,
    who             text NOT NULL CHECK (who IN ('agent', 'patient')),
    line_id         text,
    approval_status text,
    text            text,
    translation     text,
    label           text CHECK (label IN ('YES', 'NO', 'HEALTH_CONCERN', 'UNCLEAR')),
    reason_code     text,
    ai_failed       boolean NOT NULL DEFAULT false,
    model           text,
    clip_id         text,
    audio_id        bigint REFERENCES audio_blob (audio_id),
    at_demo         timestamp NOT NULL,
    PRIMARY KEY (call_id, seq),
    CHECK (who <> 'agent' OR (line_id IS NOT NULL AND approval_status IS NOT NULL))
);

-- The simulated DEXA slot list.
CREATE TABLE voice_slot (
    slot_id       bigserial PRIMARY KEY,
    slot_date     date NOT NULL,
    slot_time     time NOT NULL,
    status        text NOT NULL DEFAULT 'free' CHECK (status IN ('free', 'taken')),
    taken_by_card bigint REFERENCES order_card (card_id),
    UNIQUE (slot_date, slot_time)
);

ALTER TABLE handover
    ADD COLUMN voice_call_id   bigint REFERENCES voice_call (call_id),
    ADD COLUMN voice_turn      int,
    ADD COLUMN callback_due_at timestamp;

ALTER TABLE appointment DROP CONSTRAINT IF EXISTS appointment_source_check;
ALTER TABLE appointment ADD CONSTRAINT appointment_source_check
    CHECK (source IN ('appointment_list', 'patient_button', 'patient_elsewhere', 'patient_slot', 'voice_agent'));

-- V9: the agent never books without a date and a time.
ALTER TABLE order_card ADD CONSTRAINT order_card_voice_booking_has_date
    CHECK (state <> 'Booked' OR booking_source IS DISTINCT FROM 'voice_agent' OR appointment_date IS NOT NULL);
