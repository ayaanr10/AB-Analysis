# Case Study 2 — Criteo Uplift

**What this case study is for:** proving the contract generalises, and running the two
analyses Cookie Cats cannot support.

Case Study 1 is the interesting *finding*. This one is the interesting *engineering claim*.
Cookie Cats has 90,189 rows, five columns, no covariates and no timestamps. Criteo has
13,979,592 rows, sixteen columns, twelve covariates and no unit identifier. If the same
pipeline reads both without modification, "reusable system" is a description. If it needs
edits, it was a script with a config file.

Numbers here are produced by `python -m readout.cli readout --config config/criteo.yaml`.

---

## 1. Did the contract survive?

**Yes, unmodified.** No file in `sql/`, `analysis/` or `readout/` changed to accommodate
this dataset. What was added is exactly what the design says should be added: one adapter
(`adapters/criteo.py`) and one config (`config/criteo.yaml`).

| | Cookie Cats | Criteo |
|---|---|---|
| Source rows | 90,189 | 13,979,592 |
| Assignments | 90,189 | 13,979,592 |
| Events | 147,123 | 1,125,915 |
| Covariate rows | 0 | 167,755,104 |
| Rejected rows | 0 | 0 |
| Unit identifier | real (`userid`) | none, synthesised from row ordinal |
| Intended split | stated 50/50 | inferred 85/15 |
| Covariates | none | 12, anonymised |

Two changes were made to shared code while running this dataset, and neither was to
accommodate Criteo specifically:

- **`metric_unit_values` became a table, and split by horizon mode.** The original query
  grouped 42M joined rows by a string unit id to collapse multiple events per unit, which
  took over ten minutes. Collapsing the events table *first*, while it is still 1.1M rows,
  brings the same result in under 10 seconds. Cookie Cats produces byte-identical output
  either way. This is a performance fix, not a contract change.
- **`relative_effect` became nullable.** See §4 — a guardrail here has a control rate of
  exactly zero, so its relative effect is undefined. The old code crashed on it. That is a
  bug the second dataset exposed rather than caused.

Under the spirit of `SPEC.md` §7.8, which asks for contract changes to be documented as
findings: **there were none.** The two changes above are to the implementation behind the
contract, and both improve Cookie Cats too.

---

## 2. Four of six diagnostics report `n/a`, and that is the honest answer

```
[  n/a] Sample ratio mismatch  observed holdout 15.00%, treated 85.00%; intended ratio is inferred
[  n/a] Duplicate assignments  unit ids are synthesised from row position; duplicates are impossible
[  n/a] Cross-contamination    unit ids are synthesised; a unit cannot span two arms by construction
[ PASS] Orphan events          every event belongs to an assigned unit
[  n/a] Zero-activity units    every event type in this dataset is an outcome being measured
[ PASS] Pre-period balance     worst |SMD| = 0.0488 across 12 covariate(s)
```

This is the part of the case study most worth defending, because the tempting version of
this table is six green ticks.

Criteo ships **no unit identifier**. `unit_id` is the row ordinal, so it is unique by
construction. Duplicate detection and cross-contamination detection cannot fail on this
dataset, whatever is wrong with it. Reporting them as PASS would tell a reader that
something was verified when nothing was. Same for SRM: the 85/15 split is inferred from the
observed counts, so testing those counts against it is circular and cannot reject.

The gate therefore has a fourth state, `NOT_APPLICABLE`, which renders distinctly and
carries its reason (`docs/decisions.md` D-08). **A check that cannot go red is not a check
that passed.**

Worth noticing what this implies about the two datasets. Cookie Cats, the small
"easy" one, is the dataset where the diagnostics do real work: it has a genuine unit id and a
genuine 50/50 design intent, so its SRM check can and nearly does fire (p = 0.0086, a WARN).
Criteo, at 155 times the size, supports fewer meaningful validity checks, not more. Scale
is not integrity.

---

## 3. Pre-period balance, and why it is not a p-value

All 12 covariates are balanced on effect size and all 12 fail on significance:

| | worst | verdict |
|---|---|---|
| Standardised mean difference | 0.0488 (`f3`) | all 12 inside the 0.10 threshold |
| Welch t-test | p < 10⁻³⁰⁰ (`f3`, `f5`, `f6`) | all 12 reject at p < 0.001 |

At n = 14M the test detects imbalances far too small to bias anything. A p-value
implementation of this diagnostic would have blocked a perfectly usable experiment, and
would have looked rigorous doing it. The check is built on |SMD| for that reason, with the
p-value shown as context (`docs/decisions.md` D-04).

The general form of the lesson: **diagnostics whose sensitivity scales with n have to be
built on effect sizes.** The SRM check is the deliberate exception, because there the null
is a real claim about a mechanism rather than an approximation.

---

## 4. The `exposure` guardrail has no relative effect, and that is the point

| treatment | exposure | rows |
|---|---|---|
| 0 (holdout) | 0 | 2,096,937 |
| 1 (treated) | 0 | 11,454,443 |
| 1 (treated) | 1 | 428,212 |

Not one holdout unit is exposed, because an ad cannot be delivered to a holdout. The
control rate is exactly 0, so the relative effect divides by zero and the readout reports
`undefined (control rate is 0)` rather than a number or a crash.

This is the cleanest available demonstration of what "post-treatment" means. `exposure`
does not merely correlate with treatment, it is *definitionally* produced by it. Any
analysis conditioning on it compares treated-and-exposed users against a control group that
cannot contain their counterparts. It is reported as an outcome to show delivery volume
(3.6% of treated units were actually served an ad) and is excluded from every covariate
role: balance, segmentation and CUPED.

Same error class as `sum_gamerounds` in Case Study 1, and much more obvious here. Which is
the useful thing about it: the version of the mistake that is easy to see makes the version
that is hard to see recognisable.

---

## 5. What this dataset does not fix

`SPEC.md` §5.3 listed five selection criteria. Criteo meets criterion 1, which was the
required one, and misses two others. Stated plainly because the write-up should not imply
the second case study solved every gap:

- **Criterion 2, event timestamps: not met.** Outcomes are precomputed flags with no time
  column. Horizons are still declared rather than computed, and the peeking analysis is
  still a simulation. The one thing Case Study 2 was most hoped to add beyond covariates,
  it does not add.
- **Criterion 3, stated split ratio: partly met.** 85/15 is evident, not published.
- **The covariates are anonymised and randomly projected.** Their pre-treatment status
  rests on Criteo's documentation plus a balance check that failed to falsify it. Balance
  can rule out a covariate the treatment moved; it cannot establish when a value was
  recorded. The honest claim is **unfalsified, not established** —
  weaker than what Cookie Cats' `userid`/`version` permit.

That last point also limits what the segmentation can mean, which §6 addresses directly.

---

## 6. Segmentation: method demonstrated, insight deliberately not claimed

Twelve covariates cut into quartiles is 48 comparisons. At α = 0.05, roughly two come back
"significant" on noise alone, so the family is corrected with Benjamini-Hochberg and both
raw and adjusted p-values are reported.

**All 48 survive correction**, which is itself the observation. At 14M units the segment
analysis has so much power that every subgroup shows a detectable effect, so significance
stops discriminating between segments and the multiple-comparison machinery, while correct to
apply, does no filtering. What varies is magnitude: quartile effects range from about +23% to
+47% relative. At this scale the useful question is heterogeneity of effect *size*, not which
segments are "real" — they all are.

That is the opposite of the usual subgroup problem and worth being clear about. Small
experiments need correction because noise masquerades as signal. This one needs effect-size
thresholds because everything is signal.

**And then the result is not interpreted as a product finding, on purpose.** "Uplift is
higher in the top quartile of `f9`" is not a sentence a product team can act on, because
nobody knows what `f9` is, including me. The projection that protects Criteo's users also
destroys the narrative.

So this section demonstrates the *method* — pre-treatment-only splits, global rather than
per-arm quartile boundaries, multiple-comparison discipline — and claims no product insight.
Presenting anonymised quartile effects as a business recommendation would be the more
impressive-looking choice and the less honest one.

A note on one detail that matters more than it looks: quartile boundaries are computed over
the pooled population, not within each arm. Ranking within arm would let "top quartile" mean
a different covariate range in treatment than in control, and the two would no longer be
comparable.

---

## 7. CUPED

CUPED adjusts each unit's outcome by its pre-experiment covariate:

```
Y_adjusted = Y - theta * (X - mean(X)),    theta = cov(X, Y) / var(X)
```

Because X is pre-treatment, this removes variance unrelated to the treatment without moving
the expected difference between arms. The gain is a tighter interval on the same effect. It
does not manufacture significance; it removes noise that was never informative.

Two implementation choices with reasons:

- **theta is estimated on the pooled sample**, not per arm. A per-arm theta would let the
  adjustment differ by arm and could shift the effect estimate, which is exactly what CUPED
  must not do.
- **The reported reduction is in the variance of the estimator**, not in raw outcome
  variance. The latter is a larger number and the wrong one.

### Measured result

Best covariate `f9` (correlation with the outcome +0.4953, theta +0.014932):

| | before | after |
|---|---|---|
| Standard error | 0.000146 | 0.000129 |
| 95% CI width | 0.000574 | 0.000505 |
| Effect estimate | +0.010342 | +0.007859 |

**Variance reduction 22.33%, against a theoretical ceiling of corr² = 24.53%.** Landing just
under the ceiling is what a correct single-covariate implementation should do.

### The estimate moved, and that turned out to be the finding

The effect dropped from +0.010342 to +0.007859. In relative terms the headline went from
**+27.07% to +20.57%** — a quarter of it gone. That looked like a bug. It is not:

```
shift = -theta x (mean_f9[treated] - mean_f9[holdout])
      = -0.014932 x (16.052589 - 15.886253)
      = -2.4838e-03          observed: -2.48e-03
```

The treated arm happened to receive users with slightly higher `f9`, and `f9` positively
predicts visiting, so part of the raw effect was never the treatment. CUPED removes it, and
the adjusted number is the better estimate.

What makes this worth the space: **`f9`'s imbalance is |SMD| = 0.0240, which this system's
own balance check reports as PASS.** A covariate the diagnostic correctly called balanced
still inflated the headline by 24% of its own size. So "balanced enough not to block" and
"balanced enough to ignore" are different claims, and the balance check only establishes the
first. The right response to sub-threshold imbalance is to adjust for it, not to raise the
threshold and not to dismiss it. Full reasoning in `docs/decisions.md` D-14.

It also says something about Case Study 1 by contrast: Cookie Cats has no covariates, so its
estimate cannot be adjusted and this particular error cannot be detected there at all. The
day-7 effect is reported raw because raw is the only option, not because it was checked and
found clean.

`SPEC.md` §12 Q4 asked how far to take CUPED. Resolved as **a demonstration with reported
variance reduction against the corr² ceiling**, not a full treatment of pre-period selection,
because this dataset has no time dimension and therefore no pre-period to select from.

---

## 8. What an interviewer should push on

Written down because the point of the project is to survive being interrogated.

1. *"You can't verify those covariates are pre-treatment."* Correct, and §5 says so. The
   balance check can only falsify. If pressed on why I used the dataset anyway: it was the
   only one of four candidates with unit-level covariates at all, and the alternative was
   dropping segmentation and CUPED entirely.
2. *"Your SRM check is meaningless here."* Also correct, which is why it reports `n/a`
   rather than PASS. The interesting question is what a system should do when a check cannot
   run, and the answer this system gives is: say so.
3. *"48 tests and you found some significant segments. So what?"* So nothing, deliberately.
   See §6.
4. *"Why is the small dataset the one with better diagnostics?"* Because integrity comes
   from how an experiment was instrumented, not from how many rows it has. §2.
