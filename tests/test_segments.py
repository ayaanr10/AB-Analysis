"""Segmentation, CUPED, and the HTML surface (SPEC.md §7.8, §7.9).

The synthetic fixture here carries a genuine pre-treatment covariate, which the two real
datasets cannot both provide: Cookie Cats has none at all, and Criteo's are anonymised, so
neither lets a test assert what CUPED *should* produce. With a covariate whose correlation
to the outcome is known by construction, the variance reduction has a right answer.
"""

from __future__ import annotations

import csv

import duckdb
import numpy as np
import pytest

from adapters.base import SOURCE_VIEW, Adapter, get_adapter, register, sql_literal
from readout.config import ExperimentConfig, Horizon, Metric
from readout.contract import load
from readout.html import render_html
from readout.metrics import compute_metrics
from readout.run import build_readout
from readout.segments import run_segments

from .conftest import fixture_config, write_fixture


@register
class CovariateAdapter(Adapter):
    """Fixture with one declared pre-treatment covariate."""

    name = "covariate_fixture"

    def register_source(self, con, path):
        con.execute(f"CREATE OR REPLACE VIEW {SOURCE_VIEW} AS "
                    f"SELECT * FROM read_csv_auto({sql_literal(path)}, header=true)")

    def assignments_sql(self):
        return f"""SELECT CAST(unit_id AS VARCHAR) AS unit_id, variant AS variant,
                          CAST(NULL AS TIMESTAMP) AS assigned_at FROM {SOURCE_VIEW}"""

    def events_sql(self):
        return f"""SELECT CAST(unit_id AS VARCHAR) AS unit_id, 'convert' AS event_type,
                          CAST(NULL AS TIMESTAMP) AS event_at, CAST(NULL AS DOUBLE) AS value
                   FROM {SOURCE_VIEW} WHERE converted
                   UNION ALL
                   SELECT CAST(unit_id AS VARCHAR), 'impression', CAST(NULL AS TIMESTAMP),
                          CAST(NULL AS DOUBLE) FROM {SOURCE_VIEW}"""

    def covariates_sql(self):
        return f"""SELECT CAST(unit_id AS VARCHAR) AS unit_id,
                          'prior_activity'        AS covariate_name,
                          CAST(prior AS DOUBLE)   AS value
                   FROM {SOURCE_VIEW}"""

    @property
    def mapping_notes(self):
        return "fixture with a pre-treatment covariate"


def write_covariate_fixture(path, n=8_000, lift=0.03, imbalance=0.0, seed=11):
    """Outcome is driven partly by the covariate, so CUPED has real work to do.

    `imbalance` shifts the treatment arm's covariate mean, simulating a randomisation that
    happened to hand one arm better users. That is what CUPED corrects, and what
    test_cuped_shift_equals_the_covariate_imbalance checks.
    """
    rng = np.random.default_rng(seed)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["unit_id", "variant", "prior", "converted"])
        for i in range(n * 2):
            variant = "control" if i % 2 == 0 else "treatment"
            prior = float(rng.normal(10 + (imbalance if variant == "treatment" else 0.0), 3))
            # Baseline rate rises with the covariate: correlation by construction.
            p = min(max(0.02 + 0.02 * (prior - 10) / 3, 0.001), 0.95)
            if variant == "treatment":
                p = min(p + lift, 0.98)
            w.writerow([f"u{i}", variant, f"{prior:.6f}", rng.random() < p])
    return path


def covariate_config(path):
    return ExperimentConfig(
        name="covariate_fixture", description="fixture with a covariate",
        adapter="covariate_fixture", source_path=str(path),
        variants=("control", "treatment"), control="control",
        intended_split={"control": 0.5, "treatment": 0.5}, intended_split_source="stated",
        metrics=(Metric(name="conversion", label="Conversion", role="primary",
                        type="binary_rate",
                        horizons=(Horizon(name="overall", label="Conversion",
                                          event_type="convert", mode="precomputed"),)),),
        covariates=("prior_activity",),
        practical_effect=0.05,
    )


@pytest.fixture
def covariate_setup(tmp_path):
    path = write_covariate_fixture(tmp_path / "cov.csv")
    config = covariate_config(path)
    con = duckdb.connect(":memory:")
    load(con, get_adapter("covariate_fixture"), config)
    compute_metrics(con, config, resamples=200)
    return con, config


# --- segmentation ---------------------------------------------------------------------

def test_no_covariates_returns_none_not_empty(tmp_path):
    """Declining an analysis and finding nothing are different answers."""
    path = write_fixture(tmp_path / "f.csv")
    config = fixture_config(source_path=str(path))
    con = duckdb.connect(":memory:")
    load(con, get_adapter("fixture"), config)
    compute_metrics(con, config, resamples=100)
    assert run_segments(con, config) is None


def test_segments_cover_every_quartile(covariate_setup):
    con, config = covariate_setup
    report = run_segments(con, config)
    assert report is not None
    assert {e.quartile for e in report.effects} == {1, 2, 3, 4}
    assert report.n_tests == 4  # one covariate x four quartiles


def test_multiple_comparison_correction_is_applied(covariate_setup):
    con, config = covariate_setup
    report = run_segments(con, config)
    assert all(e.p_adjusted >= e.p_value - 1e-12 for e in report.effects), \
        "BH-adjusted p-values must never be smaller than raw ones"
    assert report.expected_false_positives == pytest.approx(0.05 * report.n_tests)


def test_quartile_cuts_are_global_not_per_arm(covariate_setup):
    """Both arms must see the same covariate range in a given quartile.

    Ranking within arm would make "top quartile" mean different things in each arm, and the
    comparison would no longer be like for like.
    """
    con, config = covariate_setup
    run_segments(con, config)  # creates segment_membership
    rows = con.execute(
        """
        SELECT s.quartile, min(s.value), max(s.value)
        FROM segment_membership s GROUP BY s.quartile ORDER BY s.quartile
        """
    ).fetchall()
    for (_, _, upper), (_, lower_next, _) in zip(rows, rows[1:]):
        assert upper <= lower_next, "quartile ranges must not overlap"


# --- CUPED ----------------------------------------------------------------------------

def test_cuped_reduces_variance(covariate_setup):
    con, config = covariate_setup
    report = run_segments(con, config)
    best = report.best_cuped
    assert best is not None
    assert best.se_cuped < best.se_raw
    assert best.variance_reduction > 0


def test_cuped_reduction_tracks_the_theoretical_ceiling(covariate_setup):
    """With one covariate the ceiling is corr squared. Landing far off means a bug."""
    con, config = covariate_setup
    best = run_segments(con, config).best_cuped
    assert best.variance_reduction == pytest.approx(best.theoretical_reduction, abs=0.02)


def test_cuped_barely_moves_the_estimate_when_the_covariate_is_balanced(covariate_setup):
    """With a balanced covariate there is nothing to correct, so the estimate holds still.

    The fixture assigns arms by row parity, so the covariate is balanced by construction.
    """
    con, config = covariate_setup
    best = run_segments(con, config).best_cuped
    assert abs(best.effect_shift) < 0.002, (
        f"CUPED shifted the effect by {best.effect_shift:.5f} on a balanced covariate; "
        "with nothing to correct it should only shrink the interval"
    )


def test_cuped_shift_equals_the_covariate_imbalance(tmp_path):
    """When a covariate IS imbalanced, CUPED must shift the estimate by exactly -theta*delta.

    This is the assertion that matters, and it is the one the Criteo run forced (see
    docs/decisions.md D-14). Asserting "CUPED never moves the estimate" would be wrong:
    CUPED corrects chance covariate imbalance, and on Criteo that accounted for a quarter of
    the headline effect. What must hold is that the movement is fully explained.
    """
    path = write_covariate_fixture(tmp_path / "imb.csv", n=6_000, imbalance=1.5, seed=3)
    config = covariate_config(path)
    con = duckdb.connect(":memory:")
    load(con, get_adapter("covariate_fixture"), config)
    compute_metrics(con, config, resamples=100)
    best = run_segments(con, config).best_cuped

    means = con.execute(
        """
        SELECT avg(CASE WHEN a.variant = 'treatment' THEN c.value END),
               avg(CASE WHEN a.variant = 'control'   THEN c.value END)
        FROM covariates c JOIN assignments a USING (unit_id)
        """
    ).fetchone()
    predicted = -best.theta * (means[0] - means[1])

    assert abs(means[0] - means[1]) > 0.5, "fixture should be genuinely imbalanced"
    assert abs(best.effect_shift) > 1e-4, "an imbalanced covariate must move the estimate"
    assert best.effect_shift == pytest.approx(predicted, abs=1e-9), (
        f"CUPED moved the estimate by {best.effect_shift:.6e} but the covariate imbalance "
        f"predicts {predicted:.6e}; the shift is not fully explained"
    )


# --- HTML surface ---------------------------------------------------------------------

def test_html_renders_and_is_self_contained(covariate_setup):
    con, config = covariate_setup
    readout = build_readout(con, config, resamples=200)
    page = render_html(readout)
    assert page.startswith("<!doctype html>")
    assert "<table" in page
    for external in ("http://", "https://", "src="):
        assert external not in page, f"scorecard must not reference {external}"


def test_html_includes_a_horizon_control(covariate_setup):
    con, config = covariate_setup
    readout = build_readout(con, config, resamples=200)
    assert 'data-horizon-filter="*"' in render_html(readout)


def test_html_escapes_content(covariate_setup):
    """Config text reaches the page, so it has to be escaped."""
    con, config = covariate_setup
    hostile = ExperimentConfig(**{**config.__dict__,
                                 "description": '<script>alert("x")</script>'})
    readout = build_readout(con, hostile, resamples=200)
    page = render_html(readout)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
