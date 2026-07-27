"""Compute metrics across horizons (SPEC.md §7.5).

SQL aggregates (sql/02_metrics.sql); this module materialises the config into the table
that SQL reads, then attaches bootstrap intervals and significance tests to the aggregated
rows. No aggregation happens in Python — the arrays this module pulls out for the bootstrap
are unit-level values, not summaries (docs/decisions.md D-02).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
from scipy import stats

from analysis.bootstrap import BootstrapInterval, bootstrap_difference

from .config import ExperimentConfig, Metric
from .contract import materialise_config_variants

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


@dataclass(frozen=True)
class HorizonResult:
    metric_name: str
    metric_label: str
    role: str
    metric_type: str
    direction: str
    horizon_name: str
    horizon_label: str
    variant: str
    control: str
    n_control: int
    n_treatment: int
    control_value: float
    treatment_value: float
    absolute_effect: float
    relative_effect: float
    se_absolute: float
    p_value: float
    ci_absolute: BootstrapInterval
    ci_relative: BootstrapInterval

    @property
    def is_significant(self) -> bool:
        return self.p_value < 0.05

    @property
    def is_primary(self) -> bool:
        return self.role == "primary"

    @property
    def moved_against_treatment(self) -> bool:
        """True when the treatment moved the metric the way nobody wanted."""
        return (self.absolute_effect < 0) if self.direction == "increase" else (self.absolute_effect > 0)


def _materialise_metric_spec(con: duckdb.DuckDBPyConnection, config: ExperimentConfig) -> None:
    con.execute("DROP TABLE IF EXISTS metric_spec")
    con.execute(
        "CREATE TABLE metric_spec ("
        " metric_name VARCHAR, metric_label VARCHAR, role VARCHAR, metric_type VARCHAR,"
        " direction VARCHAR, horizon_name VARCHAR, horizon_label VARCHAR,"
        " event_type VARCHAR, mode VARCHAR, window_days DOUBLE)"
    )
    con.executemany(
        "INSERT INTO metric_spec VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (m.name, m.label, m.role, m.type, m.direction,
             h.name, h.label, h.event_type, h.mode, h.window_days)
            for m in config.metrics
            for h in m.horizons
        ],
    )


def _unit_values(con, metric_name: str, horizon_name: str, variant: str) -> np.ndarray:
    return con.execute(
        "SELECT unit_value FROM metric_unit_values"
        " WHERE metric_name = ? AND horizon_name = ? AND variant = ?",
        [metric_name, horizon_name, variant],
    ).fetchnumpy()["unit_value"]


def compute_metrics(
    con: duckdb.DuckDBPyConnection,
    config: ExperimentConfig,
    *,
    resamples: int = 10_000,
) -> tuple[HorizonResult, ...]:
    """Every metric at every configured horizon, with intervals.

    Only ever called after the gate has permitted estimates — see readout/run.py.
    """
    materialise_config_variants(con, config)
    _materialise_metric_spec(con, config)
    con.execute((SQL_DIR / "02_metrics.sql").read_text())

    rows = con.execute(
        """
        SELECT metric_name, metric_label, role, metric_type, direction,
               horizon_name, horizon_label, variant,
               n_units, metric_value, control_n, control_value,
               absolute_effect, relative_effect, se_absolute
        FROM metric_effects
        ORDER BY role DESC, metric_name, horizon_name, variant
        """
    ).fetchall()

    results = []
    for (metric_name, metric_label, role, metric_type, direction, horizon_name,
         horizon_label, variant, n_t, value_t, n_c, value_c,
         absolute, relative, se) in rows:

        z = absolute / se if se else 0.0
        p_value = float(2 * stats.norm.sf(abs(z)))

        if metric_type == "binary_rate":
            # Closed-form bootstrap: no need to pull 14M unit values out of the database.
            kwargs = dict(
                control_values=None, treatment_values=None,
                control_summary=(int(round(value_c * n_c)), int(n_c)),
                treatment_summary=(int(round(value_t * n_t)), int(n_t)),
                binary=True,
            )
        else:
            kwargs = dict(
                control_values=_unit_values(con, metric_name, horizon_name, config.control),
                treatment_values=_unit_values(con, metric_name, horizon_name, variant),
            )

        results.append(HorizonResult(
            metric_name=metric_name, metric_label=metric_label, role=role,
            metric_type=metric_type, direction=direction,
            horizon_name=horizon_name, horizon_label=horizon_label,
            variant=variant, control=config.control,
            n_control=int(n_c), n_treatment=int(n_t),
            control_value=value_c, treatment_value=value_t,
            absolute_effect=absolute, relative_effect=relative,
            se_absolute=se, p_value=p_value,
            ci_absolute=bootstrap_difference(**kwargs, resamples=resamples, relative=False),
            ci_relative=bootstrap_difference(**kwargs, resamples=resamples, relative=True),
        ))

    return tuple(results)


def primary_results(results, config: ExperimentConfig) -> tuple[HorizonResult, ...]:
    return tuple(r for r in results if r.metric_name == config.primary_metric.name)


def horizon_contrast(results, metric: Metric) -> str:
    """Describe how the decision changes across horizons — the project's headline (§7.5).

    Deliberately distinguishes a change of *sign* from a change of *detectability*.
    Conflating them is the error docs/decisions.md D-07 exists to prevent: on Cookie Cats
    both horizons point the same way, and only one of them is readable as a result.
    """
    rows = [r for r in results if r.metric_name == metric.name]
    if len(rows) < 2:
        return ""

    signs = {np.sign(r.absolute_effect) for r in rows}
    significant = [r for r in rows if r.is_significant]

    if len(signs) > 1:
        return (
            "The horizons disagree in direction: "
            + "; ".join(f"{r.horizon_label} {r.relative_effect:+.2%}" for r in rows)
            + ". The same experiment supports opposite conclusions depending on when it is read."
        )
    if significant and len(significant) < len(rows):
        readable = ", ".join(r.horizon_label for r in significant)
        quiet = ", ".join(r.horizon_label for r in rows if not r.is_significant)
        return (
            f"Every horizon points the same way, but only {readable} is distinguishable from "
            f"noise; {quiet} is not. The point estimates agree — what changes with the horizon "
            "is whether the effect is detectable at all, and therefore what a team reading "
            "that horizon would decide."
        )
    return "The horizons agree in both direction and significance."
