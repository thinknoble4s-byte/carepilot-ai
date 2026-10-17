-- Use Case 2a (reply and voice note, v1.2): demo time of day, voice notes with audio,
-- AI understanding (Sarvam) results, proposals with a hold, handover response time,
-- inbound idempotency, and a runtime demo switch for turning Sarvam off.

-- Demo clock with a time of day (handover response time is measured in minutes).
ALTER TABLE demo_clock ADD COLUMN now_time time NOT NULL DEFAULT '09:00';

-- Runtime demo switches (e.g. the "Sarvam off" test switch); cleared by demo reset.
CREATE TABLE demo_setting (
    key   text PRIMARY KEY,
    value jsonb NOT NULL
);

-- Audio of voice notes, stored privately in the database (8-10 s clips).
CREATE TABLE audio_blob (
    audio_id   bigserial PRIMARY KEY,
    mime       text NOT NULL,
    data       bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Inbound messages: voice notes, what the AI made of them, and an event id so a replay is recognised.
ALTER TABLE inbound_message DROP CONSTRAINT inbound_message_check;
ALTER TABLE inbound_message
    ADD COLUMN kind              text NOT NULL DEFAULT 'text' CHECK (kind IN ('button', 'text', 'voice')),
    ADD COLUMN event_id          text UNIQUE,
    ADD COLUMN audio_id          bigint REFERENCES audio_blob (audio_id),
    ADD COLUMN clip_id           text,
    ADD COLUMN transcript        text,          -- in the patient's language (AI)
    ADD COLUMN translation       text,          -- English (AI)
    ADD COLUMN ai_flag           text CHECK (ai_flag IN ('yes', 'no')),
    ADD COLUMN ai_failed         boolean NOT NULL DEFAULT false,
    ADD COLUMN ai_attempts       int,
    ADD COLUMN ai_model          text,
    ADD COLUMN ai_prompt_version text,
    ADD COLUMN received_time     time;
ALTER TABLE inbound_message ADD CONSTRAINT inbound_message_has_content
    CHECK (button IS NOT NULL OR length(trim(coalesce(text, ''))) > 0 OR audio_id IS NOT NULL);

-- Handover: link to the message that opened it, AI failure, and the response time.
ALTER TABLE handover
    ADD COLUMN inbound_id       bigint REFERENCES inbound_message (inbound_id),
    ADD COLUMN ai_failed        boolean NOT NULL DEFAULT false,
    ADD COLUMN opened_at_demo   timestamp,
    ADD COLUMN reviewed_at_demo timestamp,
    ADD COLUMN reviewed_by      text,
    ADD COLUMN response_minutes int;

-- Proposals (flag = no): an AI draft a coordinator approves or rejects. Changes no card, sends nothing.
CREATE TABLE proposal (
    proposal_id       bigserial PRIMARY KEY,
    mrn               text NOT NULL REFERENCES patient (mrn),
    inbound_id        bigint NOT NULL UNIQUE REFERENCES inbound_message (inbound_id),
    summary           text,                 -- AI draft, 1-2 lines, English
    intent            text CHECK (intent IN ('booked', 'help_to_book', 'cant_come', 'pause_request', 'other')),
    intent_reason     text,
    draft_failed      boolean NOT NULL DEFAULT false,
    status            text NOT NULL DEFAULT 'drafted' CHECK (status IN ('drafted', 'approved', 'rejected')),
    final_summary     text,
    edited            boolean,
    decided_by        text REFERENCES app_user (user_id),
    decision_reason   text,
    created_at_demo   timestamp NOT NULL,
    decided_at_demo   timestamp,
    minutes_taken     int,
    ai_model          text,
    ai_prompt_version text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'rejected' OR length(trim(coalesce(decision_reason, ''))) > 0)
);

CREATE INDEX proposal_open ON proposal (mrn) WHERE status = 'drafted';

ALTER TABLE coordinator_task ADD COLUMN proposal_id bigint REFERENCES proposal (proposal_id);
