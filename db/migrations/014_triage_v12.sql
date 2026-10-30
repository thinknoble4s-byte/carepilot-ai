-- Voice Triage v1.2: the router's plan for each call (the model's tags, the modules, the questions it chose and the
-- floors her own words set), the ticket's reopen time (state REOPENED until someone acknowledges again) and the ER
-- doctor's "Log what was done" on a Red ticket.

ALTER TABLE triage_episode ADD COLUMN plan jsonb;         -- {route, profile, question_ids, modules, floors, tags}
ALTER TABLE triage_episode ADD COLUMN reopened_at timestamp;
ALTER TABLE triage_episode ADD COLUMN er_log jsonb;       -- Red: {by, at, note}
