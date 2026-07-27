"""Test fixtures: a synthetic dataset with controllable corruption.

The point of this module is the corrupted-input test the spec requires (SPEC.md §7.3,
§10). Real datasets are either clean or broken in ways you cannot dial; to prove the gate
actually blocks, corruption has to be injectable one failure mode at a time.

This is synthetic data used to test the *system*. It is not, and must never become, a
synthetic experiment used to produce a *finding* — that is the trap SPEC.md §8 forbids.
Nothing in here is ever analysed or written up.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

import duckdb
import pytest

from adapters.base import SOURCE_VIEW, Adapter, register, sql_literal
from readout.config import ExperimentConfig, Horizon, Metric


@register
class FixtureAdapter(Adapter):
    """Reads the synthetic fixture CSV.

    Columns: unit_id, variant, converted, assigned, active.

    `active` emits an `impression` event that is deliberately NOT an outcome metric. The
    zero-activity diagnostic needs an activity signal independent of the thing being
    measured — without one, "no events" just means "did not convert" and any real lift
    trips the check. See readout/diagnostics.py::_zero_activity.
    """

    name = "fixture"

    def register_source(self, con: duckdb.DuckDBPyConnection, path: str) -> None:
        con.execute(
            f"CREATE OR REPLACE VIEW {SOURCE_VIEW} AS "
            f"SELECT * FROM read_csv_auto({sql_literal(path)}, header = true)"
        )

    def source_row_count(self, con: duckdb.DuckDBPyConnection) -> int:
        # Rows that are meant to become assignments; orphan-event rows are not.
        return con.execute(f"SELECT count(*) FROM {SOURCE_VIEW} WHERE assigned").fetchone()[0]

    def assignments_sql(self) -> str:
        return f"""
            SELECT CAST(unit_id AS VARCHAR) AS unit_id,
                   variant                  AS variant,
                   CAST(NULL AS TIMESTAMP)  AS assigned_at
            FROM {SOURCE_VIEW} WHERE assigned
        """

    def events_sql(self) -> str:
        return f"""
            SELECT CAST(unit_id AS VARCHAR) AS unit_id,
                   'convert'                AS event_type,
                   CAST(NULL AS TIMESTAMP)  AS event_at,
                   CAST(NULL AS DOUBLE)     AS value
            FROM {SOURCE_VIEW} WHERE converted

            UNION ALL

            SELECT CAST(unit_id AS VARCHAR), 'impression', CAST(NULL AS TIMESTAMP),
                   CAST(NULL AS DOUBLE)
            FROM {SOURCE_VIEW} WHERE active
        """

    @property
    def mapping_notes(self) -> str:
        return "Synthetic fixture for testing the gate. Never analysed."


def write_fixture(
    path: Path,
    *,
    n_control: int = 5_000,
    n_treatment: int = 5_000,
    control_rate: float = 0.10,
    treatment_rate: float = 0.12,
    duplicate_units: int = 0,
    cross_contaminated: int = 0,
    orphan_events: int = 0,
    inactive_control: int = 0,
    inactive_treatment: int = 0,
    seed: int = 7,
) -> Path:
    """Write a fixture CSV, optionally injecting one or more validity failures."""
    rng = random.Random(seed)
    rows: list[dict] = []

    def add(unit_id, variant, rate, assigned=True, active=True):
        rows.append({
            "unit_id": unit_id,
            "variant": variant,
            "converted": active and rng.random() < rate,
            "assigned": assigned,
            "active": active,
        })

    for i in range(n_control):
        add(f"u{i}", "control", control_rate, active=i >= inactive_control)
    for i in range(n_treatment):
        add(f"u{n_control + i}", "treatment", treatment_rate, active=i >= inactive_treatment)

    # Same unit assigned twice to the same arm — a logging or pipeline fault.
    for i in range(duplicate_units):
        add(f"u{i}", "control", control_rate)

    # Same unit assigned to both arms — assignment is broken; the arms are not disjoint.
    for i in range(cross_contaminated):
        add(f"u{i}", "treatment", treatment_rate)

    # Events for units that were never assigned — the join is broken.
    for i in range(orphan_events):
        add(f"orphan{i}", "control", 1.0, assigned=False)

    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["unit_id", "variant", "converted", "assigned", "active"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def fixture_config(
    *,
    split: tuple[float, float] = (0.5, 0.5),
    split_source: str = "stated",
    source_path: str = "",
) -> ExperimentConfig:
    return ExperimentConfig(
        name="fixture_experiment",
        description="synthetic, for testing the gate",
        adapter="fixture",
        source_path=source_path,
        variants=("control", "treatment"),
        control="control",
        intended_split={"control": split[0], "treatment": split[1]},
        intended_split_source=split_source,
        metrics=(
            Metric(
                name="conversion",
                label="Conversion",
                role="primary",
                type="binary_rate",
                horizons=(
                    Horizon(name="overall", label="Conversion", event_type="convert",
                            mode="precomputed"),
                ),
            ),
        ),
        practical_effect=0.05,
    )


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def fixture_dir(tmp_path):
    return tmp_path
