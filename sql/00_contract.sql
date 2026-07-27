-- The two-table contract (SPEC.md §6).
--
-- Every experiment, from any source, is mapped into exactly these two tables before
-- anything downstream touches it. No query outside adapters/ may reference a
-- dataset-specific column name.
--
-- Deliberately NOT declared here: primary keys, unique constraints, foreign keys.
-- Duplicate unit_ids and orphan events are validity *failures we need to detect and
-- report* (SPEC.md §7.2), not load errors. A database constraint would reject a
-- corrupted dataset with a stack trace at load time, which means the diagnostics could
-- never run on it and the blocking rule in §7.3 could never fire. Integrity is enforced
-- by the gate, on purpose, and the corrupted-input test in tests/ depends on that.

DROP TABLE IF EXISTS assignments;
CREATE TABLE assignments (
    unit_id     VARCHAR   NOT NULL,  -- one row per randomised unit; duplicates are a finding
    variant     VARCHAR   NOT NULL,  -- must match a variant declared in the experiment config
    assigned_at TIMESTAMP            -- NULL where the source has no time dimension
);

DROP TABLE IF EXISTS events;
CREATE TABLE events (
    unit_id    VARCHAR   NOT NULL,   -- references assignments.unit_id; orphans are a finding
    event_type VARCHAR   NOT NULL,   -- e.g. return_d1, visit, conversion
    event_at   TIMESTAMP,            -- NULL where outcomes are precomputed, not timestamped
    value      DOUBLE                -- numeric payload for value metrics; NULL for count metrics
);

-- Rows the adapter produced that could not be admitted to the contract, kept with the
-- reason rather than merely counted (SPEC.md §7.1: never silently discarded).
DROP TABLE IF EXISTS load_rejects;
CREATE TABLE load_rejects (
    table_name VARCHAR NOT NULL,
    reason     VARCHAR NOT NULL,
    unit_id    VARCHAR,
    variant    VARCHAR
);

-- Pre-treatment covariates, one row per unit. Empty for datasets that have none.
-- Populated only from columns an adapter has explicitly declared pre-treatment; see
-- docs/dataset_selection.md §2 for why that declaration is a timing claim, not a
-- statistical one.
DROP TABLE IF EXISTS covariates;
CREATE TABLE covariates (
    unit_id        VARCHAR NOT NULL,
    covariate_name VARCHAR NOT NULL,
    value          DOUBLE
);
