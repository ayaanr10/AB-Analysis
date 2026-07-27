"""Metric computation, including the horizon logic (SPEC.md §7.5).

The elapsed-horizon path deserves particular attention: neither Cookie Cats nor Criteo
exercises it (both ship precomputed outcomes), so without a test it would be code that
looks supported and has never once run.
"""

from __future__ import annotations

import csv

import duckdb
import numpy as np
import pytest

from adapters.base import SOURCE_VIEW, Adapter, get_adapter, register, sql_literal
from analysis.bootstrap import bootstrap_difference
from analysis.power import minimum_detectable_effect, required_sample_size
from readout.config import ExperimentConfig, Horizon, Metric
from readout.contract import load
from readout.metrics import compute_metrics

from .conftest import fixture_config, write_fixture


def load_fixture(tmp_path, **kwargs):
    path = write_fixture(tmp_path / "f.csv", **kwargs)
    config = fixture_config(source_path=str(path))
    con = duckdb.connect(":memory:")
    load(con, get_adapter("fixture"), config)
    return con, config


# --- the denominator is the thing most easily got wrong -------------------------------

def test_units_with_no_events_stay_in_the_denominator(tmp_path):
    """A binary rate is over assigned units, not over units that did something.

    Dropping the zeros silently redefines "conversion rate" as "conversion rate among
    converters", which is 100%.
    """
    con, config = load_fixture(tmp_path, n_control=1_000, n_treatment=1_000,
                               control_rate=0.10, treatment_rate=0.20)
    results = compute_metrics(con, config, resamples=200)
    r = results[0]
    assert r.n_control == 1_000 and r.n_treatment == 1_000
    assert 0.05 < r.control_value < 0.15
    assert 0.15 < r.treatment_value < 0.25


def test_effect_matches_a_hand_computation(tmp_path):
    con, config = load_fixture(tmp_path, n_control=2_000, n_treatment=2_000)
    r = compute_metrics(con, config, resamples=200)[0]

    control = con.execute(
        "SELECT avg(CASE WHEN c > 0 THEN 1.0 ELSE 0.0 END) FROM ("
        " SELECT a.unit_id, count(e.unit_id) c FROM assignments a"
        " LEFT JOIN events e ON e.unit_id = a.unit_id AND e.event_type = 'convert'"
        " WHERE a.variant = 'control' GROUP BY a.unit_id)"
    ).fetchone()[0]
    assert r.control_value == pytest.approx(control)
    assert r.absolute_effect == pytest.approx(r.treatment_value - r.control_value)
    assert r.relative_effect == pytest.approx(r.absolute_effect / r.control_value)


def test_a_real_lift_is_detected(tmp_path):
    con, config = load_fixture(tmp_path, n_control=20_000, n_treatment=20_000,
                               control_rate=0.10, treatment_rate=0.13)
    r = compute_metrics(con, config, resamples=2_000)[0]
    assert r.is_significant
    assert r.ci_absolute.excludes_zero
    assert r.ci_absolute.low < r.absolute_effect < r.ci_absolute.high


def test_no_lift_is_not_detected(tmp_path):
    con, config = load_fixture(tmp_path, n_control=20_000, n_treatment=20_000,
                               control_rate=0.10, treatment_rate=0.10)
    r = compute_metrics(con, config, resamples=2_000)[0]
    assert not r.is_significant
    assert not r.ci_absolute.excludes_zero


# --- elapsed horizons: the untested-by-real-data path ---------------------------------

@register
class TimestampedAdapter(Adapter):
    """Fixture with real timestamps, so `mode: elapsed` is exercised somewhere."""

    name = "timestamped"

    def register_source(self, con, path):
        con.execute(f"CREATE OR REPLACE VIEW {SOURCE_VIEW} AS "
                    f"SELECT * FROM read_csv_auto({sql_literal(path)}, header=true)")

    def assignments_sql(self):
        return f"""SELECT CAST(unit_id AS VARCHAR) AS unit_id, variant AS variant,
                          CAST(assigned_at AS TIMESTAMP) AS assigned_at
                   FROM {SOURCE_VIEW} GROUP BY 1, 2, 3"""

    def events_sql(self):
        return f"""SELECT CAST(unit_id AS VARCHAR) AS unit_id, 'act' AS event_type,
                          CAST(event_at AS TIMESTAMP) AS event_at, CAST(NULL AS DOUBLE) AS value
                   FROM {SOURCE_VIEW} WHERE event_at IS NOT NULL"""

    @property
    def mapping_notes(self):
        return "timestamped fixture"


def timestamped_config(path, window_days):
    return ExperimentConfig(
        name="timestamped", description="", adapter="timestamped", source_path=str(path),
        variants=("control", "treatment"), control="control",
        intended_split={"control": 0.5, "treatment": 0.5}, intended_split_source="stated",
        metrics=(Metric(name="act", label="Acted", role="primary", type="binary_rate",
                        horizons=(Horizon(name=f"d{window_days}", label=f"{window_days}d",
                                          event_type="act", mode="elapsed",
                                          window_days=window_days),)),),
    )


def test_elapsed_horizon_counts_only_events_inside_the_window(tmp_path):
    """Day 1 sees the fast returner; day 7 sees both. Same events, different horizon."""
    path = tmp_path / "ts.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["unit_id", "variant", "assigned_at", "event_at"])
        w.writerow(["a", "control", "2026-01-01 00:00:00", "2026-01-01 06:00:00"])  # +6h
        w.writerow(["b", "control", "2026-01-01 00:00:00", "2026-01-04 00:00:00"])  # +3d
        w.writerow(["c", "control", "2026-01-01 00:00:00", ""])                     # never
        w.writerow(["d", "treatment", "2026-01-01 00:00:00", "2026-01-01 06:00:00"])
        w.writerow(["e", "treatment", "2026-01-01 00:00:00", "2026-01-04 00:00:00"])
        w.writerow(["f", "treatment", "2026-01-01 00:00:00", ""])

    for window, expected in ((1, 1 / 3), (7, 2 / 3)):
        con = duckdb.connect(":memory:")
        config = timestamped_config(path, window)
        load(con, get_adapter("timestamped"), config)
        r = compute_metrics(con, config, resamples=100)[0]
        assert r.control_value == pytest.approx(expected), f"window={window}"
        assert r.treatment_value == pytest.approx(expected), f"window={window}"


# --- power ----------------------------------------------------------------------------

def test_mde_shrinks_as_sample_grows():
    small = minimum_detectable_effect(0.20, 1_000, 1_000)
    large = minimum_detectable_effect(0.20, 100_000, 100_000)
    assert large.mde_absolute < small.mde_absolute


def test_mde_depends_on_baseline():
    """Why power is reported per horizon rather than once per experiment."""
    high = minimum_detectable_effect(0.45, 44_700, 45_489)
    low = minimum_detectable_effect(0.19, 44_700, 45_489)
    assert low.mde_absolute < high.mde_absolute       # smaller absolute at a lower baseline
    assert low.mde_relative > high.mde_relative       # but larger *relative*


def test_practical_effect_verdict():
    assert minimum_detectable_effect(0.20, 500_000, 500_000,
                                     practical_effect=0.05).can_answer_its_own_question
    assert not minimum_detectable_effect(0.20, 100, 100,
                                         practical_effect=0.01).can_answer_its_own_question
    assert minimum_detectable_effect(0.20, 100, 100).can_answer_its_own_question is None


def test_required_sample_size_round_trips():
    n = required_sample_size(0.20, 0.05)
    achieved = minimum_detectable_effect(0.20, n, n)
    assert achieved.mde_relative == pytest.approx(0.05, rel=0.02)


def test_mde_rejects_impossible_baselines():
    with pytest.raises(ValueError):
        minimum_detectable_effect(0.0, 100, 100)
    with pytest.raises(ValueError):
        minimum_detectable_effect(0.5, 0, 100)


# --- bootstrap ------------------------------------------------------------------------

def test_binomial_shortcut_matches_index_resampling():
    """The closed form must agree with the honest slow path, or the shortcut is a bug."""
    rng = np.random.default_rng(0)
    control = (rng.random(4_000) < 0.20).astype(float)
    treatment = (rng.random(4_000) < 0.26).astype(float)

    slow = bootstrap_difference(control, treatment, resamples=4_000)
    fast = bootstrap_difference(
        None, None, binary=True, resamples=4_000,
        control_summary=(int(control.sum()), control.size),
        treatment_summary=(int(treatment.sum()), treatment.size),
    )
    assert fast.point == pytest.approx(slow.point, abs=1e-12)
    assert fast.low == pytest.approx(slow.low, abs=0.006)
    assert fast.high == pytest.approx(slow.high, abs=0.006)


def test_bootstrap_is_deterministic():
    a = bootstrap_difference(None, None, binary=True, control_summary=(200, 1000),
                             treatment_summary=(260, 1000), resamples=500)
    b = bootstrap_difference(None, None, binary=True, control_summary=(200, 1000),
                             treatment_summary=(260, 1000), resamples=500)
    assert (a.low, a.high) == (b.low, b.high)


def test_bootstrap_refuses_to_silently_degrade_on_huge_input():
    """Better to fail than to quietly reduce resamples and understate the interval."""
    huge = np.zeros(3_000_000)
    with pytest.raises(ValueError, match="index resampling refused"):
        bootstrap_difference(huge, huge, resamples=10)
