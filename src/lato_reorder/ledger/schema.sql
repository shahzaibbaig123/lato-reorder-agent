-- PO ledger: the system of record for purchase-order intents, approvals and outcomes.
-- SAP does not deduplicate orders (a repeated call creates a second PO), so these constraints are
-- what guarantee "never order the same part twice".

CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    mode           TEXT NOT NULL,
    llm            TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_hash    TEXT NOT NULL,
    snapshot_hash  TEXT NOT NULL,
    plan_json      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS po_intents (
    intent_id          TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES runs (run_id),
    part_key           TEXT NOT NULL,
    item_name          TEXT NOT NULL, -- exact snapshot title, sent verbatim as ItemName
    supplier           TEXT,
    quantity           INTEGER NOT NULL CHECK (quantity > 0), -- what will be / was ordered
    engine_quantity    INTEGER NOT NULL CHECK (engine_quantity > 0),
    route              TEXT NOT NULL CHECK (route IN ('AUTO', 'HUMAN')),
    decision_hash      TEXT NOT NULL,
    status             TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    on_hand_at_plan    INTEGER,
    on_hand_at_submit  INTEGER,
    approved_expires_at TEXT,
    binding_hash       TEXT,
    sap_po_number      TEXT,
    parsed_quantity    INTEGER,
    raw_response       TEXT,
    submitted_at       TEXT
);

-- At most one open intent per part, enforced by the database, not just the code.
CREATE UNIQUE INDEX IF NOT EXISTS one_open_intent_per_part ON po_intents (part_key)
WHERE status IN ('PENDING_APPROVAL', 'APPROVED', 'SUBMITTING', 'SUBMITTED', 'SUBMITTED_MISMATCH', 'UNKNOWN');

CREATE TABLE IF NOT EXISTS approvals (
    approval_id  TEXT PRIMARY KEY,
    intent_id    TEXT NOT NULL REFERENCES po_intents (intent_id),
    actor        TEXT NOT NULL, -- "user:<name>" or "policy:auto@<version>"
    action       TEXT NOT NULL CHECK (action IN ('APPROVE', 'EDIT', 'REJECT')),
    quantity     INTEGER,
    reason       TEXT,
    binding_hash TEXT,
    created_at   TEXT NOT NULL,
    expires_at   TEXT
);

-- Append-only audit trail of every state change.
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    intent_id   TEXT,
    run_id      TEXT,
    kind        TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    detail      TEXT
);

CREATE TRIGGER IF NOT EXISTS events_append_only_update BEFORE UPDATE ON events
BEGIN SELECT RAISE (ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_append_only_delete BEFORE DELETE ON events
BEGIN SELECT RAISE (ABORT, 'events are append-only'); END;
