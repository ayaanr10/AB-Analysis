"""Run the validity diagnostics and assign each one a gate status (SPEC.md §7.2).

Aggregation happens in sql/01_diagnostics.sql. This module materialises the config into a
table the SQL can join to, converts test statistics into p-values, and applies the
thresholds. Thresholds live here rather than in SQL because they are policy — the values
are arguable and each one is defended in a comment where it is defined.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
from scipy import stats

from .config import ExperimentConfig
from .contract import materialise_config_variants
from .gate import DiagnosticResult, Status

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# --- thresholds, each with its reason -------------------------------------------------

# SRM (SPEC.md §7.2, docs/decisions.md D-01). 0.001 is the industry convention: at 0.05 a
# platform running many experiments generates constant false alarms. A p-value is the
# right instrument here — unlike the balance check, the null ("assignment is behaving as
# designed") is a real mechanism claim, not an approximation that large n makes brittle.
SRM_BLOCK_P = 0.001
SRM_WARN_P = 0.05

# Covariate balance (docs/decisions.md D-04). Effect size, not p-value: at n = 14M every
# Criteo covariate rejects on p while sitting inside |SMD| = 0.05. 0.10 is the
# conventional threshold from the matching literature.
BALANCE_BLOCK_SMD = 0.25
BALANCE_WARN_SMD = 0.10

# Zero-activity imbalance. SPEC.md §7.2 asks for this to be *reported* per variant and
# calls a large imbalance a signal — it does not ask for it to block, and blocking on it
# would be this system inventing a rule the spec did not agree to. So it warns at most.
# The 1pp floor keeps a statistically detectable but trivial gap from crying wolf at large n.
ZERO_ACTIVITY_WARN_DIFF = 0.01
ZERO_ACTIVITY_WARN_P = 0.001


def _srm(con: duckdb.DuckDBPyConnection, config: ExperimentConfig) -> DiagnosticResult:
    rows = con.execute(
        "SELECT variant, observed_units, expected_units, observed_share, chi2_contribution"
        " FROM diag_variant_counts"
    ).fetchall()
    observed = {v: int(n) for v, n, *_ in rows}
    shares = {v: (s if s is not None else 0.0) for v, _, _, s, _ in rows}
    chi2 = sum(c for *_, c in rows if c is not None)
    dof = max(len(rows) - 1, 1)
    p = float(stats.chi2.sf(chi2, dof))

    split_text = ", ".join(f"{v} {shares[v]:.2%}" for v in config.variants)
    intended_text = ", ".join(f"{v} {config.intended_split[v]:.0%}" for v in config.variants)
    facts = {"chi2": chi2, "p_value": p, "dof": dof, "observed": observed, "shares": shares}

    if not config.srm_is_diagnostic:
        return DiagnosticResult(
            name="srm",
            label="Sample ratio mismatch",
            status=Status.NOT_APPLICABLE,
            headline=f"observed {split_text}; intended ratio is inferred, not published",
            detail=(
                "The intended split for this experiment is not documented, so it was inferred "
                "from the observed counts. Testing those counts against a ratio derived from "
                "them is circular — the test cannot reject, whatever the data looks like. "
                "Reported as non-diagnostic rather than as a pass (docs/decisions.md D-08)."
            ),
            observed=facts,
        )

    if p < SRM_BLOCK_P:
        status, verdict = Status.BLOCK, (
            f"p = {p:.2e} is below the {SRM_BLOCK_P} block threshold. The split is not "
            "consistent with the intended ratio, so assignment, logging or filtering is "
            "faulty and no comparison between these arms is interpretable."
        )
    elif p < SRM_WARN_P:
        status, verdict = Status.WARN, (
            f"p = {p:.4f} sits between the warn ({SRM_WARN_P}) and block ({SRM_BLOCK_P}) "
            "thresholds. The imbalance is unlikely to be chance and is reported next to "
            "every estimate, but it is small in magnitude and applies equally to every "
            "metric and horizon, so it cannot by itself reverse the sign of an effect."
        )
    else:
        status, verdict = Status.PASS, f"p = {p:.4f}; consistent with the intended ratio."

    return DiagnosticResult(
        name="srm",
        label="Sample ratio mismatch",
        status=status,
        headline=f"observed {split_text} vs intended {intended_text}, chi2={chi2:.4f}, p={p:.6f}",
        detail=verdict,
        observed=facts,
    )


def _duplicates(con: duckdb.DuckDBPyConnection, synthetic_units: bool) -> DiagnosticResult:
    n_units, n_rows = con.execute(
        "SELECT count(*), coalesce(sum(n_rows), 0) FROM diag_duplicate_units"
    ).fetchone()
    facts = {"duplicate_unit_ids": int(n_units), "duplicate_rows": int(n_rows)}

    if synthetic_units:
        return DiagnosticResult(
            name="duplicate_units", label="Duplicate assignments",
            status=Status.NOT_APPLICABLE,
            headline="unit ids are synthesised from row position; duplicates are impossible",
            detail=(
                "This dataset ships no unit identifier, so unit_id was generated from row "
                "ordinal. Every id is unique by construction and this check cannot fail. "
                "Reported as non-diagnostic rather than as a pass — a green tick here would "
                "imply a verification that did not happen (docs/decisions.md D-08)."
            ),
            observed=facts,
        )
    if n_units:
        return DiagnosticResult(
            name="duplicate_units", label="Duplicate assignments", status=Status.BLOCK,
            headline=f"{n_units:,} unit_id(s) appear more than once ({n_rows:,} rows)",
            detail=(
                "The contract is one row per randomised unit (SPEC.md §6). Duplicates mean "
                "some units are weighted more than once in every metric, and it is not "
                "knowable from the data which row is authoritative."
            ),
            observed=facts,
        )
    return DiagnosticResult(
        name="duplicate_units", label="Duplicate assignments", status=Status.PASS,
        headline="every unit_id appears exactly once", detail="", observed=facts,
    )


def _cross_contamination(con: duckdb.DuckDBPyConnection, synthetic_units: bool) -> DiagnosticResult:
    rows = con.execute(
        "SELECT unit_id, variants FROM diag_cross_contamination LIMIT 5"
    ).fetchall()
    n = con.execute("SELECT count(*) FROM diag_cross_contamination").fetchone()[0]
    facts = {"contaminated_units": int(n), "examples": [r[0] for r in rows]}

    if synthetic_units:
        return DiagnosticResult(
            name="cross_contamination", label="Cross-contamination",
            status=Status.NOT_APPLICABLE,
            headline="unit ids are synthesised; a unit cannot span two arms by construction",
            detail=(
                "Same reason as the duplicate check: with row-ordinal ids, no id can appear "
                "twice, so contamination is undetectable rather than absent."
            ),
            observed=facts,
        )
    if n:
        return DiagnosticResult(
            name="cross_contamination", label="Cross-contamination", status=Status.BLOCK,
            headline=f"{n:,} unit(s) appear under more than one variant",
            detail=(
                "The arms are not disjoint. A unit exposed to both experiences cannot be "
                "attributed to either, so the difference between arms no longer estimates "
                "the treatment effect. This is strictly worse than a duplicate."
            ),
            observed=facts,
        )
    return DiagnosticResult(
        name="cross_contamination", label="Cross-contamination", status=Status.PASS,
        headline="every unit belongs to exactly one variant", detail="", observed=facts,
    )


def _orphan_events(con: duckdb.DuckDBPyConnection) -> DiagnosticResult:
    n_units, n_events = con.execute(
        "SELECT count(*), coalesce(sum(n_events), 0) FROM diag_orphan_events"
    ).fetchone()
    facts = {"orphan_units": int(n_units), "orphan_events": int(n_events)}
    if n_units:
        return DiagnosticResult(
            name="orphan_events", label="Orphan events", status=Status.BLOCK,
            headline=f"{n_events:,} event(s) from {n_units:,} unassigned unit(s)",
            detail=(
                "Events exist for units with no assignment row (SPEC.md §6: a validity "
                "failure). Either the assignment table is incomplete — in which case every "
                "denominator is wrong — or events are leaking in from outside the "
                "experiment. Both make the metrics mean something other than what they say."
            ),
            observed=facts,
        )
    return DiagnosticResult(
        name="orphan_events", label="Orphan events", status=Status.PASS,
        headline="every event belongs to an assigned unit", detail="", observed=facts,
    )


def _zero_activity(con: duckdb.DuckDBPyConnection, config: ExperimentConfig) -> DiagnosticResult:
    rows = con.execute(
        "SELECT variant, assigned_units, zero_activity_units, zero_activity_rate"
        " FROM diag_zero_activity"
    ).fetchall()
    covering = [
        r[0] for r in con.execute(
            "SELECT event_type FROM diag_event_coverage WHERE covers_every_unit"
        ).fetchall()
    ]
    present = {r[0] for r in con.execute("SELECT DISTINCT event_type FROM events").fetchall()}
    outcome_types = {h.event_type for m in config.metrics for h in m.horizons}
    facts = {
        "by_variant": {r[0]: {"assigned": int(r[1]), "zero": int(r[2]), "rate": r[3]} for r in rows},
        "covering_event_types": covering,
        "non_outcome_event_types": sorted(present - outcome_types),
    }

    if present and not (present - outcome_types):
        # Every event in the data is an outcome the experiment is measuring. "Zero events"
        # then means "achieved no outcome", which is the treatment effect restated — a real
        # lift necessarily shows up here as an arm-level gap. The check cannot separate the
        # signal it is looking for (differential logging or attrition) from the signal it is
        # supposed to be independent of, so it reports nothing rather than a false alarm.
        return DiagnosticResult(
            name="zero_activity", label="Zero-activity units", status=Status.NOT_APPLICABLE,
            headline="every event type in this dataset is an outcome being measured",
            detail=(
                "This check looks for units that never showed up at all — a sign of "
                "differential logging, or of the treatment driving units away before they "
                "could act. It needs an activity signal that is not itself an outcome. Here "
                f"the only event type(s) present ({', '.join(sorted(present))}) are the "
                "metrics under test, so a unit with no events is simply a unit that did not "
                "convert, and any genuine treatment effect would trip this check. Reported "
                "as non-diagnostic rather than run and reported as a pass or a warning."
            ),
            observed=facts,
        )

    if covering:
        return DiagnosticResult(
            name="zero_activity", label="Zero-activity units", status=Status.NOT_APPLICABLE,
            headline=f"'{covering[0]}' is emitted for every assigned unit; no unit can have zero",
            detail=(
                f"The adapter emits {', '.join(repr(c) for c in covering)} unconditionally, one "
                "row per unit, so zero-activity is 0 by construction rather than by observation. "
                "The check is reported as non-diagnostic (docs/decisions.md D-08). On Cookie "
                "Cats the substantive version of this question — players who installed and "
                "never played a round — is a metric, not a validity check, and appears in the "
                "guardrail rather than here."
            ),
            observed=facts,
        )
    if len(rows) < 2:
        return DiagnosticResult(
            name="zero_activity", label="Zero-activity units", status=Status.NOT_APPLICABLE,
            headline="fewer than two arms with assigned units", detail="", observed=facts,
        )

    counts = [[int(r[2]), int(r[1]) - int(r[2])] for r in rows]
    rates = [r[3] or 0.0 for r in rows]
    spread = max(rates) - min(rates)
    try:
        _, p, _, _ = stats.chi2_contingency(counts)
    except ValueError:
        p = 1.0
    facts.update({"p_value": float(p), "rate_spread": spread})

    summary = ", ".join(f"{r[0]} {r[3]:.2%}" for r in rows)
    if spread >= ZERO_ACTIVITY_WARN_DIFF and p < ZERO_ACTIVITY_WARN_P:
        return DiagnosticResult(
            name="zero_activity", label="Zero-activity units", status=Status.WARN,
            headline=f"{summary} — {spread:.2%} gap between arms (p={p:.2e})",
            detail=(
                "Arms differ in how many assigned units produced no events at all. That can "
                "mean differential logging, or that the treatment itself drove units away "
                "before they could act. Either way the arms are no longer comparable in the "
                "way the metrics assume. Reported as a warning, not a block: SPEC.md §7.2 "
                "asks for this to be surfaced as a signal and does not specify a threshold."
            ),
            observed=facts,
        )
    return DiagnosticResult(
        name="zero_activity", label="Zero-activity units", status=Status.PASS,
        headline=f"{summary} — no material gap between arms",
        detail="", observed=facts,
    )


def _balance(con: duckdb.DuckDBPyConnection, config: ExperimentConfig) -> DiagnosticResult:
    if not config.has_covariates():
        return DiagnosticResult(
            name="pre_period_balance", label="Pre-period balance",
            status=Status.NOT_APPLICABLE,
            headline="no pre-treatment covariates exist in this dataset",
            detail=(
                "Nothing in this dataset was known before randomisation, so there is nothing "
                "to check balance on. This is a property of the data, not an analysis that "
                "was skipped — see SPEC.md §5.2."
            ),
            observed={"covariates": []},
        )

    rows = con.execute(
        "SELECT covariate_name, variant, n_treatment, n_control, mean_treatment,"
        " mean_control, var_treatment, var_control, smd FROM diag_covariate_balance"
    ).fetchall()
    detailed = []
    worst = 0.0
    for name, variant, n_t, n_c, m_t, m_c, v_t, v_c, smd in rows:
        smd = smd or 0.0
        se = ((v_t or 0) / max(n_t, 1) + (v_c or 0) / max(n_c, 1)) ** 0.5
        t = (m_t - m_c) / se if se else 0.0
        detailed.append({
            "covariate": name, "variant": variant, "smd": smd,
            "p_value": float(2 * stats.norm.sf(abs(t))), "t": t,
        })
        worst = max(worst, abs(smd))

    facts = {"worst_abs_smd": worst, "covariates": detailed,
             "n_rejecting_on_p": sum(1 for d in detailed if d["p_value"] < 0.001)}

    if worst >= BALANCE_BLOCK_SMD:
        status, detail = Status.BLOCK, (
            f"|SMD| = {worst:.3f} exceeds {BALANCE_BLOCK_SMD}. A covariate known before "
            "randomisation differs materially between arms, so the arms were not comparable "
            "to begin with and the difference between them is not the treatment effect."
        )
    elif worst >= BALANCE_WARN_SMD:
        status, detail = Status.WARN, (
            f"|SMD| = {worst:.3f} exceeds the {BALANCE_WARN_SMD} balance threshold."
        )
    else:
        status, detail = Status.PASS, (
            f"Worst |SMD| = {worst:.3f}, inside the {BALANCE_WARN_SMD} threshold. "
            f"Note that {facts['n_rejecting_on_p']} of {len(detailed)} covariates would be "
            "flagged by a t-test at p < 0.001 — at this sample size the test detects "
            "imbalances far too small to bias anything, which is why this check is built on "
            "effect size (docs/decisions.md D-04)."
        )

    return DiagnosticResult(
        name="pre_period_balance", label="Pre-period balance", status=status,
        headline=f"worst |SMD| = {worst:.4f} across {len(detailed)} covariate(s)",
        detail=detail, observed=facts,
    )


def run_diagnostics(
    con: duckdb.DuckDBPyConnection,
    config: ExperimentConfig,
    synthetic_units: bool = False,
) -> tuple[DiagnosticResult, ...]:
    """Run every validity check. Called before any effect estimate exists."""
    materialise_config_variants(con, config)
    con.execute((SQL_DIR / "01_diagnostics.sql").read_text())
    return (
        _srm(con, config),
        _duplicates(con, synthetic_units),
        _cross_contamination(con, synthetic_units),
        _orphan_events(con),
        _zero_activity(con, config),
        _balance(con, config),
    )
