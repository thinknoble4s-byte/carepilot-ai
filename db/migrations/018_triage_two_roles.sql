-- Triage v3 (2 Nov): two people roles, the Coordinator and the Doctor on Duty (the ER Desk is removed). The
-- coordinator reviews the AI's Red and Amber tickets (Confirm, Raise tier or Flag as wrong; not a gate: the doctor
-- sees the ticket at once). A ticket closes with a summary drafted from the record and the doctor's recommendation,
-- edited and submitted by the person who closes it. There is no 24-hour follow-up call any more.

ALTER TABLE triage_episode
    ADD COLUMN review  jsonb,  -- {action CONFIRMED|FLAGGED|RAISED, by, at, reason}
    ADD COLUMN closing jsonb;  -- {by, at, recommendation, contact PHONE|VIDEO|IN_PERSON, draft, summary, edited}

-- The ER Desk users are gone; a Red acknowledgement is now the doctor's, on the same field as Amber.
UPDATE triage_episode SET coord_ack_by = er_ack_by, coord_ack_at = er_ack_at
WHERE tier = 'TIER_1_RED' AND coord_ack_by IS NULL AND er_ack_by IS NOT NULL;
