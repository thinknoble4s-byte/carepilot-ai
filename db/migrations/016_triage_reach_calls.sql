-- Testing round 1 Nov (Worksheet2): an empty message whose two automated calls go unanswered becomes a staff call.

-- A patient can send a message with nothing in it: it is kept as its own kind, so every other message still needs
-- a button, some text, a voice note or a document.
ALTER TABLE inbound_message DROP CONSTRAINT IF EXISTS inbound_message_kind_check;
ALTER TABLE inbound_message ADD CONSTRAINT inbound_message_kind_check
    CHECK (kind IN ('button', 'text', 'voice', 'document', 'empty'));
ALTER TABLE inbound_message DROP CONSTRAINT IF EXISTS inbound_message_has_content;
ALTER TABLE inbound_message ADD CONSTRAINT inbound_message_has_content
    CHECK (button IS NOT NULL OR length(trim(coalesce(text, ''))) > 0 OR audio_id IS NOT NULL OR kind = 'empty');

-- Each call a coordinator or the ER Desk makes to reach her is kept on the episode: who, when, the result
-- (REACHED or NOT_REACHABLE), what was decided (close the ticket or escalate to the doctor) and the note.
ALTER TABLE triage_episode ADD COLUMN reach_calls jsonb NOT NULL DEFAULT '[]'::jsonb;
