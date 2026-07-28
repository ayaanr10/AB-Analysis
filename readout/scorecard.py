"""Scorecard surface (SPEC.md §7.9).

A dense side-by-side view for someone who already knows what an experiment is — the memo's
counterpart, not its summary. Renders to text here; readout/html.py wraps the same data
for the published version, so both surfaces share one gate check and cannot disagree about
whether a result may be shown.

Diagnostics are printed above the metrics, and visually first, because a scorecard whose
validity panel is below the fold is a scorecard whose validity panel does not exist.
"""

from __future__ import annotations

from .gate import Status


def render_scorecard(readout) -> str:
    cfg = readout.config
    width = 78
    out = [
        "=" * width,
        f" SCORECARD — {cfg.name}",
        "=" * width,
        "",
        " DIAGNOSTICS",
        " " + "-" * (width - 2),
    ]
    label_width = max(len(r.label) for r in readout.gate.results)
    for r in readout.gate.results:
        out.append(f"  [{r.status.symbol:>5s}]  {r.label:<{label_width}s}  {r.headline}")

    out += ["", f" GATE: {readout.gate.status.value}"]

    if readout.blocked:
        out += [
            " " + "-" * (width - 2),
            "",
            "  Effect estimates are suppressed in this scorecard.",
            f"  Blocking checks: {', '.join(readout.gate.blocked_by)}.",
            "",
            "  The comparison between these arms is not interpretable, so no number is",
            "  shown here rather than shown with a caveat attached (SPEC.md §7.3).",
            "=" * width,
        ]
        return "\n".join(out)

    out += [
        "",
        " POWER  (before results — SPEC.md §7.4)",
        " " + "-" * (width - 2),
        f"  {'horizon':<26s}{'baseline':>11s}{'MDE (rel)':>12s}{'powered?':>12s}",
    ]
    for hp in readout.power:
        r = hp.result
        powered = {True: "yes", False: "NO", None: "n/a"}[r.can_answer_its_own_question]
        out.append(
            f"  {hp.horizon_label[:24]:<26s}{r.baseline_rate:>11.2%}"
            f"{r.mde_relative:>12.2%}{powered:>12s}"
        )
    out += [
        "",
        " METRICS",
        " " + "-" * (width - 2),
        f"  {'horizon':<26s}{'control':>11s}{'treatment':>12s}{'effect':>10s}{'p':>9s}",
    ]
    for r in readout.require_results():
        is_rate = r.metric_type == "binary_rate"
        fmt = (lambda v: f"{v:.4%}") if is_rate else (lambda v: f"{v:,.2f}")
        marker = "*" if r.is_significant else " "
        tag = "" if r.is_primary else "  (guardrail)"
        out.append(
            f"  {r.horizon_label[:24]:<26s}{fmt(r.control_value):>11s}"
            f"{fmt(r.treatment_value):>12s}{r.relative_text:>10s}"
            f"{r.p_value:>8.4f}{marker}{tag}"
        )
        if r.relative_effect is not None:
            out.append(
                f"  {'':<26s}{'95% CI':>11s} {r.ci_relative.low:+.2%} to {r.ci_relative.high:+.2%}"
            )

    if readout.gate.warnings:
        out += ["", " WARNINGS (shown with the metrics, not beneath them)",
                " " + "-" * (width - 2)]
        for w in readout.gate.warnings:
            out.append(f"  {w.label}: {w.headline}")

    out += ["", "=" * width]
    return "\n".join(line for line in out if line != "")
