-- Testing round 31 Oct (defects 4, 6, 8): every patient message that opened or was added to a triage episode is
-- linked to it, so the ticket lists her voice notes with their date and time, and the simulated WhatsApp chat can
-- clear an episode's messages once the episode is closed.

ALTER TABLE inbound_message ADD COLUMN triage_episode_id bigint REFERENCES triage_episode (episode_id);

UPDATE inbound_message i SET triage_episode_id = e.episode_id
FROM triage_episode e WHERE e.inbound_id = i.inbound_id;
