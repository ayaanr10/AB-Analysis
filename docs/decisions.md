# Decision Log

Every methodological choice in this repo with a stated reason, in the order it was made.
`SPEC.md` §10 requires that each choice be defensible; this is where the defence lives.

Decisions inherited from `SPEC.md` are restated here with their rationale so a reader does
not have to reconstruct them from the spec. Decisions made during implementation are marked
**[new]** and say what evidence forced them.

---

## D-01 — SRM blocks at p < 0.001, warns at p < 0.05

*Source: `SPEC.md` §7.2. Phase 0.*

A sample ratio mismatch means the assignment mechanism is suspect, and an experiment with a
broken assignment mechanism is not interpretable however good the results look. But at
p < 0.05 a platform running many experiments generates constant false alarms, so 0.001 is the
industry convention and the one used here.

**Observed on Cookie Cats: chi² = 6.9024, p = 0.008608.** That falls between the two
thresholds: it **warns and does not block**. This is the honest outcome and it is reported in
the memo next to the estimate rather than in a footnote — the split really is 49.56/50.44
against an intended 50/50, which is unlikely to be chance across 90,189 users, and a reader
is entitled to weigh that. The experiment is still treated as interpretable because the
imbalance is small in magnitude, affects both horizons identically, and cannot by itself
reverse the sign of the day-7 effect.

## D-02 — SQL does the aggregation; Python does not

*Source: `SPEC.md` §7.5. Phase 0.*

All metric aggregation lives in `sql/`, using CTEs and window functions. Python is restricted
to bootstrap resampling, power calculation, and simulation — three things SQL is genuinely
the wrong tool for.

This is a hard constraint, not a stylistic preference. One of the two portfolio gaps this
project exists to close is "SQL claimed on every resume variant, one SQL artifact in nine
repositories" (`SPEC.md` §2). If aggregation drifts into pandas because it is more
convenient in the moment, the project has failed its primary purpose while still appearing to
work.

## D-03 — BLOCK suppresses effect estimates in every output surface

*Source: `SPEC.md` §7.3. Phase 0.*

A system that hands you a number it does not trust is worse than no system, because the
number gets quoted downstream without its caveat. So the gate is not advisory: when a
diagnostic returns BLOCK, no effect estimate is rendered anywhere — memo, scorecard, or CLI.

The risk in this design is a gate that silently never fires. It is therefore tested with a
deliberately corrupted fixture, and that test was written before the gate (Phase 2).

## D-04 — Pre-period balance is judged on standardised mean difference, not p-values **[new]**

*Phase 0, forced by the Criteo evaluation.*

The obvious implementation of `SPEC.md` §7.2's balance check is a t-test per covariate,
flagging p < 0.001. Running that on Criteo rejects **all 12 covariates**, several at
p < 10⁻³⁰⁰ — while every one of them sits at |SMD| ≤ 0.0488, comfortably inside the
conventional 0.10 threshold.

At n = 14M the test has enough power to detect imbalances far too small to bias anything. A
p-value implementation would have blocked a perfectly usable experiment. The diagnostic
therefore reports **|SMD| against a 0.10 threshold**, and shows the p-value alongside as
context rather than as the decision rule.

Generalisation worth stating: **diagnostics that scale with n must be built on effect sizes.**
The SRM check is the exception, and deliberately so — there the null of "assignment is
correct" is a real mechanism claim, not an approximation, so a p-value is the right test.

## D-05 — Criteo Uplift selected as Case Study 2, with three caveats **[new]**

*Phase 0. Full evaluation in `docs/dataset_selection.md`. Resolves `SPEC.md` §12 Q1.*

Four candidates evaluated against real files. Criteo is the only one with unit-level
pre-treatment covariates, which is the required criterion. ASOS is aggregate-only (no units
exist), Upworthy randomises impressions with no per-user rows, and the Kaggle marketing
dataset's only non-outcome columns describe post-assignment ad delivery.

Accepted caveats: no unit identifier (synthesised from row ordinal, which makes duplicate and
cross-contamination checks non-informative on this dataset), no stated intended split ratio
(85/15 is inferred, so its SRM check is circular and labelled non-diagnostic), and anonymised
randomly-projected covariates (so pre-treatment status is *unfalsified* rather than
*established*, and segmentation demonstrates method rather than product insight).

Checked before selecting, because Phase 5 depends on it: covariate `f9` correlates +0.495
with the `visit` outcome while being balanced across arms, so CUPED has something to work
with. A balanced-but-uncorrelated covariate set would have made the CUPED demonstration
vacuous.

## D-06 — ASOS deferred rather than added as a third path **[new]**

*Phase 0.*

ASOS is the only candidate with a real time dimension — cumulative metric values at day and
half-day checkpoints, published to support adaptive-stopping research. It would upgrade the
peeking analysis (`SPEC.md` §7.6) from a simulation to a demonstration on real sequential
data, which is a genuine improvement.

Declined for now. ASOS cannot be expressed in the two-table contract — it has no units — so
using it means a second analysis path outside the abstraction that the rest of the system is
built on. That is real architectural cost for one chart, and "everything reads from exactly
two tables" (`SPEC.md` §6) is the claim that makes this a system rather than a script.

Recorded rather than dropped: this is the first thing to build if the project is extended.

## D-07 — The Cookie Cats headline is verified, and stated precisely **[new]**

*Phase 0. Resolves `SPEC.md` §12 Q2, which the brief pulled forward from Phase 3.*

Measured on the real file (90,189 rows, sha256 `5ab54d76…`):

| horizon | gate_30 (control) | gate_40 (treatment) | absolute | relative | p |
|---|---|---|---|---|---|
| day 1 | 44.8188% | 44.2283% | −0.5905pp | −1.32% | 0.0744 |
| day 7 | 19.0201% | 18.2000% | −0.8201pp | −4.31% | 0.0016 |

The divergence is real and in the direction §5.1 predicted. **But the precise claim matters,
and one plausible phrasing of it is wrong.**

The day-1 and day-7 effects do **not** have opposite signs. Both point the same way — moving
the gate to level 40 looks worse at both horizons. What flips is whether the effect is
*detectable*: at day 1 the difference is well inside noise (p = 0.074), at day 7 it is not
(p = 0.0016), and the relative effect is more than three times larger.

So the accurate statement of the headline is: **a team reading day-1 retention sees a
neutral result and ships; a team reading day-7 retention sees a real harm and does not.**
The decisions are opposite; the point estimates are not. Every downstream surface uses that
phrasing. `SPEC.md` §13's resume bullet 2 already says "short-horizon metrics scored as
neutral," which is consistent with the data and may be used as written.

## D-09 — Power is computed per horizon, not once per experiment **[new]**

*Phase 3, forced by the Cookie Cats readout.*

`SPEC.md` §7.4 asks for the MDE to be computed from sample size and baseline rate. The
first implementation took the baseline from whichever horizon was listed first in the
config, and reported one MDE for the experiment. That is wrong whenever horizons have
different baselines, and here they differ by a factor of more than two:

| Horizon | Baseline | MDE (relative) | Observed effect | Detectable? |
|---|---|---|---|---|
| Day 1 | 44.82% | 2.07% | 1.32% | no |
| Day 7 | 19.02% | 3.85% | 4.31% | yes, barely |

The MDE is now computed per horizon, and this table is the clearest statement of what
actually happened in this experiment. The day-1 read did not fail to find the effect
because the effect was absent — it failed because **an effect that size was below what
day 1 could resolve at this sample size**. The day-7 read found it with very little room
to spare.

That reframes the headline from "the metric changed between horizons" to something
sharper: the short horizon was never capable of answering the question, and reporting it
as "no significant difference" gives a team false confidence rather than no information.

Worth stating plainly because it cuts against the project's own result: **neither horizon
could reliably detect the 1% effect declared worth acting on.** The harm was found because
it happened to be large. A real but smaller harm would have passed silently through both
reads. That is a criticism of how the experiment was sized, not of what it found, and the
memo says so rather than leaving it for an interviewer to notice.

## D-10 — Zero-activity is non-diagnostic when every event is an outcome **[new]**

*Phase 2, caught by a test that should have passed and didn't.*

The zero-activity check (`SPEC.md` §7.2) looks for units that never showed up at all —
evidence of differential logging, or of the treatment driving units away before they could
act. The first implementation flagged the clean synthetic fixture as a WARN, which was the
test doing its job.

The cause is structural. If the only event type in the data is the outcome being measured,
then "this unit produced no events" means exactly "this unit did not convert" — so any
genuine treatment effect makes the arms differ on zero-activity, and the check fires on
every healthy experiment with a real lift. It cannot distinguish the signal it is looking
for from the signal it is supposed to be independent of.

The check now requires an activity event that is **not** referenced by any configured
metric, and reports NOT_APPLICABLE when there is none. Both real datasets land there:
Cookie Cats for a second reason as well — its adapter emits `game_rounds` for every player,
so zero-activity is 0 by construction rather than by observation.

## D-14 — A covariate can pass the balance check and still bias the estimate **[new]**

*Phase 5, found by running CUPED on Criteo. The most useful thing this project turned up.*

CUPED is supposed to shrink the confidence interval without moving the point estimate. On
Criteo it moved it a lot:

| | absolute | relative |
|---|---|---|
| Raw effect on visit rate | +0.010342 | +27.07% |
| CUPED-adjusted effect | +0.007859 | +20.57% |

A quarter of the headline effect disappeared. That looked like a bug, and it is not. The
shift is exactly what the arithmetic predicts:

```
shift = -theta x (mean_X[treated] - mean_X[holdout])
      = -0.014932 x (16.052589 - 15.886253)
      = -2.4838e-03        observed: -2.48e-03
```

The treated arm happened to get users with slightly higher `f9`, `f9` positively predicts
visiting, so part of the raw effect was never the treatment. CUPED removes it. The adjusted
estimate is the better one.

**Why this matters, and why it complicates D-04.** `f9`'s imbalance is |SMD| = 0.0240 —
comfortably inside the 0.10 threshold, reported as PASS by this system's own balance check.
So a covariate that the diagnostic correctly called balanced still inflated the headline
effect by 24% of its own size. The p-value view of balance, which D-04 rejects, was pointing
at something real.

The resolution is not to switch back to p-values, and it is not to raise the SMD threshold:

- **|SMD| < 0.10 is the right rule for *blocking*.** An experiment with this much imbalance
  is still interpretable, and the decision here (advertising drove a large real increase) is
  identical either way.
- **But the right response to sub-threshold imbalance is to *adjust for it*, not to dismiss
  it.** "Balanced enough not to block" and "balanced enough to ignore" are different claims,
  and the balance check only establishes the first.

So the general rule this project now holds: report the raw effect, report the
covariate-adjusted effect, and treat a gap between them as information about the
randomisation rather than as a problem with the adjustment. Where covariates exist, the
adjusted estimate is the headline. Cookie Cats has no covariates, so its estimate cannot be
adjusted and cannot be checked this way, which is worth knowing about it.

The corollary for the CUPED test suite: asserting "CUPED must not move the estimate" is
wrong. The correct assertion is that any movement equals `-theta x` the covariate imbalance,
which is what `tests/test_segments.py` now checks, on both a balanced and a deliberately
imbalanced fixture.

## D-11 — Generated HTML scorecard rather than Tableau **[new]**

*Phase 6. Resolves `SPEC.md` §12 Q3.*

The scorecard is generated HTML (`readout/html.py`), produced by the same pipeline run that
produces the memo, from the same `Readout` object.

The deciding argument is the blocking rule. `SPEC.md` §7.3 requires that a BLOCK suppresses
effect estimates in **every** output surface. In generated HTML that is one branch, sharing
the gate check with every other surface, and `tests/test_gate.py` asserts it. In Tableau it
would be a calculated field on an extract, which someone can unhide and which goes stale
silently the moment the pipeline changes. A scorecard that can disagree with the analysis
behind it is worse than no scorecard.

**What this costs, stated because it is a real loss.** The argument for Tableau was a
portfolio argument, not a technical one: it would add a second artifact to an existing
Tableau Public profile, which has value for the job this project exists to do (`SPEC.md`
§2). Nothing here recovers that. If the portfolio consideration is judged to outweigh the
integrity argument, the fix is to add a Tableau workbook *alongside* this, reading the same
DuckDB output, rather than replacing it.

§7.9's "parameterized so either experiment loads without rebuilding the workbook" holds in
the form this choice permits: one generator, any config, no edits. Nothing in `html.py`
names a dataset, variant, metric or horizon.

## D-12 — A metric with a zero control rate reports no relative effect **[new]**

*Phase 5, found by running Criteo.*

Criteo's `exposure` guardrail is 0% in the holdout arm, because an ad cannot be delivered to
a holdout. The relative effect therefore divides by zero. The first version of the code
crashed formatting it.

`relative_effect` is now nullable and every surface renders
`undefined (control rate is 0)`. Reporting it that way is more informative than either a
crash or a suppressed row: a metric that is structurally zero in one arm is a metric the
treatment *created*, which is the sharpest possible illustration of post-treatment. It also
means the bootstrap's relative interval is undefined, which the interval reports rather than
returning a silent NaN.

This is the second dataset earning its place: the bug was always in the code, and only a
dataset with a definitionally-zero arm would surface it.

## D-13 — Segmentation on Criteo demonstrates method and claims no insight **[new]**

*Phase 5. Bears on `SPEC.md` §7.8.*

48 comparisons (12 covariates x 4 quartiles), Benjamini-Hochberg corrected across the whole
family, with raw and adjusted p-values both reported and the count of tests stated. At
α = 0.05 about two of the 48 would look significant on noise alone; not saying how many were
examined is how subgroup analyses produce findings that never replicate.

The results are deliberately **not** interpreted as a product finding. Criteo's covariates
are randomly projected, so "uplift is higher in the top quartile of `f9`" is not actionable
by anyone, including me. The same anonymisation that makes the dataset publishable destroys
the narrative. Presenting anonymised quartile effects as a business recommendation would look
more impressive and be less honest.

One detail worth defending: quartile boundaries are computed over the pooled population
rather than within each arm. Ranking within arm would make "top quartile" a different
covariate range in treatment than in control, and the segments would no longer be comparable
across the thing being compared.

## D-08 — Non-informative diagnostics report as such, never as passes **[new]**

*Phase 0, forced by Criteo's missing unit identifier.*

On Criteo, `unit_id` is a synthesised row ordinal, so duplicate-unit and cross-contamination
checks cannot fail by construction. Reporting them as green passes would be misleading: a
check that cannot go red carries no information, and a reader who sees a green check
reasonably infers a real check happened.

The gate therefore has a third state alongside PASS / WARN / BLOCK: **NOT_APPLICABLE**, which
renders distinctly and states why the check could not run.
