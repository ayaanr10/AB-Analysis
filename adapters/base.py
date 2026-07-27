"""Adapter interface (SPEC.md §7.1).

An adapter is the *only* code permitted to know a dataset's column names. Everything
downstream — diagnostics, metrics, gate, memo — reads the two-table contract and nothing
else. That constraint is what makes this a system rather than a script, so it is worth
being pedantic about.

Adapters express the mapping as SQL rather than as dataframe code, for three reasons:

1. It keeps the aggregation-in-SQL rule (docs/decisions.md D-02) unbroken end to end,
   rather than starting the pipeline in pandas and switching later.
2. The mapping becomes inspectable — `print(adapter.assignments_sql())` shows exactly what
   the contract was populated from, which is the first thing to check when a row count
   disagrees.
3. DuckDB reads the source file directly, so a 14M-row dataset never has to fit in memory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import duckdb

SOURCE_VIEW = "raw_source"


def sql_literal(value: str) -> str:
    """Quote a string for inline use in SQL."""
    return "'" + value.replace("'", "''") + "'"


def sql_in_list(values) -> str:
    return "(" + ", ".join(sql_literal(v) for v in values) + ")"


class Adapter(ABC):
    """Maps one dataset into the two-table contract."""

    name: str

    @abstractmethod
    def register_source(self, con: duckdb.DuckDBPyConnection, path: str) -> None:
        """Create the `raw_source` view over the dataset's real file."""

    @abstractmethod
    def assignments_sql(self) -> str:
        """SELECT producing exactly (unit_id, variant, assigned_at)."""

    @abstractmethod
    def events_sql(self) -> str:
        """SELECT producing exactly (unit_id, event_type, event_at, value)."""

    def covariates_sql(self) -> str | None:
        """SELECT producing (unit_id, covariate_name, value), or None if the dataset has none.

        Returning None is a real answer, not a stub: Cookie Cats genuinely has no
        pre-treatment covariates (SPEC.md §5.2) and the readout says so rather than
        presenting an empty table as though the analysis merely found nothing.
        """
        return None

    def source_row_count(self, con: duckdb.DuckDBPyConnection) -> int:
        return con.execute(f"SELECT count(*) FROM {SOURCE_VIEW}").fetchone()[0]

    @property
    @abstractmethod
    def mapping_notes(self) -> str:
        """Prose explaining any impedance mismatch, surfaced in the readout.

        Not optional. Where a mapping had to make a judgement call — a precomputed
        boolean becoming a synthetic event, a synthesised unit id — the readout owns it
        in writing instead of hiding it behind a clean-looking table.
        """


_REGISTRY: dict[str, type[Adapter]] = {}


def register(cls: type[Adapter]) -> type[Adapter]:
    _REGISTRY[cls.name] = cls
    return cls


def get_adapter(name: str) -> Adapter:
    if name not in _REGISTRY:
        raise KeyError(f"unknown adapter {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()
