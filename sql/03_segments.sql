-- Heterogeneous treatment effects on PRE-TREATMENT covariates only (SPEC.md §7.8).
--
-- The constraint in the title is the whole point of this file. Splitting results by
-- anything the treatment could have influenced conditions on an outcome and breaks the
-- comparability randomisation bought (SPEC.md §4, collider bias). So segments are cut
-- exclusively from the `covariates` table, which an adapter populates only from columns it
-- has explicitly declared pre-treatment. There is no code path here that can reach a
-- column from `events`.
--
-- Cookie Cats produces nothing from this file, and that is correct rather than a gap: it
-- has no pre-treatment covariates at all, so `covariates` is empty and every view below
-- returns zero rows. The declined analysis is documented in the memo instead.
--
-- Depends on config_variants, metric_spec, and metric_unit_values from the earlier files.

-- Segmentation and CUPED run on the PRIMARY metric only, and this table is what enforces
-- it. Without the restriction the join below is (metrics x units) x covariates, which on
-- Criteo is 3 x 14M x 12 = roughly half a billion rows to answer a question nobody asked
-- about guardrails. Heterogeneous effects are a question about the metric the decision
-- turns on.
CREATE OR REPLACE TABLE primary_unit_values AS
SELECT m.*
FROM metric_unit_values m
WHERE m.metric_name IN (SELECT DISTINCT metric_name FROM metric_spec WHERE role = 'primary');

-- Quartile membership per unit per covariate.
--
-- ntile() over the whole population rather than within each arm, deliberately. Ranking
-- within arm would define the cut points using arm membership, so "top quartile" would
-- mean a different covariate range in treatment than in control and the two would no
-- longer be comparable. One global cut keeps the segments identical across arms.
CREATE OR REPLACE TABLE segment_membership AS
SELECT
    c.unit_id,
    c.covariate_name,
    c.value,
    ntile(4) OVER (PARTITION BY c.covariate_name ORDER BY c.value) AS quartile
FROM covariates c;

-- Effect within each segment, against that segment's own control arm.
--
-- The baseline is the control units *inside the same segment*, not the global control
-- rate. Comparing a segment's treated units to the overall control average would confound
-- the treatment effect with whatever makes that segment different in the first place.
CREATE OR REPLACE VIEW segment_effects AS
WITH unit_segments AS (
    SELECT
        m.metric_name,
        m.horizon_name,
        m.variant,
        m.unit_id,
        m.unit_value,
        s.covariate_name,
        s.quartile
    FROM primary_unit_values m
    JOIN segment_membership s USING (unit_id)
),
per_arm AS (
    SELECT
        metric_name,
        horizon_name,
        covariate_name,
        quartile,
        variant,
        count(*)             AS n_units,
        avg(unit_value)      AS metric_value,
        var_samp(unit_value) AS unit_variance
    FROM unit_segments
    GROUP BY metric_name, horizon_name, covariate_name, quartile, variant
),
against_control AS (
    SELECT
        p.*,
        v.is_control,
        max(CASE WHEN v.is_control THEN p.metric_value  END)
            OVER (PARTITION BY p.metric_name, p.horizon_name, p.covariate_name, p.quartile)
            AS control_value,
        max(CASE WHEN v.is_control THEN p.n_units       END)
            OVER (PARTITION BY p.metric_name, p.horizon_name, p.covariate_name, p.quartile)
            AS control_n,
        max(CASE WHEN v.is_control THEN p.unit_variance END)
            OVER (PARTITION BY p.metric_name, p.horizon_name, p.covariate_name, p.quartile)
            AS control_variance
    FROM per_arm p
    JOIN config_variants v USING (variant)
)
SELECT
    metric_name,
    horizon_name,
    covariate_name,
    quartile,
    variant,
    n_units                                                   AS n_treatment,
    control_n,
    metric_value                                              AS treatment_value,
    control_value,
    metric_value - control_value                              AS absolute_effect,
    (metric_value - control_value) / nullif(control_value, 0)  AS relative_effect,
    sqrt(  unit_variance    / nullif(n_units, 0)
         + control_variance / nullif(control_n, 0))            AS se_absolute
FROM against_control
WHERE NOT is_control
ORDER BY metric_name, horizon_name, covariate_name, quartile;

-- CUPED inputs (SPEC.md §7.8).
--
-- CUPED adjusts each unit's outcome by its pre-experiment covariate:
--
--     Y_adjusted = Y - theta * (X - mean(X)),  theta = cov(X, Y) / var(X)
--
-- Because X is pre-treatment, subtracting it removes variance that has nothing to do with
-- the treatment, without moving the expected difference between arms. The result is a
-- tighter interval on the same effect, which is the honest version of "more sensitive" —
-- it does not manufacture significance, it removes noise that was never informative.
--
-- theta is estimated on the POOLED sample across both arms. Estimating it per arm would
-- let the adjustment itself differ by arm and could shift the effect estimate, which is
-- exactly what CUPED is supposed not to do.
CREATE OR REPLACE VIEW cuped_theta AS
WITH joined AS (
    SELECT
        m.metric_name,
        m.horizon_name,
        c.covariate_name,
        m.unit_id,
        m.variant,
        m.unit_value AS y,
        c.value      AS x
    FROM primary_unit_values m
    JOIN covariates c USING (unit_id)
)
SELECT
    metric_name,
    horizon_name,
    covariate_name,
    count(*)                        AS n_units,
    avg(x)                          AS mean_x,
    var_samp(y)                     AS var_y_raw,
    covar_samp(y, x)                AS cov_xy,
    var_samp(x)                     AS var_x,
    covar_samp(y, x) / nullif(var_samp(x), 0) AS theta,
    corr(y, x)                      AS corr_xy
FROM joined
GROUP BY metric_name, horizon_name, covariate_name;

-- Variance before and after adjustment, per arm, using the pooled theta.
--
-- The variance reduction reported in the memo is the drop in the variance of the
-- *estimator*, computed from these per-arm variances. Reporting the drop in raw outcome
-- variance instead would overstate the benefit.
CREATE OR REPLACE VIEW cuped_adjusted AS
WITH params AS (
    SELECT metric_name, horizon_name, covariate_name, theta, mean_x FROM cuped_theta
),
adjusted AS (
    SELECT
        m.metric_name,
        m.horizon_name,
        p.covariate_name,
        m.variant,
        m.unit_value                                        AS y,
        m.unit_value - p.theta * (c.value - p.mean_x)        AS y_cuped
    FROM primary_unit_values m
    JOIN covariates c USING (unit_id)
    JOIN params p
      ON p.metric_name  = m.metric_name
     AND p.horizon_name = m.horizon_name
     AND p.covariate_name = c.covariate_name
)
SELECT
    metric_name,
    horizon_name,
    covariate_name,
    variant,
    count(*)             AS n_units,
    avg(y)               AS mean_raw,
    avg(y_cuped)         AS mean_cuped,
    var_samp(y)          AS var_raw,
    var_samp(y_cuped)    AS var_cuped
FROM adjusted
GROUP BY metric_name, horizon_name, covariate_name, variant;
