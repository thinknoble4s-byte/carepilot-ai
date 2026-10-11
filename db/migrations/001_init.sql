-- Increment 0: audit ledger (append-only, hash-chained) and versioned config.

-- ---------------------------------------------------------------------------
-- Audit ledger: four actor types; ids are assigned under a lock inside the
-- trigger so the chain order equals id order even with concurrent writers.
-- ---------------------------------------------------------------------------
CREATE SEQUENCE audit_ledger_seq;

CREATE TABLE audit_ledger (
    id               bigint PRIMARY KEY,
    occurred_at      timestamptz NOT NULL,
    actor_type       text NOT NULL CHECK (actor_type IN ('person', 'system', 'agent', 'ai_suggestion')),
    actor_name       text NOT NULL,
    action           text NOT NULL,
    entity_type      text,
    entity_id        text,
    details          jsonb NOT NULL DEFAULT '{}'::jsonb,
    policy_version   text,
    template_version text,
    prev_hash        text NOT NULL,
    hash             text NOT NULL
);

CREATE FUNCTION audit_ledger_hash(r audit_ledger) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(concat_ws('|',
        r.prev_hash,
        r.id::text,
        to_char(r.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        r.actor_type, r.actor_name, r.action,
        coalesce(r.entity_type, ''), coalesce(r.entity_id, ''),
        r.details::text,
        coalesce(r.policy_version, ''), coalesce(r.template_version, '')
    ), 'UTF8')), 'hex')
$$;

CREATE FUNCTION audit_ledger_chain() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    last_hash text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('audit_ledger'));
    NEW.id := nextval('audit_ledger_seq');
    NEW.occurred_at := clock_timestamp();
    SELECT hash INTO last_hash FROM audit_ledger ORDER BY id DESC LIMIT 1;
    NEW.prev_hash := coalesce(last_hash, repeat('0', 64));
    NEW.hash := audit_ledger_hash(NEW);
    RETURN NEW;
END $$;

CREATE TRIGGER audit_ledger_chain BEFORE INSERT ON audit_ledger
    FOR EACH ROW EXECUTE FUNCTION audit_ledger_chain();

CREATE FUNCTION audit_ledger_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_ledger is append-only (% blocked)', TG_OP;
END $$;

CREATE TRIGGER audit_ledger_no_update BEFORE UPDATE OR DELETE ON audit_ledger
    FOR EACH ROW EXECUTE FUNCTION audit_ledger_immutable();
CREATE TRIGGER audit_ledger_no_truncate BEFORE TRUNCATE ON audit_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION audit_ledger_immutable();

-- Walk the chain in id order; first_bad_id is NULL when the chain is intact.
CREATE FUNCTION audit_ledger_verify(OUT checked bigint, OUT first_bad_id bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r audit_ledger;
    expected_prev text := repeat('0', 64);
BEGIN
    checked := 0;
    FOR r IN SELECT * FROM audit_ledger ORDER BY id LOOP
        IF r.prev_hash <> expected_prev OR r.hash <> audit_ledger_hash(r) THEN
            first_bad_id := r.id;
            RETURN;
        END IF;
        expected_prev := r.hash;
        checked := checked + 1;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- Versioned, signed-off configuration: rules, templates, autonomy policy.
-- ---------------------------------------------------------------------------
CREATE TABLE config_versions (
    id            bigserial PRIMARY KEY,
    kind          text NOT NULL CHECK (kind IN ('plan_template', 'rule_thresholds', 'message_template', 'autonomy_policy')),
    name          text NOT NULL,
    version       int  NOT NULL,
    body          jsonb NOT NULL,
    status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'retired')),
    signed_off_by text,
    signed_off_at timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, name, version),
    CHECK (status <> 'active' OR signed_off_by IS NOT NULL)
);

CREATE UNIQUE INDEX config_versions_one_active ON config_versions (kind, name) WHERE status = 'active';

-- Thresholds from the agentic architecture (illustrative, synthetic sign-off).
INSERT INTO config_versions (kind, name, version, body, status, signed_off_by, signed_off_at)
VALUES ('rule_thresholds', 'default', 1,
        '{"g0_booking_days_before_due": 7, "g0_booking_help_days_before_due": 3,
          "visit_grace_days": 2, "refill_grace_days": 3, "report_wait_days": 7,
          "return_window_days": 21, "g4_unanswered_messages": 2, "g4_wait_hours": 24}'::jsonb,
        'active', 'Synthetic seed (pending clinical sign-off)', now());

INSERT INTO audit_ledger (actor_type, actor_name, action, entity_type, entity_id, details)
VALUES ('system', 'CarePilot setup', 'config.activated', 'config_versions', 'rule_thresholds/default/v1',
        '{"synthetic": true}'::jsonb);
