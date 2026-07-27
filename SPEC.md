# Experiment Readout System — Product Specification

**Status:** Draft v1, not yet started
**Owner:** Shreyas Jain
**Type:** Portfolio project (personal, not commercial, no client or employer involved)
**Last updated:** 25 July 2026

---

## 0. How to read this document

This spec is written to be self-contained. A reader — human or LLM — should be able to
build this project from this document alone, without asking follow-up questions and
without access to the conversation that produced it.

- **§1–3** explain what this is and why, in plain English. No statistics background needed.
- **§4** is a glossary. Every technical term used later is defined there. Read it before §7.
- **§5–6** define the data and the core abstraction.
- **§7–11** are the actual requirements, phasing, and acceptance criteria.
- **§12** lists what is genuinely unresolved. Do not invent answers to these.

**If you are an LLM picking this up:** everything you need is here, but three things are
deliberately *not* settled and are marked as blocking in §12. Do not fabricate a dataset,
a benchmark figure, or a result. Where this document says "verify on pull," it means the
number is an expectation from memory and has not been confirmed against the real file.

---

## 1. Plain-English summary

When a company wants to know whether a product change actually helped, it runs an
**A/B test**: show the old version to half of users, the new version to the other half,
and compare. This sounds simple and is very easy to get wrong. Teams routinely ship
changes that did nothing, or kill changes that would have worked, because the analysis
was done badly.

This project builds a small **system that reads out A/B tests correctly** — it takes the
raw data from an experiment and produces a trustworthy verdict, while automatically
checking for the ways experiments commonly break. It then runs that system on two real
public experiment datasets and writes up what it found as a decision memo.

The headline finding it is built around: **on one real experiment, measuring after one day
and measuring after seven days point to opposite decisions.** A team that checked the fast
metric would have confidently shipped a change that the slower metric shows was harmful.

Two words that will recur:

- **Variant** — one of the versions being compared (e.g. "old" vs "new").
- **Metric horizon** — how long after the experiment starts you measure the outcome.
  Day-1 retention and day-7 retention are the same metric at two different horizons.

## 2. Why this project exists

This is a portfolio project with a specific job to do. It is not speculative product work.

**The gap it closes.** Shreyas is a final-year B.Tech student (Delhi Technological
University, graduating July 2027) applying to Product Analyst, Data Analyst, and GenAI
roles. Two concrete problems exist in the current portfolio:

1. **SQL is claimed but not evidenced.** SQL appears on all three resume variants and on
   every target job description. There is not a single `.sql` file in any existing project.
2. **Experimentation is a stated requirement with zero evidence.** The Eternal Product
   Analyst job description lists *"understanding of customer experience, product metrics,
   experimentation frameworks, and A/B testing methodologies"* as a requirement. Nothing in
   the portfolio speaks to it.

A third, softer gap: the same JD asks candidates to *"drive automation and build scalable
reporting/monitoring solutions wherever possible."* A one-off analysis does not answer
that. A reusable system does.

**The connection to prior work.** An earlier project (a consumer research teardown of
JioHotstar) found that subscriber churn was **silent dormancy rather than cancellation** —
users stop opening the app without ever formally leaving. That finding has a measurement
consequence that this project makes concrete: *a dormant user and an active user look
identical at short horizons.* Any experiment on retention that is read at 24 hours is
blind to the exact failure mode. This project demonstrates that risk on real data.

**Why not just a Kaggle notebook.** The standard portfolio version of this project — load
CSV, run a chi-square test, report p < 0.05 — exists thousands of times over and is
actively negative signal. The differentiating work is the *diagnostics before the result*
and the *decision after it*, not the test statistic.

## 3. Who this is for

| Audience | What they need from it |
|---|---|
| **Recruiter / screener** (non-technical, ~20 seconds) | A README whose first screen states the decision reached and why, in plain language. |
| **Hiring manager** (technical, ~5 minutes) | Evidence of experimentation judgment: power analysis done up front, validity checks that can block a result, awareness of what the data cannot support. |
| **Interviewer** (deep, 30+ minutes) | Something to interrogate. Every methodological choice must be defensible and written down. |
| **Shreyas, in an interview** | A concrete story with a decision, a trade-off, and a thing he chose *not* to do for a stated reason. |

---

## 4. Glossary

Read this before §7. Terms are ordered so each builds on the previous.

**Unit** — the thing being randomized. Usually a user. Every unit is assigned to exactly
one variant.

**Variant / arm** — one version in the comparison. Conventionally `control` (existing
experience) and `treatment` (the change). An experiment may have more than two.

**Assignment** — the record of which unit got which variant, and when.

**Exposure** — whether a unit actually encountered the thing being tested. A unit can be
assigned but never exposed (assigned to see a new checkout page, then never reaches
checkout). Analyzing assigned-but-unexposed units dilutes the measured effect.

**Metric horizon** — the time window over which an outcome is measured. "Day-7 retention"
means: did this unit return within 7 days of assignment. The same experiment can produce
different verdicts at different horizons. **This is the central concept of this project.**

**Pre-treatment variable** — an attribute of a unit known *before* randomization (country,
device, signup date, prior activity). Safe to segment and analyze by.

**Post-treatment variable** — anything measured *after* assignment. It may itself have been
changed by the treatment.

**Collider bias / conditioning on a post-treatment variable** — the error of splitting
results by something the treatment could have affected. If you compare variants *only among
highly-engaged users*, and the treatment changed who becomes highly engaged, the two groups
you are comparing are no longer comparable — randomization is broken. **Segment only on
pre-treatment variables.** This constraint has already bitten this project once; see §5.2.

**Sample Ratio Mismatch (SRM)** — when the observed split between variants differs from the
intended split by more than chance. A test intended to be 50/50 that lands at 50.8/49.2
across 90,000 users is not random noise; it means something is wrong with assignment,
logging, or filtering. **An experiment with SRM is not interpretable, regardless of how
good the results look.** Detected with a chi-squared goodness-of-fit test against the
intended ratio.

**Statistical power** — the probability that the experiment detects a real effect of a given
size, if that effect exists. Low power means a "no significant difference" result tells you
nothing — the experiment could not have found the effect either way.

**Minimum Detectable Effect (MDE)** — the smallest true effect the experiment had a
realistic chance of detecting, given its sample size. Computed *before* looking at results.
If the MDE is larger than the effect the business would care about, the experiment was
unable to answer its own question.

**Bootstrap confidence interval** — a way of estimating uncertainty by resampling the
observed data many times (typically 10,000) and observing how much the result varies. Makes
fewer assumptions than a formula-based interval and is easy to explain to non-statisticians.

**Peeking** — repeatedly checking an experiment's results while it runs and stopping as soon
as it looks significant. This dramatically inflates the false-positive rate: checking daily
for two weeks and stopping at the first p < 0.05 produces a false positive far more often
than the nominal 5%. It is fundamentally an *organizational* problem — a stakeholder asks
"how's the test looking?" on day 2 — not a purely statistical one.

**CUPED** (Controlled-experiment Using Pre-Experiment Data) — a variance reduction technique
that uses each unit's *pre-experiment* behaviour as a covariate, making the experiment more
sensitive without needing more users. Requires genuine pre-treatment data. Not applicable to
every dataset; see §5.2.

**Readout / scorecard** — the standard output summarizing an experiment: variant metrics,
effect size, confidence interval, diagnostics, and a recommendation.

---

## 5. Data

### 5.1 Case Study 1 — Cookie Cats (confirmed as the primary dataset)

A mobile puzzle game ran an experiment moving a progression gate (a forced pause point)
from level 30 to level 40. Publicly available on Kaggle, widely mirrored.

**Expected schema — VERIFY ON PULL. These values are from memory and must be confirmed
against the actual file before any of them are used in a written conclusion:**

| Column | Type | Meaning |
|---|---|---|
| `userid` | integer | Unique player ID |
| `version` | string | `gate_30` (control) or `gate_40` (treatment) |
| `sum_gamerounds` | integer | Game rounds played in the first 14 days after install |
| `retention_1` | boolean | Did the player return 1 day after installing |
| `retention_7` | boolean | Did the player return 7 days after installing |

Approximately 90,189 rows, split roughly 44,700 / 45,489 between variants.

**Why this dataset.** Two properties make it the right vehicle:

1. **The decision flips on horizon.** Day-1 retention is expected to show little or no
   meaningful difference between variants. Day-7 retention is expected to favour the
   original gate (`gate_30`), i.e. moving the gate later appears *harmful*. A team reading
   the fast metric ships the wrong thing. **Both of these expectations must be verified
   against the real data — do not write them up as findings until confirmed.**
2. **The split is not exactly even**, which makes the SRM check a genuine decision point
   rather than a formality. See §7.2 for how the threshold choice is handled.

### 5.2 Known constraints of Cookie Cats

These are settled findings. Do not re-litigate them, and do not design around them being
false.

**`sum_gamerounds` is post-treatment and must not be used for segmentation.** It measures
activity in the 14 days *after* install, and players are randomized at install. Splitting
results by engagement level therefore conditions on an outcome of the treatment and biases
the estimate (collider bias, see §4). It is also unusable as a CUPED covariate for the
same reason.

**Consequence:** Cookie Cats has effectively **no pre-treatment covariates** (only `userid`
and `version`). Heterogeneous treatment effect analysis and CUPED are *not possible* on
this dataset. This is not a gap to work around — the write-up should state it explicitly.
Documenting a correctly-declined analysis is stronger evidence of judgment than producing a
biased one.

**There is no time dimension.** `retention_1` and `retention_7` are booleans, not
timestamped events. A true sequential/peeking analysis cannot be run on this data; the
peeking demonstration must be a simulation (see §7.5).

### 5.3 Case Study 2 — RESOLVED in Phase 0: Criteo Uplift

> **Update, 27 July 2026.** This section was written as UNRESOLVED / BLOCKING. It is now
> resolved. Four candidates were evaluated against their real files; the **Criteo Uplift
> Prediction Dataset** was selected as the only one with unit-level pre-treatment
> covariates. Full evaluation, including the three rejections and the caveats accepted,
> is in `docs/dataset_selection.md`. The criteria below are left as originally written —
> they are what the evaluation was run against.

A second dataset is required to demonstrate that the system generalizes beyond the
experiment it was designed against.

**Selection criteria, in priority order:**

1. **Genuine pre-treatment covariates** (required) — unlocks the segmentation and CUPED work
   that Cookie Cats cannot support.
2. **Event-level timestamps** (strongly preferred) — enables a *real* sequential analysis
   rather than a simulated one, and lets metric horizon be computed rather than hardcoded.
3. Clearly documented randomization and a stated intended split ratio.
4. Openly licensed and stable enough to link from a public repo.
5. Large enough that the SRM check is meaningful (order 10⁴+ units).

Candidate families to evaluate (**schemas unverified — check before committing**): the
Criteo uplift modelling dataset, and the various marketing/advertising A/B datasets on
Kaggle. Neither has been inspected.

**Risk:** if no dataset meeting criterion 1 can be found, the "reusable system" claim is not
supportable and the project collapses back to a single-experiment analysis. That is still a
worthwhile project, but it is not the anchor-scale version. **Resolve this before writing
implementation code** (§9, Phase 0).

---

## 6. The core abstraction: the two-table contract

Everything in the system reads from exactly two tables. Any experiment, from any source,
must be mapped into this shape before analysis. Nothing downstream may read a
dataset-specific column.

**`assignments`** — one row per unit

| Column | Type | Null? | Meaning |
|---|---|---|---|
| `unit_id` | string | no | Unique, one row per unit. Duplicates are a validity failure. |
| `variant` | string | no | Which arm. Must match a variant declared in experiment config. |
| `assigned_at` | timestamp | yes | When assignment occurred. Null permitted for datasets without a time dimension (e.g. Cookie Cats); horizon logic then falls back to precomputed outcome columns. |

**`events`** — one row per unit-event

| Column | Type | Null? | Meaning |
|---|---|---|---|
| `unit_id` | string | no | Foreign key to `assignments`. An event with no matching assignment is a validity failure. |
| `event_type` | string | no | e.g. `return`, `purchase`, `click`. |
| `event_at` | timestamp | yes | When it happened. Required for horizon-based metrics. |
| `value` | double | yes | Numeric payload for value metrics (revenue, duration). Null for count metrics. |

**Experiment config** — a small YAML or JSON file per experiment declaring: experiment name,
list of variants, which is control, intended split ratio, primary metric, guardrail metrics,
metric horizons to evaluate, and any pre-treatment covariate columns available.

**Why this matters beyond convenience.** The contract is what makes this a system rather
than a script, and it is the same design instinct as an earlier project (CostSense), where
pipeline agents communicated only through typed contracts and never free-form data. Worth
stating aloud in an interview as a consistent engineering preference, not a coincidence.

**Mapping Cookie Cats to the contract.** `retention_1` / `retention_7` are precomputed
outcomes, not events. Map each true value to a synthetic `return` event with `event_at`
null and rely on the config declaring the horizon as precomputed. Document this adapter
clearly — it is exactly the kind of impedance mismatch a real system hits.

---

## 7. Requirements

### 7.1 P0 — Data contract and ingestion

Load a raw dataset, map it to the two-table contract, and persist to DuckDB.

- [ ] `assignments` and `events` tables created in DuckDB with the §6 schema
- [ ] One adapter module per dataset; adapters are the *only* code aware of source columns
- [ ] Experiment config file parsed and validated (unknown variant → error, not silent pass)
- [ ] Cookie Cats loads end to end and row counts match the source file exactly
- [ ] Any row dropped during mapping is counted and reported, never silently discarded

### 7.2 P0 — Validity diagnostics

Run before any effect estimate is computed or displayed.

- [ ] **SRM check** via chi-squared goodness-of-fit against the config's intended ratio
- [ ] **Duplicate unit detection** — a `unit_id` appearing twice in `assignments`
- [ ] **Cross-contamination** — a `unit_id` appearing under more than one variant
- [ ] **Orphan events** — events whose `unit_id` has no assignment
- [ ] **Zero-activity units** — assigned units with no events at all, reported per variant
  (a large imbalance here is itself a signal)
- [ ] **Pre-period balance** on any declared pre-treatment covariates (Case Study 2 only;
  Cookie Cats has none)

**SRM threshold — decided, with rationale.** Block at **p < 0.001**, warn at **p < 0.05**.
The 0.001 convention is standard industry practice: with the number of experiments a real
platform runs, a 0.05 threshold generates constant false alarms. Cookie Cats' uneven split
is expected to land *between* these thresholds, meaning it warns but does not block —
which is precisely why this is worth writing up rather than hiding. The memo must state the
threshold, the observed p-value, and why the experiment was still considered interpretable.

### 7.3 P0 — The blocking rule

**If any diagnostic returns BLOCK, the readout does not render an effect estimate.** It
renders the diagnostic failure and stops.

- [ ] BLOCK state suppresses all effect estimates, in every output surface (memo, dashboard, CLI)
- [ ] WARN state renders results with the warning displayed adjacent to the estimate, not in a footnote
- [ ] The rule is unit-tested with a deliberately corrupted dataset that must trigger BLOCK

This is an opinionated design choice and should be defended as one: a system that will hand
you a number it does not trust is worse than no system. It mirrors the confidence-gating
approach used in CostSense.

### 7.4 P0 — Power analysis, computed up front

- [ ] MDE computed from sample size, baseline rate, α = 0.05, power = 0.80
- [ ] Reported **before** results in every output — ordering is a requirement, not a preference
- [ ] Memo explicitly compares MDE against a stated "effect size the business would care
  about," and says whether the experiment could have answered its own question

### 7.5 P0 — Metric computation across horizons

- [ ] Primary metric computed at each configured horizon
- [ ] Guardrail metrics computed alongside, never omitted when the primary metric looks good
- [ ] Absolute and relative effect, with bootstrap confidence intervals (10,000 resamples)
- [ ] All aggregation implemented in **SQL** (CTEs and window functions), not pandas.
      Python is limited to bootstrap, power calculation, and simulation.
- [ ] Horizon comparison surfaced as a first-class output — the day-1 vs day-7 contrast is
      the project's headline, not a subsection

### 7.6 P0 — Peeking protocol

- [ ] A simulation showing false-positive rate under daily peeking vs a single fixed readout
- [ ] Presented **before** the results in the memo, as a stated analysis protocol
      ("this experiment is read once, at day 7, and here is why") — not as an appendix
- [ ] Documented as an organizational failure mode, not just a statistical curiosity
- [ ] One chart, roughly half a page. If it grows beyond that, it is out of scope.

### 7.7 P0 — Decision memo

The primary artifact for a non-technical reader.

- [ ] Opens with the decision — ship / do not ship / re-run — in the first three sentences
- [ ] States the cost of being wrong in *both* directions
- [ ] Written for a product team, not a statistics audience; every term from §4 that appears
      is explained in-line or avoided
- [ ] Contains a "what this data cannot tell us" section, including the §5.2 constraints
- [ ] Serves as the repository README, or is linked from its first screen

### 7.8 P1 — Second case study

- [ ] Second dataset mapped through the *unmodified* contract (changes to the contract
      required by dataset 2 are a finding worth documenting, not a failure)
- [ ] Segmentation on pre-treatment covariates only
- [ ] CUPED applied, with before/after variance reduction reported
- [ ] Real sequential analysis if the dataset has timestamps

### 7.9 P1 — Tableau scorecard

- [ ] Variant metrics side by side with effect size and confidence interval
- [ ] Diagnostics panel including SRM status, visually prominent
- [ ] Horizon toggle
- [ ] Parameterized so either experiment loads without rebuilding the workbook
- [ ] Published to Tableau Public and linked from the README

### 7.10 P2 — Future considerations

Not built now. Listed so the architecture does not foreclose them.

- Multi-arm experiments (>2 variants) with multiple-comparison correction
- Ratio metrics with the delta method for variance
- Proper sequential testing (always-valid p-values / group sequential boundaries)
- A CLI that runs the full readout from a config path in one command

---

## 8. Non-goals

| Not doing | Why |
|---|---|
| Running an original experiment on live users | No traffic, no product. This project analyzes existing experiments. |
| Building a synthetic Hotstar experiment | Generating an effect and then "discovering" it proves nothing, and a sharp interviewer will spot it in one question. The Hotstar connection stays as a written framing, not fabricated data. |
| A causal inference library | Uses standard methods; does not invent them. |
| A web application or hosted service | Repo + memo + Tableau dashboard is the delivery surface. |
| Bayesian A/B testing | Interesting, doubles the surface area, and the frequentist path already carries the argument. Explicitly parked. |
| Beating a benchmark or leaderboard | There is no benchmark. The output is a decision, not a score. |

---

## 9. Phasing

| Phase | Deliverable | Exit condition |
|---|---|---|
| **0** | **Resolve Case Study 2 dataset** (§5.3) | A dataset with confirmed pre-treatment covariates is identified and its schema inspected — or the anchor scope is formally abandoned and the project is rescoped to one case study. **No implementation code before this.** |
| **1** | Contract, DuckDB load, Cookie Cats adapter | Row counts match source exactly |
| **2** | Diagnostics suite + blocking rule | Corrupted-input test triggers BLOCK |
| **3** | Power/MDE, SQL metrics across horizons, bootstrap CIs | Day-1 vs day-7 contrast reproduced and verified against the raw file |
| **4** | Decision memo for Cookie Cats | A non-technical reader can state the decision after one screen |
| **5** | Second dataset, segmentation, CUPED | Contract unchanged, or changes documented |
| **6** | Tableau scorecard | Both experiments load without workbook edits |

**Phases 1–4 are a complete, shippable project on their own.** If the project stalls, it
stalls with something coherent rather than a half-built system. Phases 5–6 are what make it
anchor-scale.

---

## 10. Success criteria

This is a portfolio project, so "success" is legibility and defensibility, not adoption.

**Artifact bar (binary, self-assessed):**

- [ ] A non-technical reader states the decision correctly after reading only the first screen
- [ ] At least one `.sql` file doing real analytical work — CTEs and window functions, not `SELECT *`
- [ ] Power analysis appears before results in every output surface
- [ ] The repo contains a written, correct explanation of an analysis that was *declined*
      for methodological reasons (the §5.2 segmentation constraint)
- [ ] Deliberately corrupted input produces a BLOCK, demonstrably

**Signal bar (observable over the placement season):**

- [ ] Survives interview interrogation: every methodological choice has a stated reason
- [ ] Referenced unprompted by an interviewer who read the repo
- [ ] Retires "SQL claimed but not evidenced" and "no experimentation evidence" as portfolio gaps

**Explicit anti-goal:** if the final artifact is a notebook that loads a CSV, runs a
significance test, and reports a p-value, the project has failed regardless of code quality.

---

## 11. Repository layout

```
experiment-readout-system/
├── README.md                 # The decision memo. First screen = the verdict.
├── SPEC.md                   # This file.
├── config/
│   ├── cookie_cats.yaml      # Experiment config (variants, split, metrics, horizons)
│   └── <case_study_2>.yaml   # TBD — see §5.3
├── adapters/
│   ├── cookie_cats.py        # ONLY file aware of Cookie Cats' column names
│   └── <case_study_2>.py     # TBD
├── sql/
│   ├── 01_diagnostics.sql    # SRM, duplicates, orphans, cross-contamination
│   ├── 02_metrics.sql        # Horizon-parameterized metric aggregation
│   └── 03_segments.sql       # Pre-treatment covariates only (Case Study 2)
├── analysis/
│   ├── power.py              # MDE / power calculation
│   ├── bootstrap.py          # Bootstrap confidence intervals
│   └── peeking_sim.py        # False-positive inflation simulation
├── readout/
│   ├── gate.py               # The BLOCK/WARN rule (§7.3)
│   └── memo.py               # Generates the markdown scorecard
├── tests/
│   └── test_gate.py          # Corrupted input MUST trigger BLOCK
├── data/                     # Gitignored. Datasets downloaded, not committed.
└── tableau/                  # Packaged workbook + link to Tableau Public
```

**Stack:** DuckDB (embedded, reads CSV directly — no server), Python 3.11+, Tableau Public.
No cloud dependencies, no API keys, runs offline after the datasets are downloaded.

---

## 12. Open questions

**BLOCKING — must be resolved before implementation begins:**

1. ~~**Which dataset is Case Study 2?**~~ **RESOLVED, Phase 0 — Criteo Uplift Prediction
   Dataset** (Diemert et al., AdKDD 2018), 13,979,592 rows, CC BY-NC-SA 4.0.

   It was the only one of four candidates with unit-level pre-treatment covariates. Its 12
   covariates are balanced across arms (worst |SMD| = 0.0488, all inside the 0.10 threshold)
   and `f9` correlates +0.495 with the `visit` outcome, so CUPED has something to work with.

   Rejected: **ASOS** (group-level aggregates only — no units exist, so it cannot be
   expressed in the §6 contract), **Upworthy Research Archive** (randomises impressions; no
   per-user rows), **Kaggle "Marketing A/B testing"** (its only non-outcome columns describe
   post-assignment ad delivery — confirming the prediction in the implementation brief).

   Three caveats accepted, detailed in `docs/dataset_selection.md` §6: no unit identifier,
   no stated intended split ratio, and anonymised randomly-projected covariates. Criterion 2
   (event timestamps) is **not** met, so the peeking analysis remains a simulation as
   specified in §7.6.

**NON-BLOCKING — resolve during implementation:**

2. ~~**Do the Cookie Cats expectations in §5.1 actually hold?**~~ **VERIFIED, Phase 0 —
   yes, with one correction to how it must be phrased.**

   Measured on the real file (90,189 rows, sha256 `5ab54d76…`; schema exactly as §5.1
   predicted, and the split is exactly the recalled 44,700 / 45,489):

   | horizon | gate_30 (control) | gate_40 (treatment) | absolute | relative | p |
   |---|---|---|---|---|---|
   | day 1 | 44.8188% | 44.2283% | −0.5905pp | −1.32% | 0.0744 |
   | day 7 | 19.0201% | 18.2000% | −0.8201pp | −4.31% | 0.0016 |

   SRM: chi² = 6.9024, p = 0.008608 — between the warn and block thresholds, exactly as
   §7.2 anticipated.

   **The correction.** The two horizons do not have opposite *signs* — both point against
   `gate_40`. What flips is *detectability*: day 1 is inside noise, day 7 is not, and the
   relative effect is over three times larger. The defensible phrasing is therefore "day-1
   reads neutral and ships, day-7 reads harmful and does not" — opposite **decisions**, not
   opposite estimates. All downstream surfaces use that phrasing. §13's resume bullet 2
   already says "scored as neutral" and is consistent with the data as written.
3. **Is Tableau the right surface for the scorecard**, or does a static generated HTML report
   serve better? Tableau adds a second artifact to an existing Tableau Public profile, which
   is the main argument for it. *Decide at Phase 6.*
4. **How far to take CUPED** — a demonstration with reported variance reduction, or a proper
   treatment including the pre-period selection trade-off? *Depends on Case Study 2's data.*
5. **Repository naming.** `experiment-readout-system` is a working title.

**Deliberately NOT open — already decided, do not revisit:**

- Segmentation on `sum_gamerounds` is out. It is post-treatment. (§5.2)
- Peeking analysis goes *before* results as protocol, not after as an appendix. (§7.6)
- SQL does the aggregation; Python does not. (§7.5)
- No synthetic/simulated primary dataset. (§8)

---

## 13. Resume bullets this produces

Written in advance deliberately — if the finished project cannot support these claims
honestly, the project has drifted.

> **Experiment Readout System** — *Experimentation Analysis Platform* [GitHub] [Dashboard]
> - Built a reusable A/B test analysis pipeline on a two-table assignment/events contract,
>   running sample-ratio-mismatch, duplicate-assignment and exposure diagnostics that block
>   a readout from rendering when assignment integrity fails.
> - Showed on a 90K-user retention experiment that the day-1 and day-7 reads support
>   opposite ship decisions, and recommended against a change that short-horizon metrics
>   scored as neutral.
> - Shipped a parameterized Tableau scorecard producing variant metrics, effect sizes and
>   confidence intervals for any experiment mapped to the contract.
>
> **Stack:** SQL (DuckDB, CTEs, window functions), Python, bootstrap confidence intervals,
> power analysis, Tableau

Bullet 2 is contingent on §12 question 2 verifying. **Do not put it on a resume before the
data confirms it.**
