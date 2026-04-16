-- Federation: public-channel patterns + votes.
--
-- See sentinel/growth/federation.py for the design doc. This migration
-- provisions the tables. The submission path (client → server) is a
-- later PR; for now the tab only reads + votes on seeded patterns.
--
-- Patterns are intentionally anonymous: creator_id is NULL for seeded
-- demo rows and optional for future user submissions. The UNIQUE
-- (user_id, pattern_id) on pattern_votes prevents double-voting.

CREATE TABLE IF NOT EXISTS patterns (
    id              TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    statement       TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0.5,
    sample_n        INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'pending',
    votes_confirm   INTEGER NOT NULL DEFAULT 0,
    votes_refute    INTEGER NOT NULL DEFAULT 0,
    votes_unclear   INTEGER NOT NULL DEFAULT 0,
    promoted_at     TEXT,
    creator_id      TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_patterns_status ON patterns(status);
CREATE INDEX IF NOT EXISTS idx_patterns_category ON patterns(category);

CREATE TABLE IF NOT EXISTS pattern_votes (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    pattern_id      TEXT NOT NULL REFERENCES patterns(id),
    vote            TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, pattern_id)
);
CREATE INDEX IF NOT EXISTS idx_pattern_votes_pattern ON pattern_votes(pattern_id);

-- Seed demo patterns so the tab has something to show on first launch.
-- These are illustrative — real patterns will come from slime clients
-- in the next PR. ON CONFLICT DO NOTHING works on both SQLite (≥3.24)
-- and Postgres, so re-running the migration is idempotent.
INSERT INTO patterns (id, category, statement, confidence, sample_n, status) VALUES
    ('pat_demo_01', 'schedule', '這個主人晚上 11 點後工作效率反而更好', 0.72, 14, 'pending') ON CONFLICT (id) DO NOTHING;
INSERT INTO patterns (id, category, statement, confidence, sample_n, status) VALUES
    ('pat_demo_02', 'tooling',  '寫程式的人常同時開 3 個以上的終端機', 0.81, 22, 'pending') ON CONFLICT (id) DO NOTHING;
INSERT INTO patterns (id, category, statement, confidence, sample_n, status) VALUES
    ('pat_demo_03', 'workflow', '研究型任務通常在午餐前最有進展', 0.65, 9, 'pending') ON CONFLICT (id) DO NOTHING;
INSERT INTO patterns (id, category, statement, confidence, sample_n, status) VALUES
    ('pat_demo_04', 'focus',    '連續兩小時深度專注後 需要至少 15 分鐘休息', 0.88, 31, 'pending') ON CONFLICT (id) DO NOTHING;
INSERT INTO patterns (id, category, statement, confidence, sample_n, status) VALUES
    ('pat_demo_05', 'health',   '夜貓族早上第一杯水會比咖啡晚喝', 0.55, 7, 'pending') ON CONFLICT (id) DO NOTHING;
INSERT INTO patterns (id, category, statement, confidence, sample_n, status) VALUES
    ('pat_demo_06', 'schedule', '週五下午的 commit 訊息明顯比較短', 0.70, 12, 'pending') ON CONFLICT (id) DO NOTHING;
