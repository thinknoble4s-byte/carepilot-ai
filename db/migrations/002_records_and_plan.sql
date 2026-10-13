-- Increment 1: records and plan (Golden Path Steps 0-1).
-- Step A student guide schema, translated to PostgreSQL; decisions in documents/build-guide-comparison.md.

-- ---------------------------------------------------------------------------
-- Audit ledger: extra fields for later agent and AI steps, actor type 'ai'.
-- The hash covers the new fields only when one is set, so earlier entries
-- keep their hashes and the chain stays valid.
-- ---------------------------------------------------------------------------
ALTER TABLE audit_ledger
    ADD COLUMN mrn             text,
    ADD COLUMN reason_code     text,
    ADD COLUMN decision_inputs jsonb,
    ADD COLUMN source_link     text,
    ADD COLUMN trigger_event   text,
    ADD COLUMN rule            text,
    ADD COLUMN task            text,
    ADD COLUMN mode            text,
    ADD COLUMN template_id     text,
    ADD COLUMN model           text,
    ADD COLUMN prompt_version  text,
    ADD COLUMN outcome         text;

ALTER TABLE audit_ledger DROP CONSTRAINT audit_ledger_actor_type_check;
ALTER TABLE audit_ledger ADD CONSTRAINT audit_ledger_actor_type_check
    CHECK (actor_type IN ('person', 'system', 'agent', 'ai', 'ai_suggestion'));

CREATE INDEX audit_ledger_mrn ON audit_ledger (mrn) WHERE mrn IS NOT NULL;

CREATE OR REPLACE FUNCTION audit_ledger_hash(r audit_ledger) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(
        concat_ws('|',
            r.prev_hash,
            r.id::text,
            to_char(r.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            r.actor_type, r.actor_name, r.action,
            coalesce(r.entity_type, ''), coalesce(r.entity_id, ''),
            r.details::text,
            coalesce(r.policy_version, ''), coalesce(r.template_version, ''))
        || CASE WHEN num_nonnulls(r.mrn, r.reason_code, r.decision_inputs, r.source_link, r.trigger_event,
                                  r.rule, r.task, r.mode, r.template_id, r.model, r.prompt_version, r.outcome) = 0
                THEN ''
                ELSE '|' || concat_ws('|',
                    coalesce(r.mrn, ''), coalesce(r.reason_code, ''), coalesce(r.decision_inputs::text, ''),
                    coalesce(r.source_link, ''), coalesce(r.trigger_event, ''), coalesce(r.rule, ''),
                    coalesce(r.task, ''), coalesce(r.mode, ''), coalesce(r.template_id, ''),
                    coalesce(r.model, ''), coalesce(r.prompt_version, ''), coalesce(r.outcome, ''))
           END,
        'UTF8')), 'hex')
$$;

-- Demo reset is the one allowed way to clear the ledger: TRUNCATE inside a
-- transaction that set carepilot.demo_reset = 'on' (app/seed.py). UPDATE and
-- DELETE stay blocked without exception.
CREATE OR REPLACE FUNCTION audit_ledger_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' AND current_setting('carepilot.demo_reset', true) = 'on' THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'audit_ledger is append-only (% blocked)', TG_OP;
END $$;

-- ---------------------------------------------------------------------------
-- People, demo clock
-- ---------------------------------------------------------------------------
CREATE TABLE app_user (
    user_id text PRIMARY KEY,
    name    text NOT NULL,
    role    text NOT NULL CHECK (role IN ('coordinator', 'oncologist', 'chief'))
);

CREATE TABLE demo_clock (
    id    int PRIMARY KEY CHECK (id = 1),
    today date NOT NULL
);

-- ---------------------------------------------------------------------------
-- Imports: register, appointments, exceptions
-- ---------------------------------------------------------------------------
CREATE TABLE import_batch (
    batch_id               bigserial PRIMARY KEY,
    source_type            text NOT NULL CHECK (source_type IN ('register', 'appointments')),
    file_name              text,
    imported_by            text NOT NULL REFERENCES app_user (user_id),
    imported_at            timestamptz NOT NULL DEFAULT now(),
    rows_read              int NOT NULL,
    rows_created           int NOT NULL,
    rows_skipped_identical int NOT NULL,
    rows_exception         int NOT NULL
);

CREATE TABLE patient (
    mrn                text PRIMARY KEY CHECK (mrn ~ '^SYN-[0-9]{4}$'),
    full_name          text NOT NULL,
    phone              text NOT NULL,
    caregiver_name     text,
    caregiver_phone    text,
    preferred_language text NOT NULL,
    status             text NOT NULL DEFAULT 'Active'
        CHECK (status IN ('Active', 'Paused', 'Agent off', 'Lost to follow-up', 'Followed up elsewhere')),
    agent_on           boolean NOT NULL DEFAULT false,
    batch_id           bigint NOT NULL REFERENCES import_batch (batch_id),
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE appointment (
    appointment_id   bigserial PRIMARY KEY,
    mrn              text NOT NULL REFERENCES patient (mrn),
    appointment_date date NOT NULL,
    department       text NOT NULL,
    batch_id         bigint NOT NULL REFERENCES import_batch (batch_id),
    UNIQUE (mrn, appointment_date, department)
);

CREATE TABLE import_exception (
    exception_id     bigserial PRIMARY KEY,
    batch_id         bigint NOT NULL REFERENCES import_batch (batch_id),
    source_type      text NOT NULL,
    row_number       int NOT NULL,          -- line in the file, header = 1
    raw_row          text NOT NULL,
    reason_code      text NOT NULL,
    status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'dismissed')),
    dismissed_by     text REFERENCES app_user (user_id),
    dismissed_reason text,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Consent and coordinator tasks
-- ---------------------------------------------------------------------------
CREATE TABLE consent (
    consent_id        bigserial PRIMARY KEY,
    mrn               text NOT NULL REFERENCES patient (mrn),
    consent_reference text NOT NULL,
    scopes            text[] NOT NULL CHECK (cardinality(scopes) > 0),
    recorded_by       text NOT NULL REFERENCES app_user (user_id),
    recorded_on       date NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX consent_by_patient ON consent (mrn, consent_id DESC);

CREATE TABLE coordinator_task (
    task_id    bigserial PRIMARY KEY,
    mrn        text NOT NULL REFERENCES patient (mrn),
    task_type  text NOT NULL,              -- e.g. consent_scope_missing
    reason     text,
    status     text NOT NULL DEFAULT 'open',
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Care plan and order cards
-- ---------------------------------------------------------------------------
CREATE TABLE care_plan (
    care_plan_id        bigserial PRIMARY KEY,
    mrn                 text NOT NULL UNIQUE REFERENCES patient (mrn),  -- one plan per patient
    template_id         text NOT NULL,
    template_version    text NOT NULL,
    therapy             text NOT NULL,
    treatment_end_date  date NOT NULL,
    identifiers_checked text[] NOT NULL,
    confirmed_by        text NOT NULL REFERENCES app_user (user_id),
    confirmed_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE order_card (
    card_id          bigserial PRIMARY KEY,
    mrn              text NOT NULL REFERENCES patient (mrn),
    care_plan_id     bigint NOT NULL REFERENCES care_plan (care_plan_id),
    item_code        text NOT NULL,           -- V1, T1, V2, T2, T3
    card_type        text NOT NULL CHECK (card_type IN ('visit', 'test')),
    name             text NOT NULL,
    department       text NOT NULL,
    due_date         date NOT NULL,
    state            text NOT NULL,           -- checked in app/states.py
    appointment_date date,
    owner_name       text NOT NULL,
    cover_name       text NOT NULL,
    origin           text NOT NULL DEFAULT 'plan_item',
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (care_plan_id, item_code)
);

CREATE INDEX order_card_by_patient ON order_card (mrn, card_id);
