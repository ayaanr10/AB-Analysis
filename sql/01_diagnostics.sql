-- Validity diagnostics (SPEC.md §7.2).
--
-- These run BEFORE any effect estimate exists. That ordering is enforced structurally:
-- readout/run.py will not compute metrics until the gate has returned, so it is not
-- possible to peek at the result and then decide how hard to look at the diagnostics.
--
-- Every statistic is aggregated here in SQL (docs/decisions.md D-02). Python converts
-- test statistics to p-values and applies thresholds — distribution functions and policy,
-- not aggregation.
--
-- Depends on `config_variants(variant, intended_share, is_control)`, materialised from
-- the experiment config by readout/diagnostics.py. Keeping the config in a table rather
-- than string-formatting it into these queries means this file is static and readable on
-- its own, and the intended split cannot silently disagree between config and SQL.

-- Observed vs intended split, with each variant's contribution to the chi-squared
-- goodness-of-fit statistic. Summing the contributions gives the SRM test statistic.
CREATE OR REPLACE VIEW diag_variant_counts AS
WITH observed AS (
    SELECT variant, count(*) AS observed_units
    FROM assignments
    GROUP BY variant
),
with_total AS (
    SELECT variant,
           observed_units,
           sum(observed_units) OVER () AS total_units
    FROM observed
)
SELECT
    v.variant,
    v.is_control,
    coalesce(o.observed_units, 0)                              AS observed_units,
    coalesce(o.total_units, 0)                                 AS total_units,
    coalesce(o.observed_units, 0) / nullif(o.total_units, 0)   AS observed_share,
    v.intended_share,
    v.intended_share * o.total_units                           AS expected_units,
    pow(coalesce(o.observed_units, 0) - v.intended_share * o.total_units, 2)
        / nullif(v.intended_share * o.total_units, 0)          AS chi2_contribution
FROM config_variants v
LEFT JOIN with_total o USING (variant)
ORDER BY v.variant;

-- A unit_id appearing more than once in assignments. SPEC.md §6: duplicates are a
-- validity failure — one row per unit is the contract.
CREATE OR REPLACE VIEW diag_duplicate_units AS
SELECT
    unit_id,
    count(*)                AS n_rows,
    count(DISTINCT variant) AS n_variants
FROM assignments
GROUP BY unit_id
HAVING count(*) > 1;

-- A unit_id appearing under more than one variant. Strictly worse than a duplicate: the
-- arms are no longer disjoint, so every comparison between them is contaminated.
CREATE OR REPLACE VIEW diag_cross_contamination AS
SELECT
    unit_id,
    count(DISTINCT variant)              AS n_variants,
    string_agg(DISTINCT variant, ', ')   AS variants
FROM assignments
GROUP BY unit_id
HAVING count(DISTINCT variant) > 1;

-- Events whose unit was never assigned. Means the join key is wrong, the assignment
-- table is incomplete, or events leaked in from outside the experiment.
CREATE OR REPLACE VIEW diag_orphan_events AS
SELECT
    e.unit_id,
    count(*) AS n_events
FROM events e
LEFT JOIN assignments a USING (unit_id)
WHERE a.unit_id IS NULL
GROUP BY e.unit_id;

-- Assigned units with no events at all, per variant. A large imbalance here is itself a
-- signal (SPEC.md §7.2): it can mean differential logging, or differential drop-off
-- caused by the treatment.
CREATE OR REPLACE VIEW diag_zero_activity AS
WITH unit_activity AS (
    SELECT
        a.unit_id,
        a.variant,
        count(e.unit_id) AS n_events
    FROM assignments a
    LEFT JOIN events e USING (unit_id)
    GROUP BY a.unit_id, a.variant
)
SELECT
    variant,
    count(*)                                              AS assigned_units,
    sum(CASE WHEN n_events = 0 THEN 1 ELSE 0 END)         AS zero_activity_units,
    sum(CASE WHEN n_events = 0 THEN 1 ELSE 0 END) * 1.0
        / nullif(count(*), 0)                             AS zero_activity_rate
FROM unit_activity
GROUP BY variant
ORDER BY variant;

-- How many distinct units each event type touches. Used to detect when the zero-activity
-- check is vacuous: if some event type is emitted for every assigned unit, no unit can
-- have zero events and the check cannot fail by construction (docs/decisions.md D-08).
CREATE OR REPLACE VIEW diag_event_coverage AS
SELECT
    e.event_type,
    count(DISTINCT e.unit_id)                                   AS units_with_event,
    (SELECT count(DISTINCT unit_id) FROM assignments)           AS assigned_units,
    count(DISTINCT e.unit_id)
        = (SELECT count(DISTINCT unit_id) FROM assignments)     AS covers_every_unit
FROM events e
GROUP BY e.event_type
ORDER BY e.event_type;

-- Pre-period balance on declared pre-treatment covariates, as standardised mean
-- difference against the control arm.
--
-- SMD rather than a p-value, deliberately (docs/decisions.md D-04): at 14M rows a t-test
-- rejects every covariate at p < 0.001 while every one sits inside |SMD| = 0.05. A
-- p-value implementation would block a perfectly usable experiment. The p-value is still
-- computed in Python and shown as context — it just does not decide anything.
CREATE OR REPLACE VIEW diag_covariate_balance AS
WITH per_arm AS (
    SELECT
        c.covariate_name,
        a.variant,
        count(*)          AS n,
        avg(c.value)      AS mean_value,
        var_samp(c.value) AS var_value
    FROM covariates c
    JOIN assignments a USING (unit_id)
    GROUP BY c.covariate_name, a.variant
),
control_arm AS (
    SELECT p.covariate_name, p.n, p.mean_value, p.var_value
    FROM per_arm p
    JOIN config_variants v ON v.variant = p.variant AND v.is_control
)
SELECT
    p.covariate_name,
    p.variant,
    p.n                AS n_treatment,
    c.n                AS n_control,
    p.mean_value       AS mean_treatment,
    c.mean_value       AS mean_control,
    p.var_value        AS var_treatment,
    c.var_value        AS var_control,
    (p.mean_value - c.mean_value)
        / nullif(sqrt((p.var_value + c.var_value) / 2.0), 0) AS smd
FROM per_arm p
JOIN control_arm c USING (covariate_name)
JOIN config_variants v ON v.variant = p.variant AND NOT v.is_control
ORDER BY abs(
    (p.mean_value - c.mean_value) / nullif(sqrt((p.var_value + c.var_value) / 2.0), 0)
) DESC;
