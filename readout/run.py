"""Orchestration: load, diagnose, gate, and only then estimate.

The ordering constraints in SPEC.md §7.2 and §7.4 are enforced here structurally rather
than by discipline. `build_readout` cannot compute an effect before the gate has returned,
because the metrics call sits inside the branch that checks it — so "diagnostics before
estimates" is not a convention someone has to remember when adding a new output surface.

When the gate blocks, `results` is None. There is no estimate anywhere in the process to
leak into a memo, a scorecard or a log line by accident (SPEC.md §7.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from adapters.base import get_adapter
from analysis.peeking_sim import PeekingResult, simulate_peeking
from analysis.power import PowerResult, minimum_detectable_effect

from .config import ExperimentConfig, load_config
from .contract import LoadReport, load
from .diagnostics import run_diagnostics
from .gate import GateDecision, Status, evaluate
from .metrics import HorizonResult, compute_metrics, horizon_contrast
from .segments import SegmentationReport, render_segments, run_segments


@dataclass(frozen=True)
class HorizonPower:
    """MDE for one horizon.

    Power is computed per horizon, not once for the experiment, because the MDE depends on
    the baseline rate and horizons can have very different ones. On Cookie Cats day-1
    retention runs at 44.8% and day-7 at 19.0% — reporting a single MDE from whichever
    horizon happened to be listed first would understate what the experiment could see at
    one of them and overstate it at the other.
    """

    horizon_name: str
    horizon_label: str
    result: PowerResult


@dataclass(frozen=True)
class Readout:
    config: ExperimentConfig
    config_path: str
    load_report: LoadReport
    gate: GateDecision
    power: tuple[HorizonPower, ...] | None
    results: tuple[HorizonResult, ...] | None
    peeking: PeekingResult | None
    segments: SegmentationReport | None = None

    def power_for(self, horizon_name: str) -> PowerResult | None:
        for hp in self.power or ():
            if hp.horizon_name == horizon_name:
                return hp.result
        return None

    @property
    def blocked(self) -> bool:
        return not self.gate.estimates_permitted

    def require_results(self) -> tuple[HorizonResult, ...]:
        """Fetch the estimates, or refuse loudly.

        Output surfaces call this instead of reaching for `.results` directly, so a
        surface that forgets to check the gate fails with an explanation rather than
        rendering `None` into a template.
        """
        if self.results is None:
            raise RuntimeError(
                f"readout is blocked by {', '.join(self.gate.blocked_by)} — no effect estimate "
                "was computed, and none may be rendered (SPEC.md §7.3)"
            )
        return self.results

    def primary(self) -> tuple[HorizonResult, ...]:
        return tuple(r for r in self.require_results() if r.is_primary)

    def guardrails(self) -> tuple[HorizonResult, ...]:
        return tuple(r for r in self.require_results() if not r.is_primary)

    def headline(self) -> str:
        return horizon_contrast(self.require_results(), self.config.primary_metric)

    def render_cli(self) -> str:
        """Terminal surface. Same ordering rules as every other surface."""
        out = [
            f"EXPERIMENT READOUT — {self.config.name}",
            "=" * 72,
            "",
            self.load_report.render(),
            "",
            self.gate.render(),
        ]
        if self.blocked:
            out += ["", "No effect estimate computed. Resolve the failure above and re-run."]
            return "\n".join(out)

        out += ["", "Power (computed before results, SPEC.md §7.4)"]
        for hp in self.power:
            out.append(f"  {hp.horizon_label}")
            out.append(hp.result.render())
        if self.peeking:
            out += ["", "Reading protocol (SPEC.md §7.6)", self.peeking.render()]

        out += ["", "Results", "-" * 72]
        for r in self.primary():
            flag = "significant" if r.is_significant else "not distinguishable from noise"
            out.append(
                f"  {r.horizon_label:<28s} {r.control_value:>9.4%} -> {r.treatment_value:>9.4%}"
                f"  {r.relative_text:>10s}  p={r.p_value:.4f}  ({flag})"
            )
            if r.relative_effect is not None:
                out.append(
                    f"  {'':<28s} 95% CI {r.ci_relative.low:+.2%} to "
                    f"{r.ci_relative.high:+.2%} relative"
                )
        if self.gate.warnings:
            out.append("")
            for w in self.gate.warnings:
                out.append(f"  WARNING — {w.label}: {w.headline}")

        out += ["", "Guardrails", "-" * 72]
        for r in self.guardrails():
            out.append(
                f"  {r.horizon_label:<28s} {r.control_value:>9.4f} -> {r.treatment_value:>9.4f}"
                f"  {r.relative_text:>10s}  p={r.p_value:.4f}"
            )
        contrast = self.headline()
        if contrast:
            out += ["", "Horizon contrast", "-" * 72, "  " + contrast]
        if self.config.has_covariates():
            out += ["", render_segments(self.segments)]
        return "\n".join(out)


def _arm_sizes(con: duckdb.DuckDBPyConnection, config: ExperimentConfig) -> tuple[int, int]:
    sizes = dict(con.execute("SELECT variant, count(*) FROM assignments GROUP BY 1").fetchall())
    return sizes.get(config.control, 0), sum(v for k, v in sizes.items() if k != config.control)


def _horizon_power(
    con: duckdb.DuckDBPyConnection, config: ExperimentConfig
) -> tuple[HorizonPower, ...]:
    """MDE at each horizon of the primary metric.

    Reads the *control arm only*. The treatment arm's outcome is never consulted, so the
    power calculation cannot be influenced by the effect it is meant to judge — which is
    the whole reason SPEC.md §7.4 insists it is computed up front rather than after.

    Using the observed control rate as the baseline is standard practice: at design time
    you would use a historical estimate, and the control arm is the closest thing this
    dataset has to one.
    """
    n_control, n_treatment = _arm_sizes(con, config)
    metric = config.primary_metric
    out = []
    for horizon in metric.horizons:
        row = con.execute(
            "SELECT metric_value FROM metric_by_variant"
            " WHERE metric_name = ? AND horizon_name = ? AND variant = ?",
            [metric.name, horizon.name, config.control],
        ).fetchone()
        baseline = row[0] if row else 0.0
        if not 0 < baseline < 1:
            continue
        out.append(HorizonPower(
            horizon_name=horizon.name,
            horizon_label=horizon.label,
            result=minimum_detectable_effect(
                baseline, n_control, n_treatment, practical_effect=config.practical_effect
            ),
        ))
    return tuple(out)


def _describe_loaded(con: duckdb.DuckDBPyConnection, config: ExperimentConfig) -> LoadReport:
    """Reconstruct a report from the contract tables, for callers that loaded elsewhere."""
    counts = con.execute(
        "SELECT (SELECT count(*) FROM assignments), (SELECT count(*) FROM events),"
        " (SELECT count(*) FROM covariates)"
    ).fetchone()
    return LoadReport(
        experiment=config.name,
        source_rows=counts[0],
        assignment_rows=counts[0],
        event_rows=counts[1],
        covariate_rows=counts[2],
        rejects=(),
        mapping_notes="",
    )


def build_readout(
    con: duckdb.DuckDBPyConnection,
    config: ExperimentConfig,
    *,
    load_report: LoadReport | None = None,
    config_path: str = "config/<experiment>.yaml",
    resamples: int = 10_000,
    peeking: bool = True,
    segments: bool = True,
    synthetic_units: bool = False,
) -> Readout:
    """Diagnose, gate, and estimate only if permitted. Assumes the contract is loaded."""
    load_report = load_report or _describe_loaded(con, config)
    gate = evaluate(run_diagnostics(con, config, synthetic_units=synthetic_units), config)

    if not gate.estimates_permitted:
        return Readout(config=config, config_path=config_path, load_report=load_report,
                       gate=gate, power=None, results=None, peeking=None, segments=None)

    results = compute_metrics(con, config, resamples=resamples)
    power = _horizon_power(con, config)
    n_control, n_treatment = _arm_sizes(con, config)

    # The peeking simulation is calibrated to the horizon the decision is actually read
    # at — the last one — since that is the protocol the memo is committing to.
    decision_power = power[-1].result if power else None
    peeking_result = (
        simulate_peeking(min(n_control, n_treatment), decision_power.baseline_rate)
        if peeking and decision_power else None
    )
    return Readout(config=config, config_path=config_path, load_report=load_report,
                   gate=gate, power=power, results=results, peeking=peeking_result,
                   segments=run_segments(con, config) if segments else None)


def run_experiment(
    config_path: str | Path,
    *,
    database: str | Path = ":memory:",
    resamples: int = 10_000,
    peeking: bool = True,
    segments: bool = True,
) -> Readout:
    """Full pipeline from a config path — the one-command entry point (SPEC.md §7.10)."""
    config = load_config(config_path)
    con = duckdb.connect(str(database))
    adapter = get_adapter(config.adapter)
    report = load(con, adapter, config)
    return build_readout(
        con, config, load_report=report, config_path=str(config_path),
        resamples=resamples, peeking=peeking, segments=segments,
        synthetic_units=getattr(adapter, "synthesises_unit_ids", False),
    )


__all__ = ["Readout", "build_readout", "run_experiment", "Status"]
