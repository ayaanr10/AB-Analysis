"""Criteo Uplift adapter — the ONLY module aware of this dataset's column names.

Source schema, verified against the real file (sha256 2716e1bf…, 13,979,592 rows):

    f0 … f11   DOUBLE   anonymised, randomly projected user features
    treatment  BIGINT   1 = treated, 0 = control
    visit      BIGINT   outcome
    conversion BIGINT   outcome
    exposure   BIGINT   post-treatment; see below

Selected as Case Study 2 because it is the only candidate of four with unit-level
pre-treatment covariates. The full evaluation, including the three rejections, is in
docs/dataset_selection.md.


Three impedance mismatches, all of them worse than Cookie Cats'
---------------------------------------------------------------

**1. There is no unit identifier.** Not an anonymised one — none at all. `unit_id` is
synthesised from the row ordinal via `row_number()`. The contract is satisfied (ids are
unique, one row per unit) but two diagnostics are hollowed out by it: duplicate detection
and cross-contamination cannot fail, because row ordinals cannot repeat. The adapter sets
`synthesises_unit_ids = True`, and the gate reports both checks as NOT_APPLICABLE rather
than as passes — a green tick that cannot go red tells a reader something untrue about how
hard this system looked (docs/decisions.md D-08).

**2. `exposure` is post-treatment, and it is confirmed, not assumed.** In the real file
`exposure = 1` occurs only where `treatment = 1` — 428,212 rows, all treated, zero
controls. It records the treatment being delivered. It is emitted as an *outcome event*
and never as a covariate. Same error class as `sum_gamerounds` in SPEC.md §5.2, and the
same resolution.

**3. There is no time dimension.** As with Cookie Cats, outcomes are precomputed flags and
`event_at` is null throughout, so horizons are declared rather than computed and no real
sequential analysis is possible. Criterion 2 of SPEC.md §5.3 was hoped for here and is not
met — the one thing this dataset was supposed to add beyond covariates, it does not add.


The covariates, and what can honestly be claimed about them
-----------------------------------------------------------

f0–f11 are emitted to the `covariates` table and used for balance, segmentation and CUPED.
The claim that they are pre-treatment rests on two things, and neither is proof:

- Criteo documents them as user features collected for an incrementality test.
- A balance check across arms fails to falsify it: worst |SMD| = 0.0488, all twelve inside
  the conventional 0.10 threshold.

Their values were randomly projected before release, so their timing cannot be verified
from the file itself. Balance can *rule out* a covariate that the treatment moved; it
cannot establish that a variable was measured beforehand (docs/dataset_selection.md §2).
So the honest statement is that pre-treatment status here is **unfalsified, not
established** — weaker than what Cookie Cats' `userid`/`version` permit, and stated as a
limitation in the write-up rather than glossed.

The anonymisation has a second cost: "uplift is higher in the top quartile of f9" is not a
sentence a product team can act on. The segmentation in this case study demonstrates
*method* — pre-treatment-only splits, multiple-comparison discipline — and deliberately
claims no product insight.
"""

from __future__ import annotations

import duckdb

from .base import SOURCE_VIEW, Adapter, register, sql_literal

COVARIATES = tuple(f"f{i}" for i in range(12))


@register
class CriteoAdapter(Adapter):
    name = "criteo"

    #: Consumed by readout/run.py to mark identity-dependent diagnostics NOT_APPLICABLE.
    synthesises_unit_ids = True

    def register_source(self, con: duckdb.DuckDBPyConnection, path: str) -> None:
        # row_number() is materialised once here so every downstream SELECT agrees about
        # which row is which unit. Recomputing it per query would risk two different
        # orderings silently disagreeing about identity.
        con.execute(
            f"""
            CREATE OR REPLACE VIEW {SOURCE_VIEW} AS
            SELECT row_number() OVER () AS _row_id, *
            FROM read_csv_auto({sql_literal(path)}, header = true)
            """
        )
        columns = {r[0] for r in con.execute(f"DESCRIBE {SOURCE_VIEW}").fetchall()}
        expected = {"treatment", "visit", "conversion", "exposure", *COVARIATES}
        if missing := expected - columns:
            raise ValueError(
                f"{path} is not the Criteo uplift file this adapter expects: "
                f"missing column(s) {sorted(missing)}."
            )

    def assignments_sql(self) -> str:
        return f"""
            SELECT
                CAST(_row_id AS VARCHAR)                                  AS unit_id,
                CASE WHEN treatment = 1 THEN 'treated' ELSE 'holdout' END AS variant,
                CAST(NULL AS TIMESTAMP)                                   AS assigned_at
            FROM {SOURCE_VIEW}
        """

    def events_sql(self) -> str:
        return f"""
            SELECT CAST(_row_id AS VARCHAR) AS unit_id,
                   'visit'                  AS event_type,
                   CAST(NULL AS TIMESTAMP)  AS event_at,
                   CAST(NULL AS DOUBLE)     AS value
            FROM {SOURCE_VIEW}
            WHERE visit = 1

            UNION ALL

            SELECT CAST(_row_id AS VARCHAR), 'conversion', CAST(NULL AS TIMESTAMP),
                   CAST(NULL AS DOUBLE)
            FROM {SOURCE_VIEW}
            WHERE conversion = 1

            UNION ALL

            -- Post-treatment. An outcome, never a covariate: exposure=1 occurs only under
            -- treatment=1, so it records the treatment being delivered.
            SELECT CAST(_row_id AS VARCHAR), 'exposure', CAST(NULL AS TIMESTAMP),
                   CAST(NULL AS DOUBLE)
            FROM {SOURCE_VIEW}
            WHERE exposure = 1
        """

    def covariates_sql(self) -> str:
        # UNPIVOT to reach the contract's long (unit_id, covariate_name, value) shape,
        # so the balance and CUPED SQL never has to know how many covariates exist.
        return f"""
            SELECT CAST(_row_id AS VARCHAR) AS unit_id,
                   covariate_name           AS covariate_name,
                   CAST(value AS DOUBLE)    AS value
            FROM (
                SELECT _row_id, {', '.join(COVARIATES)} FROM {SOURCE_VIEW}
            )
            UNPIVOT (value FOR covariate_name IN ({', '.join(COVARIATES)}))
        """

    @property
    def mapping_notes(self) -> str:
        return (
            "Criteo ships no unit identifier, so unit_id is the row ordinal — duplicate and "
            "cross-contamination checks cannot fail here and are reported as non-diagnostic. "
            "The intended 85/15 split is inferred from the data rather than published, so the "
            "SRM check is circular and is also reported as non-diagnostic. Outcomes are "
            "precomputed flags with no timestamps, so horizons are declared rather than "
            "computed. exposure is post-treatment (it occurs only under treatment) and is "
            "emitted as an outcome, never as a covariate. f0-f11 are anonymised and randomly "
            "projected: their pre-treatment status is unfalsified by the balance check, not "
            "established by it."
        )
