-- Users
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    google_sub      TEXT UNIQUE NOT NULL,
    email           TEXT NOT NULL,
    display_name    TEXT NOT NULL DEFAULT '',
    photo_url       TEXT NOT NULL DEFAULT '',
    wallet_uid      TEXT NOT NULL DEFAULT '',
    referral_code   TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub);

-- Community equipment submissions
CREATE TABLE IF NOT EXISTS equipment_submissions (
    id              TEXT PRIMARY KEY,
    creator_id      TEXT NOT NULL REFERENCES users(id),
    name            TEXT NOT NULL,
    slot            TEXT NOT NULL,
    rarity          TEXT NOT NULL,
    visual          TEXT NOT NULL DEFAULT '',
    buff            TEXT,
    description     TEXT NOT NULL DEFAULT '',
    image_id        TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    vote_count      INTEGER NOT NULL DEFAULT 0,
    vote_threshold  INTEGER NOT NULL DEFAULT 10,
    approved_at     TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON equipment_submissions(status);
CREATE INDEX IF NOT EXISTS idx_submissions_creator ON equipment_submissions(creator_id);

-- Votes
CREATE TABLE IF NOT EXISTS votes (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    submission_id   TEXT NOT NULL REFERENCES equipment_submissions(id),
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, submission_id)
);
CREATE INDEX IF NOT EXISTS idx_votes_submission ON votes(submission_id);

-- Approved community equipment pool
CREATE TABLE IF NOT EXISTS community_equipment (
    id              TEXT PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    slot            TEXT NOT NULL,
    rarity          TEXT NOT NULL,
    visual          TEXT NOT NULL DEFAULT '',
    buff            TEXT,
    description     TEXT NOT NULL DEFAULT '',
    image_url       TEXT NOT NULL DEFAULT '',
    creator_id      TEXT NOT NULL REFERENCES users(id),
    approved_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    version         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_community_equip_slot ON community_equipment(slot);

-- Marketplace listings
CREATE TABLE IF NOT EXISTS marketplace_listings (
    id              TEXT PRIMARY KEY,
    seller_id       TEXT NOT NULL REFERENCES users(id),
    item_id         TEXT NOT NULL,
    template_name   TEXT NOT NULL,
    slot            TEXT NOT NULL,
    rarity          TEXT NOT NULL,
    price           INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sold_at         TEXT,
    buyer_id        TEXT REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_listings_status ON marketplace_listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_slot_rarity ON marketplace_listings(slot, rarity);

-- Trade history
CREATE TABLE IF NOT EXISTS trade_history (
    id              TEXT PRIMARY KEY,
    listing_id      TEXT NOT NULL REFERENCES marketplace_listings(id),
    seller_id       TEXT NOT NULL,
    buyer_id        TEXT NOT NULL,
    template_name   TEXT NOT NULL,
    price           INTEGER NOT NULL,
    fee             INTEGER NOT NULL,
    seller_received INTEGER NOT NULL,
    completed_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Uploaded images
CREATE TABLE IF NOT EXISTS images (
    id              TEXT PRIMARY KEY,
    uploader_id     TEXT NOT NULL REFERENCES users(id),
    filename        TEXT NOT NULL,
    content_type    TEXT NOT NULL DEFAULT 'image/png',
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Pool sync version tracker
CREATE TABLE IF NOT EXISTS pool_sync (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    current_version INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO pool_sync (id, current_version) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;
