-- Use Case 1 (Outbound messaging, G0 booking reminder, v1.1).
-- Permanent rule changes: gap states open / reminder_sent / blocked (with the
-- reason) / resolved, gap key G0 + card + due date, one reminder per gap enforced
-- by the database, booking source on cards, demo date and time on audit entries,
-- care plans whose due date is entered. Plus per-scenario settings (switches).

ALTER TABLE config_versions DROP CONSTRAINT config_versions_kind_check;
ALTER TABLE config_versions ADD CONSTRAINT config_versions_kind_check
    CHECK (kind IN ('plan_template', 'rule_thresholds', 'message_template', 'autonomy_policy', 'deadline_policy',
                    'scenario_settings'));

-- Use Case 1 test patients are TP001-TP003; Step A patients stay SYN-0000.
ALTER TABLE patient DROP CONSTRAINT patient_mrn_check;
ALTER TABLE patient ADD CONSTRAINT patient_mrn_check CHECK (mrn ~ '^(SYN-[0-9]{4}|TP[0-9]{3})$');

-- Care plans whose due dates are entered at confirmation need no therapy or end date.
ALTER TABLE care_plan ALTER COLUMN therapy DROP NOT NULL;
ALTER TABLE care_plan ALTER COLUMN treatment_end_date DROP NOT NULL;

-- How a card was booked (button tap, coordinator, appointment list); empty at creation.
ALTER TABLE order_card ADD COLUMN booking_source text;

-- ---------------------------------------------------------------------------
-- Gap states
-- ---------------------------------------------------------------------------
ALTER TABLE gap_event DROP CONSTRAINT gap_event_status_check;
ALTER TABLE gap_event RENAME COLUMN last_skip_reason TO blocked_reason;
UPDATE gap_event SET status = 'reminder_sent' WHERE status = 'acted' AND outcome IN ('sent', 'pending_approval');
UPDATE gap_event SET status = 'blocked', blocked_reason = coalesce(blocked_reason, outcome) WHERE status = 'acted';
UPDATE gap_event SET status = 'blocked' WHERE status = 'open' AND blocked_reason IS NOT NULL;
ALTER TABLE gap_event ADD CONSTRAINT gap_event_status_check
    CHECK (status IN ('open', 'reminder_sent', 'blocked', 'resolved'));
ALTER TABLE gap_event DROP CONSTRAINT gap_event_rule_card_id_key;
ALTER TABLE gap_event ADD CONSTRAINT gap_event_key UNIQUE (rule, card_id, due_date);

-- One reminder per gap, template and recipient; a second run cannot add another.
CREATE UNIQUE INDEX outbox_one_reminder_per_gap ON outbox_message (gap_id, template_id, recipient)
    WHERE gap_id IS NOT NULL AND status IN ('sent', 'pending_approval');

-- ---------------------------------------------------------------------------
-- Audit: demo date and time of each entry (the demo clock, not the system clock).
-- Covered by the hash when set; earlier entries keep their hashes.
-- ---------------------------------------------------------------------------
ALTER TABLE audit_ledger ADD COLUMN demo_date date, ADD COLUMN demo_time text;

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
           END
        || CASE WHEN num_nonnulls(r.demo_date, r.demo_time) = 0 THEN ''
                ELSE '|demo|' || coalesce(r.demo_date::text, '') || '|' || coalesce(r.demo_time, '')
           END,
        'UTF8')), 'hex')
$$;
