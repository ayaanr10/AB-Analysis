-- Metric aggregation across horizons (SPEC.md §7.5).
--
-- ALL aggregation for the readout happens here. Python does bootstrap resampling, power
-- calculation and simulation, and nothing else (docs/decisions.md D-02). This is the file
-- that has to earn the project's claim to be SQL evidence, so it is written to be read.
--
-- Depends on two tables materialised from the experiment config by readout/metrics.py:
--   config_variants(variant, intended_share, is_control)
--   metric_spec(metric_name, metric_label, role, metric_type, direction,
--               horizon_name, horizon_label, event_type, mode, window_days)
--
-- Nothing here names a dataset-specific column. The same three views produce the Cookie
-- Cats readout and the Criteo readout; only the contents of metric_spec differ.

-- Every unit's contribution to every (metric, horizon), including the units that did
-- nothing. The LEFT JOIN is the important part: a unit with no qualifying event scores 0
-- and stays in the denominator. Dropping it would silently redefine "day-7 retention"
-- from "share of assigned players who returned" to "share of returning players who
-- returned", which is 100% by construction.
-- Materialised, not a view: every unit appears once per (metric, horizon), so on Criteo
-- this is 3 metrics x 14M units = 42M rows built against the events table. As a view that
-- work would be redone by metric_by_variant, again by metric_effects, and again by the
-- power calculation. Computed once instead.
--
-- Split by horizon mode, which is a performance decision with a real payoff. The obvious
-- single query groups 42M joined rows by unit to collapse multiple events per unit, and
-- hashing 42M string keys took over ten minutes on Criteo. A precomputed horizon does not
-- need that: the events table can be collapsed to one row per (unit, event_type) *first*,
-- while it is still 1.1M rows, and then joined. An elapsed horizon genuinely cannot be
-- pre-aggregated, because its window depends on each unit's own assigned_at, so it keeps
-- the original per-row join.
--
-- Neither real dataset uses the elapsed path (both ship precomputed outcomes), so it is
-- covered by tests/test_metrics.py rather than by either case study.
CREATE OR REPLACE TABLE metric_unit_values AS
WITH event_totals AS (
    SELECT
        unit_id,
        event_type,
        count(*)   AS event_count,
        sum(value) AS value_total
    FROM events
    GROUP BY unit_id, event_type
),
precomputed AS (
    SELECT
        s.metric_name,
        s.horizon_name,
        a.variant,
        a.unit_id,
        CASE s.metric_type
            WHEN 'binary_rate' THEN CASE WHEN coalesce(e.event_count, 0) > 0 THEN 1.0 ELSE 0.0 END
            WHEN 'mean_value'  THEN coalesce(e.value_total, 0.0)
        END AS unit_value
    FROM metric_spec s
    CROSS JOIN assignments a
    LEFT JOIN event_totals e
           ON e.unit_id    = a.unit_id
          AND e.event_type = s.event_type
    WHERE s.mode = 'precomputed'
),
elapsed AS (
    SELECT
        s.metric_name,
        s.horizon_name,
        a.variant,
        a.unit_id,
        CASE s.metric_type
            WHEN 'binary_rate' THEN CASE WHEN count(e.unit_id) > 0 THEN 1.0 ELSE 0.0 END
            WHEN 'mean_value'  THEN coalesce(sum(e.value), 0.0)
        END AS unit_value
    FROM metric_spec s
    CROSS JOIN assignments a
    LEFT JOIN events e
           ON e.unit_id    = a.unit_id
          AND e.event_type = s.event_type
          AND e.event_at    IS NOT NULL
          AND a.assigned_at IS NOT NULL
          AND e.event_at   >= a.assigned_at
          AND e.event_at   <= a.assigned_at + to_days(CAST(s.window_days AS INTEGER))
    WHERE s.mode = 'elapsed'
    GROUP BY s.metric_name, s.horizon_name, s.metric_type, a.variant, a.unit_id
)
SELECT * FROM precomputed
UNION ALL
SELECT * FROM elapsed;

-- Per-arm aggregates. var_samp over the unit-level values is what feeds the analytic
-- standard error; the bootstrap in analysis/bootstrap.py resamples the same unit values
-- rather than trusting this variance, and the memo reports both so they can disagree
-- visibly if the normal approximation is doing any work.
CREATE OR REPLACE TABLE metric_by_variant AS
SELECT
    metric_name,
    horizon_name,
    variant,
    count(*)            AS n_units,
    sum(unit_value)     AS total_value,
    avg(unit_value)     AS metric_value,
    var_samp(unit_value) AS unit_variance
FROM metric_unit_values
GROUP BY metric_name, horizon_name, variant;

-- Effects against the control arm.
--
-- The control arm's value is pulled across rows with a window function rather than a
-- self-join, so each (metric, horizon) partition carries its own baseline and the query
-- generalises to more than two arms without becoming a chain of joins.
CREATE OR REPLACE VIEW metric_effects AS
WITH labelled AS (
    SELECT
        m.*,
        v.is_control,
        s.metric_label,
        s.horizon_label,
        s.role,
        s.metric_type,
        s.direction
    FROM metric_by_variant m
    JOIN config_variants v USING (variant)
    JOIN (SELECT DISTINCT metric_name, horizon_name, metric_label, horizon_label,
                 role, metric_type, direction FROM metric_spec) s
      USING (metric_name, horizon_name)
),
against_control AS (
    SELECT
        *,
        max(CASE WHEN is_control THEN metric_value  END)
            OVER (PARTITION BY metric_name, horizon_name) AS control_value,
        max(CASE WHEN is_control THEN n_units       END)
            OVER (PARTITION BY metric_name, horizon_name) AS control_n,
        max(CASE WHEN is_control THEN unit_variance END)
            OVER (PARTITION BY metric_name, horizon_name) AS control_variance
    FROM labelled
)
SELECT
    metric_name,
    metric_label,
    role,
    metric_type,
    direction,
    horizon_name,
    horizon_label,
    variant,
    n_units,
    metric_value,
    unit_variance,
    control_n,
    control_value,
    control_variance,
    metric_value - control_value                              AS absolute_effect,
    (metric_value - control_value) / nullif(control_value, 0) AS relative_effect,
    sqrt(  unit_variance    / nullif(n_units, 0)
         + control_variance / nullif(control_n, 0))           AS se_absolute
FROM against_control
WHERE NOT is_control;
