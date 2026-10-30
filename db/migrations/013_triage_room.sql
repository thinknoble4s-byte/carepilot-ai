-- The Triage Room: one staff-only screen, one ticket per triage episode (replaces the separate Voice triage and
-- ER Desk screens). A ticket ID, the Green digest acknowledgement, the Amber cover alert and where each turn
-- starts in the call recording.

ALTER TABLE triage_episode ADD COLUMN ticket_id text;
ALTER TABLE triage_episode ADD COLUMN digest_ack_by text;          -- Green: the coordinator acknowledged the digest
ALTER TABLE triage_episode ADD COLUMN digest_ack_at timestamp;
ALTER TABLE triage_episode ADD COLUMN cover_alerted_at timestamp;  -- Amber: no doctor outcome in 90 minutes
ALTER TABLE triage_episode ADD COLUMN rec_offsets jsonb;           -- {turn index: seconds} in the call recording

-- CP-<date opened>-<number that day>, e.g. CP-20261027-0001
UPDATE triage_episode e SET ticket_id = t.id
FROM (SELECT episode_id,
             'CP-' || to_char(opened_at, 'YYYYMMDD') || '-' ||
             lpad(row_number() OVER (PARTITION BY opened_at::date ORDER BY episode_id)::text, 4, '0') AS id
      FROM triage_episode) t
WHERE e.episode_id = t.episode_id;

ALTER TABLE triage_episode ALTER COLUMN ticket_id SET NOT NULL;
CREATE UNIQUE INDEX triage_ticket_id ON triage_episode (ticket_id);
