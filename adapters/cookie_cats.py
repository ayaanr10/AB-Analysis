"""Cookie Cats adapter — the ONLY module aware of this dataset's column names.

Source schema, verified against the real file (sha256 5ab54d76…, 90,189 rows):

    userid          BIGINT   unique player id
    version         VARCHAR  gate_30 (control) or gate_40 (treatment)
    sum_gamerounds  BIGINT   rounds played in the 14 days after install
    retention_1     BOOLEAN  did the player return 1 day after installing
    retention_7     BOOLEAN  did the player return 7 days after installing


The impedance mismatch, and how it is resolved
----------------------------------------------
The contract (SPEC.md §6) expects *events*: a thing that happened, at a time. Cookie Cats
ships *outcomes*: two booleans that some upstream job already resolved. There is no event
log behind them and no timestamp anywhere in the file. So the mapping has to invent a
shape the source does not have, and the honest question is which distortion to accept.

`retention_1` / `retention_7` become synthetic `return_d1` / `return_d7` events with a
null `event_at`, emitted only where the flag is true.

Two things about that are worth defending explicitly, because both look wrong at a glance:

**Why the horizon is in the event type rather than computed from timestamps.** SPEC.md §6
says to map each true value to a synthetic `return` event. Taken literally — one shared
`return` type — day-1 and day-7 outcomes become indistinguishable the moment they are in
the same table, since with `event_at` null there is nothing left to separate them by. The
horizon has to survive the mapping somehow, so it is carried in `event_type`, and the
config declares which event type each horizon reads (`mode: precomputed`). This is a
refinement of the spec's instruction, not a departure from it: the horizon is still
declared rather than computed, which is what §6 was protecting.

**Why a false flag emits no event, rather than an event with value 0.** "Did not return"
is the absence of a return, and the contract already handles absence — a unit with no
matching event contributes a 0 to a `binary_rate` metric via the left join in
sql/02_metrics.sql. Emitting explicit zero-valued events would double the events table to
record nothing, and would make the orphan-event and zero-activity diagnostics (§7.2) read
very differently for no analytical gain.

The cost of both choices is real and is stated in the readout rather than hidden: this
dataset cannot support a true sequential analysis, so the peeking work in §7.6 is a
simulation. Horizons here are labels on precomputed columns, not windows this system
computed, and the memo says so.


sum_gamerounds: usable as an outcome, not as a covariate
--------------------------------------------------------
It is emitted as a `game_rounds` event carrying the round count as `value`, and the config
declares it a **guardrail metric**. That is not a contradiction of SPEC.md §5.2, which
bars it from segmentation and from CUPED. The distinction is the one that matters and is
easy to blur:

- As a **covariate** (splitting results by it, or adjusting with it) it is fatal. It is
  measured in the 14 days *after* install, players are randomised *at* install, so the
  treatment can move it. Conditioning on it conditions on an outcome of the treatment and
  breaks the comparability randomisation bought — collider bias (SPEC.md §4).
- As an **outcome** it is perfectly legitimate. "Did moving the gate change how much
  people played?" is a fair question, answered by comparing its mean across arms, with no
  conditioning anywhere.

Post-treatment disqualifies a variable from the right-hand side of the analysis, not from
the left. Emitting it as a guardrail is what lets the readout answer the engagement
question without touching the error §5.2 warns about — and gives the memo a concrete
example of the same column being fine in one role and disqualifying in another.


Covariates: none, and that is a finding
---------------------------------------
`covariates_sql()` returns None. After removing the id, the arm label, the two outcomes
and the post-treatment round count, nothing is left that was known before randomisation.
Heterogeneous treatment effects and CUPED are therefore not possible here — which is why
Case Study 2 exists (docs/dataset_selection.md).
"""

from __future__ import annotations

import duckdb

from .base import SOURCE_VIEW, Adapter, register, sql_literal


@register
class CookieCatsAdapter(Adapter):
    name = "cookie_cats"

    def register_source(self, con: duckdb.DuckDBPyConnection, path: str) -> None:
        con.execute(
            f"""
            CREATE OR REPLACE VIEW {SOURCE_VIEW} AS
            SELECT * FROM read_csv_auto({sql_literal(path)}, header = true)
            """
        )
        columns = {r[0] for r in con.execute(f"DESCRIBE {SOURCE_VIEW}").fetchall()}
        expected = {"userid", "version", "sum_gamerounds", "retention_1", "retention_7"}
        if missing := expected - columns:
            raise ValueError(
                f"{path} is not the Cookie Cats file this adapter expects: "
                f"missing column(s) {sorted(missing)}. Found {sorted(columns)}."
            )

    def assignments_sql(self) -> str:
        # assigned_at is null: the file has no time dimension at all (SPEC.md §5.2).
        return f"""
            SELECT
                CAST(userid AS VARCHAR) AS unit_id,
                version                 AS variant,
                CAST(NULL AS TIMESTAMP) AS assigned_at
            FROM {SOURCE_VIEW}
        """

    def events_sql(self) -> str:
        return f"""
            -- Day-1 retention: emitted only where the player did return.
            SELECT CAST(userid AS VARCHAR)  AS unit_id,
                   'return_d1'              AS event_type,
                   CAST(NULL AS TIMESTAMP)  AS event_at,
                   CAST(NULL AS DOUBLE)     AS value
            FROM {SOURCE_VIEW}
            WHERE retention_1

            UNION ALL

            -- Day-7 retention.
            SELECT CAST(userid AS VARCHAR), 'return_d7', CAST(NULL AS TIMESTAMP), CAST(NULL AS DOUBLE)
            FROM {SOURCE_VIEW}
            WHERE retention_7

            UNION ALL

            -- Guardrail outcome, one row per player, carrying the 14-day round count.
            -- An outcome, never a covariate — see the module docstring.
            SELECT CAST(userid AS VARCHAR), 'game_rounds', CAST(NULL AS TIMESTAMP),
                   CAST(sum_gamerounds AS DOUBLE)
            FROM {SOURCE_VIEW}
        """

    def covariates_sql(self) -> str | None:
        return None  # genuinely none — see the module docstring

    @property
    def mapping_notes(self) -> str:
        return (
            "Cookie Cats ships precomputed retention booleans, not an event log, and has no "
            "timestamp column. Each true flag is mapped to a synthetic return event with a null "
            "event_at, and the horizon is carried in the event type because a null timestamp "
            "leaves nothing else to separate day-1 from day-7 by. Horizons here are therefore "
            "labels on precomputed columns rather than windows this system computed, and no true "
            "sequential analysis is possible on this dataset. sum_gamerounds is emitted as a "
            "guardrail outcome; it is post-treatment, so it is never used as a covariate."
        )
