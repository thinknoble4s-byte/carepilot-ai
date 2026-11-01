-- Amber workflow v2 (proposal of 1 Nov): the duty doctor owns every Amber ticket with a 2-hour callback. The nurse
-- is no longer in the Amber path. The doctor logs each call attempt, any number of orders (now a list) and an
-- outcome (close now, or after the 24-hour follow-up call). A reminder at 90 minutes, an overdue escalation at 120,
-- the coordinator's call to the attender when the doctor cannot reach her, and Red (unconfirmed) when she is still
-- unreached at the end of the 2 hours. A "same" or "worse" follow-up reopens the ticket as a new Amber round.

ALTER TABLE triage_episode
    ADD COLUMN doctor_calls   jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{at, by, outcome PICKED_UP|NO_ANSWER, note}]
    ADD COLUMN attender_calls jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{at, by, outcome REACHED|NOT_REACHABLE, note}]
    ADD COLUMN doctor_outcome jsonb,                               -- {at, by, note, close NOW|FOLLOWUP}
    ADD COLUMN sla_start      timestamp,                           -- start of this Amber round's 2 hours
    ADD COLUMN reminded_at    timestamp,                           -- 90 minutes, not acknowledged
    ADD COLUMN overdue_at     timestamp,                           -- 120 minutes, no call logged
    ADD COLUMN rounds         jsonb NOT NULL DEFAULT '[]'::jsonb;  -- earlier Amber rounds, kept when it reopens

-- doctor_order now holds a list of orders: [{order, text, by, at, card_id}]
UPDATE triage_episode SET doctor_order = jsonb_build_array(doctor_order)
WHERE doctor_order IS NOT NULL AND jsonb_typeof(doctor_order) = 'object';
UPDATE triage_episode SET sla_start = result_at WHERE tier = 'TIER_2_AMBER' AND sla_start IS NULL;
