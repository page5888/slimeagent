-- Creator reward ledger (Phase 1 of slime_creator_reward flow)
--
-- While 5888's atomic s2sCreatorRewardSettle is not yet shipped (target
-- Week 5-6 per project_marketplace.md), voter payments for
-- slime_creator_reward land in the 5888 platform pool via s2sSpend.
-- Creators are NOT yet credited through the 5888 wallet.
--
-- This table records what each creator is owed. When Phase 2 ships,
-- a one-shot migration script walks pending rows and invokes the
-- atomic settle endpoint using `slime_creator_reward_settle:{ledger_id}`
-- as the idempotency key (5888's dedupe layer will collapse retries).
--
-- After migration completes + settle endpoint is proven stable:
--   - cast_vote() stops inserting new ledger rows
--   - cast_vote() calls s2sCreatorRewardSettle atomically instead
--   - This table becomes a historical audit record
CREATE TABLE IF NOT EXISTS creator_reward_ledger (
    id               TEXT PRIMARY KEY,
    creator_id       TEXT NOT NULL REFERENCES users(id),
    -- NULL when the ledger row is a system-generated approval bonus
    -- (+100 pts awarded when a submission crosses its vote threshold,
    --  not attributable to any specific voter).
    voter_id         TEXT REFERENCES users(id),
    submission_id    TEXT NOT NULL REFERENCES equipment_submissions(id),
    amount           INTEGER NOT NULL,
    -- The spend's 5888 idempotency key for per-vote rows
    -- ("slime_creator_reward:{vote_id}"), or a synthetic key for the
    -- approval bonus ("slime_creator_approval:{submission_id}") — the
    -- prefix lets the Phase 2 replay distinguish the two.
    voter_spend_key  TEXT NOT NULL UNIQUE,
    status           TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'settled'
    settled_at       TEXT,
    settle_tx_id     TEXT,  -- set when Phase 2 replay calls s2sCreatorRewardSettle
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ledger_creator
    ON creator_reward_ledger(creator_id, status);
CREATE INDEX IF NOT EXISTS idx_ledger_submission
    ON creator_reward_ledger(submission_id);
