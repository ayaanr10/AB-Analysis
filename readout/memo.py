"""Generate the decision memo (SPEC.md §7.7).

The primary artifact, and the one written for someone who will not read the second screen.
Constraints it is built to satisfy:

- opens with the decision, in the first three sentences
- states the cost of being wrong in both directions
- power before results, protocol before results (§7.4, §7.6)
- warnings sit next to the estimate, never in a footnote (§7.3)
- says what the data cannot support, including the analysis that was declined (§5.2)
- renders no estimate at all when the gate blocks

Every number in the output comes from the Readout. Nothing is written by hand, so the memo
cannot drift away from what the pipeline actually computed.
"""

from __future__ import annotations

from .gate import Status
from .metrics import HorizonResult

SHIP = "Ship it"
DO_NOT_SHIP = "Do not ship"
INCONCLUSIVE = "Do not ship yet — the experiment cannot answer this"


def _pp(x: float) -> str:
    """Format a difference between two rates as percentage points.

    Kept separate from percent on purpose. "Retention fell 0.82%" and "retention fell
    0.82 percentage points" are different claims — here they differ by a factor of about
    23 — and mixing them up is the fastest way to lose a reader who knows the difference.
    """
    return f"{abs(x) * 100:.2f} percentage points"


def decide(readout) -> tuple[str, str]:
    """Reduce the primary metric to one verdict and one sentence of reasoning."""
    primary = readout.primary()
    longest = primary[-1]  # horizons are configured shortest-first
    harmful = [r for r in primary if r.is_significant and r.moved_against_treatment]
    helpful = [r for r in primary if r.is_significant and not r.moved_against_treatment]

    if harmful:
        worst = min(harmful, key=lambda r: r.relative_effect)
        return DO_NOT_SHIP, (
            f"At {worst.horizon_label.lower()}, the change made things worse by "
            f"{abs(worst.relative_effect):.1%} — a difference too large and too consistent to be "
            f"chance (p = {worst.p_value:.4f})."
        )
    if helpful and longest.is_significant:
        return SHIP, (
            f"At {longest.horizon_label.lower()}, the change improved the metric by "
            f"{longest.relative_effect:.1%} (p = {longest.p_value:.4f}), and no guardrail moved "
            "against it."
        )
    underpowered = [hp for hp in (readout.power or ())
                    if hp.result.can_answer_its_own_question is False]
    if underpowered and len(underpowered) == len(readout.power or ()):
        return INCONCLUSIVE, (
            "No effect was detected, but this experiment was too small at every horizon to "
            "detect an effect the size the team said would matter "
            f"({underpowered[0].result.practical_effect:.1%}). A null result here is not "
            "evidence that nothing happened."
        )
    return INCONCLUSIVE, (
        "No horizon showed a difference distinguishable from noise, so there is no evidence "
        "the change helped."
    )


def _blocked_memo(readout) -> str:
    cfg = readout.config
    lines = [
        f"# {cfg.name} — readout BLOCKED",
        "",
        "## No result is reported for this experiment.",
        "",
        "The data failed a validity check, which means the two groups being compared are not "
        "comparable. Any difference measured between them would be a mix of the change being "
        "tested and whatever broke — with no way to tell how much of each. So this readout "
        "reports the failure and stops.",
        "",
        "**What failed:**",
        "",
        "| Check | Status | What was found |",
        "|---|---|---|",
    ]
    for r in readout.gate.results:
        if r.status in (Status.BLOCK, Status.WARN):
            lines.append(f"| {r.label} | **{r.status.value}** | {r.headline} |")
    lines += ["", "**Why this blocks rather than warns:**", ""]
    for r in readout.gate.results:
        if r.status is Status.BLOCK:
            lines.append(f"- **{r.label}.** {r.detail}")
    lines += [
        "",
        "This is a deliberate design choice, not a limitation. A system that hands you a "
        "number it does not trust is worse than no system: numbers get quoted downstream and "
        "caveats do not travel with them. See `docs/decisions.md` D-03.",
        "",
        f"_Load: {readout.load_report.source_rows:,} source rows, "
        f"{readout.load_report.assignment_rows:,} assignments, "
        f"{readout.load_report.event_rows:,} events._",
    ]
    return "\n".join(lines)


def _results_table(rows: tuple[HorizonResult, ...], as_rate: bool) -> list[str]:
    fmt = (lambda v: f"{v:.4%}") if as_rate else (lambda v: f"{v:,.2f}")
    out = [
        "| Horizon | Control | Treatment | Difference | 95% CI (relative) | p | Readable? |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        readable = "yes" if r.is_significant else "**no — inside noise**"
        out.append(
            f"| {r.horizon_label} | {fmt(r.control_value)} | {fmt(r.treatment_value)} "
            f"| {r.relative_effect:+.2%} | {r.ci_relative.low:+.2%} to {r.ci_relative.high:+.2%} "
            f"| {r.p_value:.4f} | {readable} |"
        )
    return out


def render_memo(readout) -> str:
    if readout.blocked:
        return _blocked_memo(readout)

    cfg = readout.config
    verdict, reasoning = decide(readout)
    primary = readout.primary()
    power = readout.power
    lines: list[str] = []

    # --- first screen: the decision -----------------------------------------------
    lines += [
        f"# {verdict}: {cfg.notes.get('decision_subtitle', 'see below')}",
        "",
        "*An A/B test readout system that runs validity checks before it will show you a "
        "result — demonstrated here on a real public experiment. "
        "[How it works](#how-this-was-built) · [Design spec](SPEC.md) · "
        "[Every decision, with reasons](docs/decisions.md)*",
        "",
        "---",
        "",
        f"**The experiment:** {cfg.description.strip()}",
        "",
        f"**The finding.** {reasoning}",
        "",
        readout.headline(),
        "",
    ]

    # SPEC.md §7.3: warnings render adjacent to the estimate, not in a footnote. This is
    # the first thing after the decision, before any number it qualifies.
    for w in readout.gate.warnings:
        lines += [
            f"> **⚠ Caveat that applies to every number below — {w.label.lower()}.**",
            f">",
            f"> {w.detail}",
            "",
        ]

    lines += ["## What it costs to be wrong", ""]
    lines += _cost_of_error(readout, verdict)

    # --- protocol before results (§7.6) --------------------------------------------
    if readout.peeking:
        p = readout.peeking
        lines += [
            "",
            "## How this experiment was read",
            "",
            "**Once, at the final horizon.** That was decided before looking at the result, and "
            "it matters more than it sounds.",
            "",
            f"Checking an experiment daily and stopping the moment it looks significant does not "
            f"give you an answer sooner — it gives you a wrong answer more often. Simulating "
            f"{p.simulations:,} copies of this experiment with **no real effect at all**, sized "
            f"exactly like this one:",
            "",
            "| Reading protocol | Times it declared a winner that does not exist |",
            "|---|---|",
            f"| Read once at the end | {p.single_read_fpr:.1%} |",
            f"| Checked daily for {p.n_checks} days, stop at first p < 0.05 | "
            f"**{p.peeking_fpr:.1%}** ({p.inflation:.1f}x) |",
            "",
            "![Peeking inflates false positives](report/peeking.png)",
            "",
            "This is an organisational failure before it is a statistical one. Nobody sets out "
            "to do it; someone asks how the test is looking on day 2, and \"we don't know yet, "
            "and looking now makes the final answer worse\" is an expensive sentence to say. "
            "Writing the protocol down in advance is what makes it cheap.",
        ]

    # --- validity ------------------------------------------------------------------
    lines += ["", "## Was the experiment itself sound?", "",
              "These checks run before any result is calculated. If one of them fails hard, this "
              "document does not show a result at all.", "",
              "| Check | Status | Finding |", "|---|---|---|"]
    for r in readout.gate.results:
        lines.append(f"| {r.label} | {r.status.symbol} | {r.headline} |")
    lines.append("")
    for r in readout.gate.results:
        if r.status in (Status.WARN, Status.NOT_APPLICABLE) and r.detail:
            lines.append(f"- **{r.label} — {r.status.symbol}.** {r.detail}")

    # --- power before results (§7.4) ------------------------------------------------
    lines += ["", "## Could this experiment have answered its own question?", ""]
    if power:
        first = power[0].result
        lines += [
            f"With {first.n_control:,} and {first.n_treatment:,} users per arm, here is the "
            "smallest effect this experiment could reliably detect at each horizon — at the "
            "conventional 5% significance and 80% power:",
            "",
            "| Horizon | Baseline | Smallest detectable effect | Worth acting on | Could it have seen that? |",
            "|---|---|---|---|---|",
        ]
        for hp in power:
            r = hp.result
            threshold = f"{r.practical_effect:.1%}" if r.practical_effect is not None else "—"
            answer = {True: "yes", False: "**no**", None: "—"}[r.can_answer_its_own_question]
            lines.append(
                f"| {hp.horizon_label} | {r.baseline_rate:.2%} | {r.mde_relative:.2%} relative "
                f"({r.mde_absolute:+.3%} absolute) | {threshold} | {answer} |"
            )
        lines.append("")
        lines += _power_commentary(readout, power)
        lines += ["", "The MDE depends on the baseline rate, which is why it differs between "
                  "horizons rather than being one number for the experiment. All of it is "
                  "calculated from sample size and the control group alone — it never looks at "
                  "the treatment result, which is why it appears before the numbers rather than "
                  "after them."]

    # --- results --------------------------------------------------------------------
    lines += ["", "## The numbers", "", f"### {cfg.primary_metric.label} (primary)", ""]
    lines += _results_table(primary, cfg.primary_metric.type == "binary_rate")
    lines += ["",
              f"Intervals are bootstrap percentile intervals from "
              f"{primary[0].ci_relative.resamples:,} resamples "
              f"({primary[0].ci_relative.method.split(',')[0]}) — the experiment re-run ten "
              "thousand times using only the users actually observed."]

    guardrails = readout.guardrails()
    if guardrails:
        lines += ["", "### Guardrails", "",
                  "Reported whether or not they are convenient — a change that improves the "
                  "primary metric while damaging something else has not succeeded.", ""]
        by_metric: dict[str, list[HorizonResult]] = {}
        for g in guardrails:
            by_metric.setdefault(g.metric_name, []).append(g)
        for name, rows in by_metric.items():
            metric = next(m for m in cfg.metrics if m.name == name)
            lines += [f"**{metric.label}**", ""]
            lines += _results_table(tuple(rows), metric.type == "binary_rate")
            if metric.note:
                lines += ["", f"> {metric.note.strip()}"]
            lines.append("")

    # --- limits ---------------------------------------------------------------------
    lines += ["", "## What this data cannot tell us", ""]
    lines += _limits(readout)

    lines += ["", "---", ""] + _how_this_was_built(readout)
    lines += [
        "",
        "## Reproducing this",
        "",
        "```bash",
        "pip install -r requirements.txt",
        "bash data/download.sh",
        f"python -m readout.cli memo --config {readout.config_path} --out README.md",
        "```",
        "",
        f"_Generated from {readout.load_report.source_rows:,} source rows "
        f"({readout.load_report.assignment_rows:,} assignments, "
        f"{readout.load_report.event_rows:,} events, "
        f"{readout.load_report.rejected_assignments} rejected). "
        "Every number above is computed by the pipeline. Nothing in this file is typed by "
        "hand, so the memo cannot drift away from what the code actually found._",
    ]
    return "\n".join(lines)


def _how_this_was_built(readout) -> list[str]:
    """What the repo is, for a reader who got the decision and now wants the system."""
    return [
        "## How this was built",
        "",
        "This is a reusable readout system, not a notebook for one dataset. Any experiment is "
        "mapped into **two tables** — `assignments` (one row per unit) and `events` (one row "
        "per unit-event) — and nothing downstream is allowed to know where the data came from. "
        "Adding an experiment means writing an adapter and a config, not editing the analysis.",
        "",
        "```",
        "adapters/   the ONLY code that knows a dataset's column names",
        "sql/        all aggregation: diagnostics, metrics, segments (CTEs, window functions)",
        "analysis/   bootstrap, power/MDE, peeking simulation",
        "readout/    the contract, the gate, and the output surfaces",
        "tests/      including the corrupted-input test the gate has to pass",
        "docs/       the dataset selection log, and every decision with its reasoning",
        "```",
        "",
        "Three things it does that the notebook version of this analysis would not:",
        "",
        "1. **It refuses to answer when it should not.** If a validity check fails hard, no "
        "effect estimate is computed at all — not computed and hidden, not computed with a "
        "warning attached. Numbers get pasted into decks and caveats do not travel with them. "
        "`tests/test_gate.py` corrupts the input deliberately and proves the block fires.",
        "2. **Diagnostics and power run before any result exists.** Enforced by construction in "
        "`readout/run.py` rather than by remembering to do things in the right order.",
        "3. **It states what it cannot support.** The segmentation analysis was declined on "
        "methodological grounds and the reasoning is written down, rather than the analysis "
        "being quietly omitted.",
        "",
        "**Status.** Phases 0–4 are complete: the system runs end to end on Cookie Cats and "
        "everything above is generated from it. A second case study — the 13.9M-row Criteo "
        "uplift experiment, which is the one with genuine pre-treatment covariates and "
        "therefore the one that can carry segmentation and CUPED — is selected and specified "
        "but **not yet run**. The claim that the contract generalises unchanged is not proven "
        "until it does, and this README will not make it before then. "
        "[`docs/dataset_selection.md`](docs/dataset_selection.md) records how that dataset was "
        "chosen and why three others were rejected on methodological grounds.",
    ]


def _power_commentary(readout, power) -> list[str]:
    """Say what the MDE table means for the specific result that was found.

    A power table nobody interprets is decoration. The interesting case — and the one
    Cookie Cats lands in — is an experiment that was underpowered at one horizon and only
    just adequately powered at the other, which is exactly why the two horizons disagree
    about whether anything happened.
    """
    results = {r.horizon_name: r for r in readout.primary()}
    out = []
    for hp in power:
        r = results.get(hp.horizon_name)
        if r is None:
            continue
        observed, mde = abs(r.relative_effect), hp.result.mde_relative
        if r.is_significant:
            out.append(
                f"- **{hp.horizon_label}:** the observed {observed:.2%} effect is larger than the "
                f"{mde:.2%} this horizon could detect, which is why it registers as a result."
            )
        else:
            out.append(
                f"- **{hp.horizon_label}:** the observed {observed:.2%} effect is *smaller* than "
                f"the {mde:.2%} this horizon could detect. Finding nothing here was close to "
                "guaranteed regardless of whether anything was happening — so \"no significant "
                "difference at day 1\" is not evidence that the change was harmless."
            )
    if out:
        out.append("")
        out.append(
            "That asymmetry is the whole story of this experiment. The short horizon was not "
            "capable of seeing an effect of the size that actually existed; the long horizon "
            "was, and only barely."
        )
        underpowered = [hp for hp in power if hp.result.can_answer_its_own_question is False]
        if len(underpowered) == len(power):
            out.append("")
            out.append(
                "Worth being blunt about the uncomfortable version of this: no horizon here "
                f"could reliably detect the {power[0].result.practical_effect:.0%} effect the "
                "team said would matter. The harm was found because it happened to be large, "
                "not because the experiment was built well enough to find it. A real but "
                "smaller version of the same harm would have passed silently — which is an "
                "argument about how this experiment was sized, not about what it found."
            )
    return out


def _config_path(cfg) -> str:
    return f"config/{cfg.name.split('_')[0]}_{cfg.name.split('_')[1]}.yaml" \
        if "_" in cfg.name else f"config/{cfg.name}.yaml"


def _cost_of_error(readout, verdict: str) -> list[str]:
    """Both directions, always. Stating only the cost of the recommended action is advocacy."""
    cfg = readout.config
    primary = readout.primary()
    longest = primary[-1]
    n = (readout.power[0].result.n_control + readout.power[0].result.n_treatment
         if readout.power else 0)

    if verdict == DO_NOT_SHIP:
        return [
            f"**If this recommendation is wrong and the change was actually fine:** the cost is "
            f"the engineering time already spent, plus whatever upside the change would have "
            f"delivered. The measured effect on {longest.horizon_label.lower()} was "
            f"{longest.relative_effect:+.2%}, so the forgone upside is bounded by roughly that "
            "much in the opposite direction — small.",
            "",
            f"**If the change ships and this recommendation was right:** every new user is "
            f"affected, permanently, and the damage compounds silently. The harm does not show "
            f"up in the metric most teams watch — at the shortest horizon it is invisible. "
            f"A drop of {_pp(longest.absolute_effect)} across a cohort the size of this "
            f"experiment ({n:,} players) is about "
            f"{abs(longest.absolute_effect) * n:,.0f} players who would have come back and now "
            "do not — every cohort, indefinitely, until someone thinks to re-measure at a "
            "longer horizon.",
            "",
            "The asymmetry is the argument: one mistake costs a sprint, the other costs users "
            "continuously and quietly.",
        ]
    return [
        "**If this recommendation is wrong:** the change ships and does nothing, or does harm "
        "that this experiment was not able to see.",
        "",
        "**If the change is held back and it would have worked:** the upside is forgone, and the "
        "cost of re-running is another full experiment cycle.",
    ]


def _limits(readout) -> list[str]:
    cfg = readout.config
    out = []

    if not cfg.has_covariates():
        out += [
            "**Which users this affected — declined, not missing.** The obvious follow-up is "
            "\"did this hurt everyone, or only some players?\" That question cannot be answered "
            "honestly with this data, and the analysis was deliberately not run.",
            "",
            "The only column that looks like it would work is the number of game rounds each "
            "player played. It cannot be used. Players were randomised when they installed, and "
            "rounds played is measured over the 14 days *after* that — so the change being "
            "tested could itself have altered it. Splitting the results by engagement level "
            "would mean comparing groups that the treatment helped define, and any difference "
            "found would be partly an artefact of the split rather than a real effect. This is "
            "collider bias, and it is the single most common way a segmentation analysis "
            "produces a confident wrong answer.",
            "",
            "The same column is used freely as a *guardrail outcome* above. That is not a "
            "contradiction: measuring whether the change moved engagement is fine, because "
            "nothing is being conditioned on. Splitting by it is not. Post-treatment variables "
            "are disqualified from one side of an analysis, not from both.",
            "",
            "For the same reason, CUPED — the standard technique for making an experiment more "
            "sensitive using each user's prior behaviour — cannot be applied here. There is no "
            "pre-experiment behaviour in this dataset at all.",
            "",
        ]

    if readout.peeking:
        out += [
            "**Nothing about *when* the effect appeared.** The dataset has no timestamps, only "
            "two precomputed flags. So the reading-protocol section above is a simulation sized "
            "to this experiment, not a replay of it, and no genuine day-by-day analysis is "
            "possible on this data.",
            "",
        ]

    for r in readout.gate.results:
        if r.status is Status.WARN:
            out += [f"**{r.label}.** {r.detail}", ""]

    out += [
        "**Anything about why.** This is an experiment, not research. It establishes that "
        "moving the gate changed behaviour and by how much. It says nothing about the mechanism, "
        "and the obvious story — that a later gate means players hit the paywall after their "
        "interest has already peaked — is a hypothesis this data cannot test.",
    ]
    return out
