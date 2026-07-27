"""Experiment config parsing and validation (SPEC.md §6, §7.1).

A config declares what an experiment *is* — its arms, its intended split, its metrics and
the horizons those metrics are read at. Nothing downstream may hardcode any of it.

Validation is strict and fails loudly. SPEC.md §7.1 requires that an unknown variant is an
error rather than a silent pass, and the same reasoning applies to every other field: a
typo in a config that silently degrades to a default produces a readout that is wrong in a
way nobody notices. Unknown keys are rejected for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

SPLIT_TOLERANCE = 1e-6

METRIC_TYPES = {
    "binary_rate",  # share of assigned units with >=1 qualifying event
    "mean_value",   # mean over assigned units of summed event value (units with none count as 0)
}
HORIZON_MODES = {
    "precomputed",  # the source ships the outcome already resolved at this horizon
    "elapsed",      # the horizon is computed from event_at - assigned_at
}
SPLIT_SOURCES = {
    "stated",    # the experiment's intended ratio is documented by the publisher
    "inferred",  # read off the observed data; see Horizon.srm_is_diagnostic
}


class ConfigError(ValueError):
    """Raised when a config is malformed. Never downgraded to a warning."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _check_keys(obj: dict, allowed: set[str], required: set[str], where: str) -> None:
    if not isinstance(obj, dict):
        raise ConfigError(f"{where}: expected a mapping, got {type(obj).__name__}")
    unknown = set(obj) - allowed
    _require(not unknown, f"{where}: unknown key(s) {sorted(unknown)}. Allowed: {sorted(allowed)}")
    missing = required - set(obj)
    _require(not missing, f"{where}: missing required key(s) {sorted(missing)}")


@dataclass(frozen=True)
class Horizon:
    """One measurement window for a metric.

    `precomputed` horizons are the awkward case and the common one in public datasets:
    the source gives you "did this user come back within 7 days" as a boolean, with no
    timestamp behind it. The horizon is then a property of *which event type* carries the
    outcome, not something computable. See adapters/cookie_cats.py for the full argument.
    """

    name: str
    label: str
    event_type: str
    mode: str
    window_days: float | None = None

    @property
    def is_precomputed(self) -> bool:
        return self.mode == "precomputed"


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    role: str  # "primary" | "guardrail"
    type: str
    horizons: tuple[Horizon, ...]
    direction: str = "increase"  # which way is good, for reading the effect
    note: str = ""

    @property
    def is_primary(self) -> bool:
        return self.role == "primary"


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    description: str
    adapter: str
    source_path: str
    variants: tuple[str, ...]
    control: str
    intended_split: dict[str, float]
    intended_split_source: str
    metrics: tuple[Metric, ...]
    covariates: tuple[str, ...] = ()
    practical_effect: float | None = None  # the effect the business would care about (§7.4)
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def treatments(self) -> tuple[str, ...]:
        return tuple(v for v in self.variants if v != self.control)

    @property
    def primary_metric(self) -> Metric:
        return next(m for m in self.metrics if m.is_primary)

    @property
    def guardrail_metrics(self) -> tuple[Metric, ...]:
        return tuple(m for m in self.metrics if not m.is_primary)

    @property
    def srm_is_diagnostic(self) -> bool:
        """Whether the SRM check can actually fail.

        Testing observed counts against a ratio that was itself read off those counts is
        circular — it cannot reject. Where the intended split is inferred rather than
        published, the SRM result is reported as NOT_APPLICABLE rather than as a pass
        (docs/decisions.md D-08).
        """
        return self.intended_split_source == "stated"

    def has_covariates(self) -> bool:
        return bool(self.covariates)


def _parse_horizon(raw: dict, where: str) -> Horizon:
    _check_keys(
        raw,
        allowed={"name", "label", "event_type", "mode", "window_days"},
        required={"name", "event_type", "mode"},
        where=where,
    )
    mode = raw["mode"]
    _require(mode in HORIZON_MODES, f"{where}: mode must be one of {sorted(HORIZON_MODES)}, got {mode!r}")
    window = raw.get("window_days")
    if mode == "elapsed":
        _require(window is not None, f"{where}: mode 'elapsed' requires window_days")
        _require(isinstance(window, (int, float)) and window > 0, f"{where}: window_days must be > 0")
    else:
        _require(window is None, f"{where}: window_days is meaningless for mode 'precomputed'")
    return Horizon(
        name=raw["name"],
        label=raw.get("label", raw["name"]),
        event_type=raw["event_type"],
        mode=mode,
        window_days=float(window) if window is not None else None,
    )


def _parse_metric(raw: dict, where: str) -> Metric:
    _check_keys(
        raw,
        allowed={"name", "label", "role", "type", "horizons", "direction", "note"},
        required={"name", "role", "type", "horizons"},
        where=where,
    )
    _require(raw["role"] in {"primary", "guardrail"}, f"{where}: role must be 'primary' or 'guardrail'")
    _require(raw["type"] in METRIC_TYPES, f"{where}: type must be one of {sorted(METRIC_TYPES)}")
    _require(raw.get("direction", "increase") in {"increase", "decrease"},
             f"{where}: direction must be 'increase' or 'decrease'")

    horizons = raw["horizons"]
    _require(isinstance(horizons, list) and horizons, f"{where}: horizons must be a non-empty list")
    parsed = tuple(_parse_horizon(h, f"{where}.horizons[{i}]") for i, h in enumerate(horizons))

    names = [h.name for h in parsed]
    _require(len(set(names)) == len(names), f"{where}: duplicate horizon names {names}")

    return Metric(
        name=raw["name"],
        label=raw.get("label", raw["name"]),
        role=raw["role"],
        type=raw["type"],
        horizons=parsed,
        direction=raw.get("direction", "increase"),
        note=raw.get("note", ""),
    )


def load_config(path: str | Path) -> ExperimentConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config not found: {path}")

    raw = yaml.safe_load(path.read_text())
    _check_keys(
        raw,
        allowed={
            "experiment", "description", "source", "variants", "control",
            "intended_split", "intended_split_source", "metrics", "covariates",
            "practical_effect", "notes",
        },
        required={"experiment", "source", "variants", "control", "intended_split",
                  "intended_split_source", "metrics"},
        where=str(path),
    )

    source = raw["source"]
    _check_keys(source, allowed={"adapter", "path"}, required={"adapter", "path"},
                where=f"{path}.source")

    variants = raw["variants"]
    _require(isinstance(variants, list) and len(variants) >= 2,
             f"{path}: variants must list at least 2 arms")
    _require(len(set(variants)) == len(variants), f"{path}: duplicate variants {variants}")

    control = raw["control"]
    _require(control in variants, f"{path}: control {control!r} is not in variants {variants}")

    split = raw["intended_split"]
    _require(isinstance(split, dict), f"{path}.intended_split: expected a mapping")
    _require(set(split) == set(variants),
             f"{path}.intended_split: keys {sorted(split)} must match variants {sorted(variants)}")
    _require(all(isinstance(v, (int, float)) and v > 0 for v in split.values()),
             f"{path}.intended_split: every share must be > 0")
    total = sum(split.values())
    _require(abs(total - 1.0) < SPLIT_TOLERANCE,
             f"{path}.intended_split: shares sum to {total}, expected 1.0")

    split_source = raw["intended_split_source"]
    _require(split_source in SPLIT_SOURCES,
             f"{path}.intended_split_source must be one of {sorted(SPLIT_SOURCES)}")

    metrics_raw = raw["metrics"]
    _require(isinstance(metrics_raw, list) and metrics_raw, f"{path}: metrics must be a non-empty list")
    metrics = tuple(_parse_metric(m, f"{path}.metrics[{i}]") for i, m in enumerate(metrics_raw))

    names = [m.name for m in metrics]
    _require(len(set(names)) == len(names), f"{path}: duplicate metric names {names}")
    primaries = [m.name for m in metrics if m.is_primary]
    _require(len(primaries) == 1,
             f"{path}: exactly one metric must have role 'primary', found {primaries}")

    effect = raw.get("practical_effect")
    if effect is not None:
        _require(isinstance(effect, (int, float)) and effect > 0,
                 f"{path}.practical_effect must be a positive relative effect, e.g. 0.02 for 2%")

    return ExperimentConfig(
        name=raw["experiment"],
        description=raw.get("description", ""),
        adapter=source["adapter"],
        source_path=source["path"],
        variants=tuple(variants),
        control=control,
        intended_split={k: float(v) for k, v in split.items()},
        intended_split_source=split_source,
        metrics=metrics,
        covariates=tuple(raw.get("covariates") or ()),
        practical_effect=float(effect) if effect is not None else None,
        notes=dict(raw.get("notes") or {}),
    )
