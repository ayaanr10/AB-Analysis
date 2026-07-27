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

## D-08 — Non-informative diagnostics report as such, never as passes **[new]**

*Phase 0, forced by Criteo's missing unit identifier.*

On Criteo, `unit_id` is a synthesised row ordinal, so duplicate-unit and cross-contamination
checks cannot fail by construction. Reporting them as green passes would be misleading: a
check that cannot go red carries no information, and a reader who sees a green check
reasonably infers a real check happened.

The gate therefore has a third state alongside PASS / WARN / BLOCK: **NOT_APPLICABLE**, which
renders distinctly and states why the check could not run.
