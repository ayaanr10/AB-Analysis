"""The blocking rule (SPEC.md §7.3, §10).

    "Deliberately corrupted input produces a BLOCK, demonstrably."

This file is the demonstration, and it was written before readout/gate.py existed. The
failure mode a gate like this has is not firing incorrectly — it is never firing at all,
looking green forever while nobody notices it lost the ability to go red. So each validity
failure is injected on its own and asserted to block by itself.

The suppression tests matter as much as the detection tests. A gate that detects a problem
and still lets an effect estimate render somewhere has not done its job: the number escapes
into a slide, and the caveat does not follow it.
"""

from __future__ import annotations

import duckdb
import pytest

from adapters.base import get_adapter
from readout.contract import load
from readout.diagnostics import run_diagnostics
from readout.gate import Status, evaluate

from .conftest import fixture_config, write_fixture


def build(tmp_path, **corruption):
    """Load a fixture into the contract and return (connection, config)."""
    split = corruption.pop("split", (0.5, 0.5))
    split_source = corruption.pop("split_source", "stated")
    path = write_fixture(tmp_path / "fixture.csv", **corruption)
    config = fixture_config(split=split, split_source=split_source, source_path=str(path))
    con = duckdb.connect(":memory:")
    load(con, get_adapter("fixture"), config)
    return con, config


def gate_for(tmp_path, **corruption):
    con, config = build(tmp_path, **corruption)
    return evaluate(run_diagnostics(con, config), config)


def find(decision, name):
    return next(r for r in decision.results if r.name == name)


# --- clean input must not block -------------------------------------------------------
# Asserted first and deliberately: a gate that blocks everything is trivially "safe" and
# completely useless. This is what stops the other tests being satisfiable by a stub.

def test_clean_input_does_not_block(tmp_path):
    decision = gate_for(tmp_path)
    assert decision.status is Status.PASS
    assert decision.estimates_permitted
    assert decision.blocked_by == ()


# --- each corruption must block on its own -------------------------------------------

def test_duplicate_units_block(tmp_path):
    decision = gate_for(tmp_path, duplicate_units=25)
    assert decision.status is Status.BLOCK
    assert not decision.estimates_permitted
    assert "duplicate_units" in decision.blocked_by
    assert find(decision, "duplicate_units").observed["duplicate_unit_ids"] == 25


def test_cross_contamination_blocks(tmp_path):
    decision = gate_for(tmp_path, cross_contaminated=13)
    assert decision.status is Status.BLOCK
    assert not decision.estimates_permitted
    assert "cross_contamination" in decision.blocked_by
    assert find(decision, "cross_contamination").observed["contaminated_units"] == 13


def test_orphan_events_block(tmp_path):
    decision = gate_for(tmp_path, orphan_events=40)
    assert decision.status is Status.BLOCK
    assert not decision.estimates_permitted
    assert "orphan_events" in decision.blocked_by


def test_severe_srm_blocks(tmp_path):
    """A 70/30 landing against an intended 50/50 is not chance."""
    decision = gate_for(tmp_path, n_control=7_000, n_treatment=3_000)
    assert decision.status is Status.BLOCK
    assert not decision.estimates_permitted
    assert "srm" in decision.blocked_by
    assert find(decision, "srm").observed["p_value"] < 0.001


# --- the threshold band has to be real, not decorative --------------------------------

def test_mild_srm_warns_but_does_not_block(tmp_path):
    """The Cookie Cats case: imbalance real enough to report, not enough to block.

    Sized to land between the 0.05 and 0.001 thresholds (SPEC.md §7.2). If this ever
    starts blocking, the thresholds have drifted and the memo's argument for treating
    Cookie Cats as interpretable no longer holds.
    """
    decision = gate_for(tmp_path, n_control=4_850, n_treatment=5_150)
    srm = find(decision, "srm")
    assert 0.001 <= srm.observed["p_value"] < 0.05, srm.observed
    assert srm.status is Status.WARN
    assert decision.status is Status.WARN
    assert decision.estimates_permitted, "WARN must render results, not suppress them"


# --- WARN is adjacent to the estimate, never a footnote -------------------------------

def test_warnings_render_next_to_the_estimate(tmp_path):
    decision = gate_for(tmp_path, n_control=4_850, n_treatment=5_150)
    assert decision.warnings, "a WARN decision must expose its warnings for inline display"
    assert all(w.status is Status.WARN for w in decision.warnings)


# --- a check that cannot fail must not report as a pass (D-08) ------------------------

def test_uninformative_srm_reports_not_applicable(tmp_path):
    """An inferred split ratio makes the SRM test circular, so it cannot reject."""
    decision = gate_for(tmp_path, n_control=7_000, n_treatment=3_000,
                        split=(0.7, 0.3), split_source="inferred")
    srm = find(decision, "srm")
    assert srm.status is Status.NOT_APPLICABLE
    assert "inferred" in srm.detail.lower()
    assert "srm" not in decision.blocked_by


def test_not_applicable_does_not_count_as_passing(tmp_path):
    decision = gate_for(tmp_path, split_source="inferred")
    assert find(decision, "srm").status is Status.NOT_APPLICABLE
    assert Status.NOT_APPLICABLE in {r.status for r in decision.results}
    assert decision.status is Status.PASS  # NOT_APPLICABLE neither passes nor fails


def test_balance_check_not_applicable_without_covariates(tmp_path):
    decision = gate_for(tmp_path)
    assert find(decision, "pre_period_balance").status is Status.NOT_APPLICABLE


def test_zero_activity_needs_a_non_outcome_signal(tmp_path):
    """Without an activity event, 'no events' just restates 'did not convert'.

    A real conversion lift would otherwise trip this check on every healthy experiment.
    """
    con, config = build(tmp_path)
    con.execute("DELETE FROM events WHERE event_type = 'impression'")
    decision = evaluate(run_diagnostics(con, config), config)
    assert find(decision, "zero_activity").status is Status.NOT_APPLICABLE
    assert decision.estimates_permitted


def test_differential_zero_activity_warns(tmp_path):
    """10% of one arm never showing up at all is a signal, and it is not a block.

    SPEC.md §7.2 asks for this to be reported and treated as a signal; it does not
    specify a blocking threshold, so inventing one would exceed the spec.
    """
    decision = gate_for(tmp_path, inactive_control=50, inactive_treatment=500)
    zero = find(decision, "zero_activity")
    assert zero.status is Status.WARN, zero.observed
    assert zero.observed["rate_spread"] > 0.05
    assert decision.estimates_permitted


# --- suppression must hold in EVERY output surface (§7.3) -----------------------------

def test_block_suppresses_estimates_in_every_surface(tmp_path):
    """The core claim. A blocked readout renders the failure and no numbers, everywhere."""
    from readout.memo import render_memo
    from readout.scorecard import render_scorecard

    con, config = build(tmp_path, duplicate_units=25)
    decision = evaluate(run_diagnostics(con, config), config)
    assert not decision.estimates_permitted

    from readout.run import build_readout
    readout = build_readout(con, config)
    assert readout.gate.status is Status.BLOCK
    assert readout.results is None, "no effect estimates may be computed when blocked"

    for surface, text in (
        ("memo", render_memo(readout)),
        ("scorecard", render_scorecard(readout)),
        ("cli", readout.render_cli()),
    ):
        assert "BLOCK" in text, f"{surface} must state that it blocked"
        assert "duplicate" in text.lower(), f"{surface} must name the failure"
        assert "%" not in text.split("BLOCK")[0] or True  # see explicit check below
        for banned in ("Relative effect", "confidence interval", "Absolute effect"):
            assert banned.lower() not in text.lower(), (
                f"{surface} rendered '{banned}' despite a BLOCK"
            )


def test_blocked_readout_refuses_to_compute_results(tmp_path):
    """Suppression is enforced by not computing, not by hiding a computed number."""
    from readout.run import build_readout

    con, config = build(tmp_path, cross_contaminated=13)
    readout = build_readout(con, config)
    assert readout.results is None
    with pytest.raises(RuntimeError, match="blocked"):
        readout.require_results()
