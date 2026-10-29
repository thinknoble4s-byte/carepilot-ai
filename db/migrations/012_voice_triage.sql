-- Voice Triage MVP-lite (CarePilot_Voice_Triage_MVP_Lite_v1.1): a health message brings an automated call with
-- 10 questions; plain rules sort the answers into Red, Amber or Green; each colour goes to a different kind of care.
-- It replaces the Use Case 2c handover for health messages in the data sets that switch it on (settings.voice_triage).

-- The ER Desk (duty nurse and ER doctor) sees the Red alerts.
ALTER TABLE app_user DROP CONSTRAINT app_user_role_check;
ALTER TABLE app_user ADD CONSTRAINT app_user_role_check
    CHECK (role IN ('coordinator', 'oncologist', 'chief', 'er_desk'));

-- What the hospital record holds for the call and the emergency summary (synthetic): arm baseline, last headache,
-- the doctor's signed advice, earlier events, cancer history, medicines, the oncologist's signed note.
CREATE TABLE triage_history (
    mrn  text PRIMARY KEY REFERENCES patient (mrn),
    data jsonb NOT NULL
);

CREATE TABLE triage_episode (
    episode_id      bigserial PRIMARY KEY,
    mrn             text NOT NULL REFERENCES patient (mrn),
    inbound_id      bigint REFERENCES inbound_message (inbound_id),
    patient_words   text,
    translation     text,
    opened_at       timestamp NOT NULL,               -- demo time
    -- The call: due (ringing within 2 minutes), in_call, done (a result), no_answer (two missed calls)
    call_status     text NOT NULL DEFAULT 'due' CHECK (call_status IN ('due', 'in_call', 'done', 'no_answer')),
    call_due_at     timestamp,
    attempts        integer NOT NULL DEFAULT 0,
    answers         jsonb NOT NULL,
    turns           jsonb NOT NULL DEFAULT '[]',
    current_q       text,
    reasked         boolean NOT NULL DEFAULT false,
    nurse_pressed   boolean NOT NULL DEFAULT false,
    fault           boolean NOT NULL DEFAULT false,
    stopped_early   boolean NOT NULL DEFAULT false,
    -- The rules' result (tier, reason codes, action, Why lines; protocol DEMO_LITE, DRAFT_UNSIGNED)
    result          jsonb,
    tier            text CHECK (tier IN ('TIER_1_RED', 'TIER_2_AMBER', 'TIER_3_GREEN')),
    red_kind        text CHECK (red_kind IN ('CONFIRMED', 'NO_ANSWER', 'UNCONFIRMED')),
    result_at       timestamp,
    callback_due_at timestamp,                         -- Amber 2 hours; nurse 15 minutes
    -- The Red package
    package_sent_at timestamp,
    package_sent_by text,
    doctor_note     text,
    er_ack_by       text,
    er_ack_at       timestamp,
    nurse_callback  jsonb,                             -- {by, at, note}
    ed_calls        jsonb NOT NULL DEFAULT '[]',       -- the coordinator's calls to the family
    ed_outcome      text CHECK (ed_outcome IN ('REACHED', 'NOT_YET', 'REFUSED', 'NOT_REACHABLE')),
    doctor_told_at  timestamp,
    -- Amber and Green
    coord_ack_by    text,
    coord_ack_at    timestamp,
    doctor_order    jsonb,                             -- {order, note, by, at, card_id}
    tier_changes    jsonb NOT NULL DEFAULT '[]',       -- the doctor's raise or lower, with a reason
    followup_due_on date,
    followup        jsonb,                             -- {answer, at, how}
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    closed_by       text,
    closed_at       timestamp,
    close_note      text,
    CHECK (status <> 'closed' OR (closed_by IS NOT NULL AND length(trim(close_note)) > 0))
);
CREATE UNIQUE INDEX triage_one_open_episode ON triage_episode (mrn) WHERE status = 'open';

-- The one-page emergency summary (PDF) sent with the family message.
CREATE TABLE triage_document (
    doc_id     bigserial PRIMARY KEY,
    episode_id bigint NOT NULL REFERENCES triage_episode (episode_id),
    file_name  text NOT NULL,
    mime       text NOT NULL,
    data       bytea NOT NULL,
    created_at timestamp NOT NULL                       -- demo time
);

ALTER TABLE outbox_message ADD COLUMN attachment_doc_id bigint REFERENCES triage_document (doc_id);
ALTER TABLE outbox_message ADD COLUMN triage_episode_id bigint REFERENCES triage_episode (episode_id);

-- The "confirm the emergency visit" task is due in minutes, not working days.
ALTER TABLE coordinator_task
    ADD COLUMN due_at_demo       timestamp,
    ADD COLUMN triage_episode_id bigint REFERENCES triage_episode (episode_id);

-- Bulbul audio of the fixed call lines, made once (not demo data: kept across resets).
CREATE TABLE tts_cache (
    cache_key  text PRIMARY KEY,
    mime       text NOT NULL,
    data       bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
