"""Load a dataset through an adapter into the two-table contract (SPEC.md §6, §7.1).

What this module deliberately does NOT do
-----------------------------------------
It does not enforce validity. Duplicate unit_ids, orphan events and cross-contamination
all load without complaint, and are caught downstream by the diagnostics (SPEC.md §7.2)
and the gate (§7.3).

That is the opposite of the usual instinct and it is load-bearing. If the loader rejected
a corrupted dataset, a broken experiment would fail with a stack trace at import time —
and the blocking rule, which is the most opinionated thing this system does, could never
fire on real corruption because nothing corrupt would ever reach it. tests/test_gate.py
depends on exactly this: a deliberately corrupted fixture must *load*, then be blocked with
a readable diagnostic.

The loader enforces only what makes a row unrepresentable — a null unit_id, a null variant,
a variant the config never declared. Those rows are moved to `load_rejects` with a reason
rather than dropped, because SPEC.md §7.1 requires that no row is ever silently discarded,
and a count alone does not let you go and look at what was lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from adapters.base import Adapter, sql_in_list

from .config import ExperimentConfig

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


@dataclass(frozen=True)
class LoadReport:
    """Reconciliation between the source file and the contract tables."""

    experiment: str
    source_rows: int
    assignment_rows: int
    event_rows: int
    covariate_rows: int
    rejects: tuple[tuple[str, str, int], ...]  # (table, reason, count)
    mapping_notes: str

    @property
    def rejected_assignments(self) -> int:
        return sum(n for table, _, n in self.rejects if table == "assignments")

    @property
    def reconciles(self) -> bool:
        """Every source row is accounted for: admitted, or rejected with a reason."""
        return self.source_rows == self.assignment_rows + self.rejected_assignments

    def render(self) -> str:
        lines = [
            f"Load: {self.experiment}",
            f"  source rows      {self.source_rows:>12,}",
            f"  assignments      {self.assignment_rows:>12,}",
            f"  events           {self.event_rows:>12,}",
            f"  covariates       {self.covariate_rows:>12,}",
        ]
        if self.rejects:
            lines.append("  rejected:")
            lines.extend(
                f"    {table}.{reason:<24s} {count:>10,}" for table, reason, count in self.rejects
            )
        else:
            lines.append("  rejected              0")
        verdict = "OK — every source row accounted for" if self.reconciles else "MISMATCH"
        lines.append(f"  reconciliation   {verdict}")
        return "\n".join(lines)


def materialise_config_variants(con: duckdb.DuckDBPyConnection, config: ExperimentConfig) -> None:
    """Put the config's arms into a table the SQL can join to.

    Both sql/01_diagnostics.sql and sql/02_metrics.sql need to know which arm is the
    control and what split was intended. Passing it as a table rather than formatting it
    into the query strings keeps those files static and readable, and means the intended
    split cannot silently disagree between the config and the SQL.

    Idempotent, and called by both diagnostics and metrics: metrics used to rely on
    diagnostics having run first, which held in the real pipeline and broke the moment
    anything called it on its own.
    """
    con.execute("DROP TABLE IF EXISTS config_variants")
    con.execute(
        "CREATE TABLE config_variants ("
        " variant VARCHAR, intended_share DOUBLE, is_control BOOLEAN)"
    )
    con.executemany(
        "INSERT INTO config_variants VALUES (?, ?, ?)",
        [(v, config.intended_split[v], v == config.control) for v in config.variants],
    )


def _create_contract_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute((SQL_DIR / "00_contract.sql").read_text())


def _check_columns(con: duckdb.DuckDBPyConnection, sql: str, expected: list[str], what: str) -> None:
    """Fail early and legibly when an adapter's SELECT does not match the contract.

    Without this the first symptom is a DuckDB binder error deep inside the loader,
    naming a column the adapter author never wrote — usually because they forgot to
    alias an expression. Adapters are the extension point of this system, so the error
    they hit when they get it wrong should say what to fix.
    """
    actual = [d[0] for d in con.execute(f"SELECT * FROM ({sql}) LIMIT 0").description]
    if actual != expected:
        raise ValueError(
            f"adapter {what} must produce exactly {expected}, got {actual}. "
            "Every selected expression needs an explicit alias."
        )


def load(
    con: duckdb.DuckDBPyConnection,
    adapter: Adapter,
    config: ExperimentConfig,
    source_path: str | None = None,
) -> LoadReport:
    """Map the source through `adapter` into `assignments`, `events` and `covariates`."""
    path = source_path or config.source_path
    if not Path(path).exists():
        raise FileNotFoundError(f"dataset not found: {path}. Run `bash data/download.sh` first.")

    adapter.register_source(con, path)
    _create_contract_tables(con)

    source_rows = adapter.source_row_count(con)
    declared = sql_in_list(config.variants)

    _check_columns(con, adapter.assignments_sql(),
                   ["unit_id", "variant", "assigned_at"], "assignments_sql()")
    _check_columns(con, adapter.events_sql(),
                   ["unit_id", "event_type", "event_at", "value"], "events_sql()")
    if (covariate_select := adapter.covariates_sql()) is not None:
        _check_columns(con, covariate_select,
                       ["unit_id", "covariate_name", "value"], "covariates_sql()")

    # An assignment row is admissible if it can be represented at all. Everything else
    # about it — duplicates, imbalance, contamination — is the diagnostics' business.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW _mapped_assignments AS
        SELECT *,
               CASE
                   WHEN unit_id IS NULL              THEN 'null_unit_id'
                   WHEN variant IS NULL              THEN 'null_variant'
                   WHEN variant NOT IN {declared}    THEN 'variant_not_in_config'
               END AS reject_reason
        FROM ({adapter.assignments_sql()})
        """
    )
    con.execute(
        "INSERT INTO assignments SELECT unit_id, variant, assigned_at "
        "FROM _mapped_assignments WHERE reject_reason IS NULL"
    )
    con.execute(
        "INSERT INTO load_rejects SELECT 'assignments', reject_reason, unit_id, variant "
        "FROM _mapped_assignments WHERE reject_reason IS NOT NULL"
    )

    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW _mapped_events AS
        SELECT *,
               CASE
                   WHEN unit_id IS NULL    THEN 'null_unit_id'
                   WHEN event_type IS NULL THEN 'null_event_type'
               END AS reject_reason
        FROM ({adapter.events_sql()})
        """
    )
    con.execute(
        "INSERT INTO events SELECT unit_id, event_type, event_at, value "
        "FROM _mapped_events WHERE reject_reason IS NULL"
    )
    con.execute(
        "INSERT INTO load_rejects SELECT 'events', reject_reason, unit_id, NULL "
        "FROM _mapped_events WHERE reject_reason IS NOT NULL"
    )

    covariate_sql = adapter.covariates_sql()
    if covariate_sql is not None:
        con.execute(
            f"INSERT INTO covariates SELECT unit_id, covariate_name, value FROM ({covariate_sql})"
        )

    counts = con.execute(
        "SELECT (SELECT count(*) FROM assignments), (SELECT count(*) FROM events),"
        " (SELECT count(*) FROM covariates)"
    ).fetchone()
    rejects = con.execute(
        "SELECT table_name, reason, count(*) FROM load_rejects GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()

    return LoadReport(
        experiment=config.name,
        source_rows=source_rows,
        assignment_rows=counts[0],
        event_rows=counts[1],
        covariate_rows=counts[2],
        rejects=tuple((t, r, n) for t, r, n in rejects),
        mapping_notes=adapter.mapping_notes,
    )
