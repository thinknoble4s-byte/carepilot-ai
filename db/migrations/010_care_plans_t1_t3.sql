-- The three care plans (T1 hormone receptor positive, T2 hormone receptor negative, T3 HER2 positive) replace
-- BC-SURV-1 and the use-case plans. Test cards follow one of two roads, set by the plan item:
--   specimen-based (blood test):  Due -> Scheduled -> Completed -> Report received -> Report verified
--   non-specimen (imaging, echo): Due -> Scheduled -> Completed -> Verified
-- The verified status is set by the doctor only: who and when are kept on the card (and in the audit ledger).
ALTER TABLE order_card
    ADD COLUMN specimen         boolean,      -- test cards: true = specimen-based; NULL on visit cards
    ADD COLUMN verified_by      text,         -- the doctor who set Report verified / Verified
    ADD COLUMN verified_at_demo timestamp;
