"""Heterogeneous treatment effects and CUPED (SPEC.md §7.8).

Aggregation is in sql/03_segments.sql. This module applies the multiple-comparison
correction and turns the CUPED variance numbers into the before/after comparison the memo
reports.

Two disciplines that matter more than the arithmetic:

**Segmentation only touches pre-treatment covariates.** Not by convention here but by
construction: the SQL reads the `covariates` table, which adapters populate only from
columns they have explicitly declared pre-treatment. There is no path from this module to a
column in `events`.

**Every segment tested gets counted.** Twelve covariates cut into quartiles is 48
comparisons, and at alpha = 0.05 roughly two of them come back "significant" on pure noise.
Reporting the most interesting one without saying how many were examined is how
subgroup analyses generate findings that never replicate. Benjamini-Hochberg is applied
across the whole family and both the raw and adjusted p-values are reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
from scipy import stats

from .config import ExperimentConfig
from .contract import materialise_config_variants

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

FDR_ALPHA = 0.05


@dataclass(frozen=True)
class SegmentEffect:
    covariate: str
    quartile: int
    n_treatment: int
    n_control: int
    control_value: float
    treatment_value: float
    absolute_effect: float
    relative_effect: float
    p_value: float
    p_adjusted: float

    @property
    def survives_correction(self) -> bool:
        return self.p_adjusted < FDR_ALPHA


@dataclass(frozen=True)
class CupedResult:
    covariate: str
    theta: float
    correlation: float
    effect_raw: float
    effect_cuped: float
    se_raw: float
    se_cuped: float

    @property
    def variance_reduction(self) -> float:
        """Reduction in the variance of the estimator, which is what actually helps."""
        if not self.se_raw:
            return 0.0
        return 1.0 - (self.se_cuped / self.se_raw) ** 2

    @property
    def theoretical_reduction(self) -> float:
        """CUPED's ceiling with one covariate is corr(X, Y) squared."""
        return self.correlation**2

    @property
    def ci_width_raw(self) -> float:
        return 2 * 1.959963985 * self.se_raw

    @property
    def ci_width_cuped(self) -> float:
        return 2 * 1.959963985 * self.se_cuped

    @property
    def effect_shift(self) -> float:
        """How far CUPED moved the point estimate.

        Near zero when the covariate is balanced across arms. When it is not, CUPED
        *corrects* the chance imbalance and this is non-zero by design, equal to
        -theta x (mean_X[treatment] - mean_X[control]). See docs/decisions.md D-14.
        """
        return self.effect_cuped - self.effect_raw

    @property
    def shift_share(self) -> float:
        """The shift as a fraction of the raw effect. Above ~5% is worth explaining."""
        if not self.effect_raw:
            return 0.0
        return abs(self.effect_shift / self.effect_raw)

    @property
    def shift_is_material(self) -> bool:
        return self.shift_share >= 0.05


@dataclass(frozen=True)
class SegmentationReport:
    covariates: tuple[str, ...]
    effects: tuple[SegmentEffect, ...]
    cuped: tuple[CupedResult, ...]

    @property
    def n_tests(self) -> int:
        return len(self.effects)

    @property
    def surviving(self) -> tuple[SegmentEffect, ...]:
        return tuple(e for e in self.effects if e.survives_correction)

    @property
    def nominally_significant(self) -> tuple[SegmentEffect, ...]:
        return tuple(e for e in self.effects if e.p_value < 0.05)

    @property
    def best_cuped(self) -> CupedResult | None:
        return max(self.cuped, key=lambda c: c.variance_reduction, default=None)

    @property
    def expected_false_positives(self) -> float:
        """How many 'significant' segments pure noise would have produced."""
        return 0.05 * self.n_tests


def _adjust(p_values: list[float]) -> list[float]:
    if not p_values:
        return []
    return list(stats.false_discovery_control(p_values, method="bh"))


def run_segments(
    con: duckdb.DuckDBPyConnection, config: ExperimentConfig
) -> SegmentationReport | None:
    """Segment and CUPED the primary metric, or return None if impossible.

    Returns None when the dataset has no pre-treatment covariates. That is a real answer,
    not an empty result: on Cookie Cats this analysis is *declined* rather than merely
    finding nothing, and the memo says so in words (SPEC.md §5.2).
    """
    if not config.has_covariates():
        return None

    materialise_config_variants(con, config)
    con.execute((SQL_DIR / "03_segments.sql").read_text())

    rows = con.execute(
        """
        SELECT covariate_name, quartile, n_treatment, control_n,
               control_value, treatment_value, absolute_effect, relative_effect, se_absolute
        FROM segment_effects
        ORDER BY covariate_name, quartile
        """
    ).fetchall()

    raw_p = []
    for *_, absolute, _relative, se in rows:
        z = absolute / se if se else 0.0
        raw_p.append(float(2 * stats.norm.sf(abs(z))))
    adjusted = _adjust(raw_p)

    effects = tuple(
        SegmentEffect(
            covariate=r[0], quartile=int(r[1]), n_treatment=int(r[2]), n_control=int(r[3]),
            control_value=r[4], treatment_value=r[5],
            absolute_effect=r[6], relative_effect=r[7],
            p_value=p, p_adjusted=q,
        )
        for r, p, q in zip(rows, raw_p, adjusted)
    )

    cuped_rows = con.execute(
        """
        WITH per_arm AS (
            SELECT a.covariate_name, a.variant, a.n_units, a.mean_raw, a.mean_cuped,
                   a.var_raw, a.var_cuped, v.is_control
            FROM cuped_adjusted a
            JOIN config_variants v USING (variant)
        )
        SELECT
            t.covariate_name,
            t.theta,
            t.corr_xy,
            max(CASE WHEN     p.is_control THEN p.mean_raw   END) AS c_mean_raw,
            max(CASE WHEN NOT p.is_control THEN p.mean_raw   END) AS t_mean_raw,
            max(CASE WHEN     p.is_control THEN p.mean_cuped END) AS c_mean_cuped,
            max(CASE WHEN NOT p.is_control THEN p.mean_cuped END) AS t_mean_cuped,
            max(CASE WHEN     p.is_control THEN p.var_raw    END) AS c_var_raw,
            max(CASE WHEN NOT p.is_control THEN p.var_raw    END) AS t_var_raw,
            max(CASE WHEN     p.is_control THEN p.var_cuped  END) AS c_var_cuped,
            max(CASE WHEN NOT p.is_control THEN p.var_cuped  END) AS t_var_cuped,
            max(CASE WHEN     p.is_control THEN p.n_units    END) AS c_n,
            max(CASE WHEN NOT p.is_control THEN p.n_units    END) AS t_n
        FROM cuped_theta t
        JOIN per_arm p USING (covariate_name)
        GROUP BY t.covariate_name, t.theta, t.corr_xy
        ORDER BY t.covariate_name
        """
    ).fetchall()

    cuped = tuple(
        CupedResult(
            covariate=name,
            theta=theta,
            correlation=corr,
            effect_raw=t_mean_raw - c_mean_raw,
            effect_cuped=t_mean_cuped - c_mean_cuped,
            se_raw=(t_var_raw / t_n + c_var_raw / c_n) ** 0.5,
            se_cuped=(t_var_cuped / t_n + c_var_cuped / c_n) ** 0.5,
        )
        for (name, theta, corr, c_mean_raw, t_mean_raw, c_mean_cuped, t_mean_cuped,
             c_var_raw, t_var_raw, c_var_cuped, t_var_cuped, c_n, t_n) in cuped_rows
    )

    return SegmentationReport(covariates=config.covariates, effects=effects, cuped=cuped)


def render_segments(report: SegmentationReport | None) -> str:
    if report is None:
        return (
            "Segmentation and CUPED: NOT POSSIBLE on this dataset.\n"
            "  No pre-treatment covariates exist, so there is nothing safe to split on and\n"
            "  nothing to adjust with. Declined rather than approximated (SPEC.md §5.2)."
        )

    out = [
        f"Segmentation — {len(report.covariates)} pre-treatment covariates, "
        f"{report.n_tests} tests",
        "-" * 72,
        f"  nominally significant (p < 0.05):        {len(report.nominally_significant)}",
        f"  expected by chance alone at that level:  {report.expected_false_positives:.1f}",
        f"  surviving Benjamini-Hochberg at {FDR_ALPHA}:    {len(report.surviving)}",
        "",
    ]
    for e in sorted(report.surviving, key=lambda e: e.p_adjusted)[:8]:
        out.append(
            f"    {e.covariate:5s} Q{e.quartile}  {e.control_value:.4%} -> "
            f"{e.treatment_value:.4%}  rel={e.relative_effect:+.2%}  q={e.p_adjusted:.2e}"
        )

    best = report.best_cuped
    if best:
        out += [
            "",
            "CUPED — variance reduction on the primary metric",
            "-" * 72,
            f"  best covariate            {best.covariate}  (corr with outcome "
            f"{best.correlation:+.4f}, theta {best.theta:+.6f})",
            f"  standard error            {best.se_raw:.6f} -> {best.se_cuped:.6f}",
            f"  variance reduction        {best.variance_reduction:.2%} "
            f"(theoretical ceiling corr^2 = {best.theoretical_reduction:.2%})",
            f"  95% CI width              {best.ci_width_raw:.6f} -> {best.ci_width_cuped:.6f}",
            f"  effect estimate           {best.effect_raw:+.6f} -> {best.effect_cuped:+.6f}"
            f"  (moved {best.effect_shift:+.2e})",
        ]
    return "\n".join(out)
