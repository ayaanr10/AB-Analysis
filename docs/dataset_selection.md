# Dataset Selection Log — Case Study 2

**Resolves:** `SPEC.md` §5.3 and §12 Q1 (blocking).
**Date:** 27 July 2026
**Verdict: Criteo Uplift Prediction Dataset selected, with three stated caveats (§6).**

This log exists because a record of datasets evaluated *and rejected on methodological
grounds* is more informative about judgment than the dataset that happened to win. Every
schema below was read off the real file. Nothing here is quoted from documentation without
being checked against the bytes.

---

## 1. What Case Study 2 has to do

Cookie Cats has effectively no pre-treatment covariates (`SPEC.md` §5.2). That makes
heterogeneous treatment effect analysis and CUPED impossible on it. Case Study 2 exists to
demonstrate that the contract generalises *and* to carry the two analyses Case Study 1
cannot support. So criterion 1 is not a nice-to-have — a dataset that fails it cannot do
the job, no matter how clean it is.

Criteria, from `SPEC.md` §5.3, in priority order:

1. **Genuine pre-treatment covariates** — required
2. Event-level timestamps — strongly preferred
3. Documented randomisation and a stated intended split ratio
4. Openly licensed, stable enough to link from a public repo
5. Order 10⁴+ units, so the SRM check means something

**Procedure:** for each candidate, pull the file, print the real schema, then ask of every
non-outcome column: *could the treatment have influenced this?* Stop at the first criterion
a candidate fails and record it. Evaluated in the order given by `IMPLEMENTATION_BRIEF.md` §3.1.

---

## 2. How "pre-treatment" was actually decided

This came up immediately and is worth stating before the verdicts, because two of the four
rejections turn on it.

A variable is pre-treatment if it is **measured before randomisation**. That is a fact about
*when the value was recorded*, not a fact about its distribution. Two consequences:

- **A balance check can falsify, but cannot confirm.** If a variable differs across arms
  beyond chance, it was probably affected by the treatment — that rules it out. But a
  variable measured *after* assignment can still come out balanced, and balance does not
  redeem it. Case in point: the Kaggle marketing dataset's `total ads` is balanced to
  SMD = +0.0014 (p = 0.83) and is still post-treatment, because it counts campaign
  deliveries that happened after users were assigned (§5).
- **Therefore column semantics decide, and the balance check is corroboration.** Where
  semantics are unavailable — anonymised features — the honest position is that
  pre-treatment status is *unfalsified*, not *established*. That is the position this
  project takes on Criteo, and it is stated as a limitation rather than papered over (§6.3).

A second finding, which changed a design decision downstream:

> **At large n, a p-value-based balance check is useless.** All 12 Criteo covariates are
> "imbalanced" at p < 0.001 — several at p < 10⁻³⁰⁰ — while every one of them sits at
> |SMD| ≤ 0.049, far inside the conventional 0.10 balance threshold. With 14M rows the test
> detects imbalances far too small to matter.
>
> **Consequence:** the pre-period balance diagnostic (`SPEC.md` §7.2) is implemented on
> **standardised mean difference, not p-values**. A p-value implementation would have
> blocked this experiment spuriously. Recorded in `docs/decisions.md` (D-04).

---

## 3. Criteo Uplift Prediction Dataset — **SELECTED**

Diemert, Betlei, Renaudin & Amini (AdKDD 2018). Retrieved from the Criteo org mirror on
Hugging Face; the URL on Criteo's own dataset page (`go.criteo.net`) is dead as of this
writing — a 404 over both http and https.

| | |
|---|---|
| File | `criteo-research-uplift-v2.1.csv.gz`, 311,422,618 bytes |
| sha256 | `2716e1bf0fd157a93b5bf86924d9088419dfbac2022c6cd90030220634f616dc` |
| Rows | 13,979,592 |
| Licence | CC BY-NC-SA 4.0 |

**Real schema** (read from the file, not the docs):

| Column | Type | Role |
|---|---|---|
| `f0` … `f11` | DOUBLE ×12 | candidate pre-treatment covariates |
| `treatment` | BIGINT | 1 = treated, 0 = control |
| `visit` | BIGINT | outcome |
| `conversion` | BIGINT | outcome |
| `exposure` | BIGINT | **post-treatment**, see below |

**Split:** treatment 11,882,655 (85.00%) / control 2,096,937 (15.00%).

### Criterion 1 — pre-treatment covariates: PASS (qualified)

- **Balance:** worst |SMD| across the 12 features is 0.0488 (`f3`); all 12 under the 0.10
  threshold. Nothing here is detectably shifted by treatment.
- **Predictive of the outcome:** `f9` correlates +0.495 with `visit`, `f8` −0.458, `f4`
  +0.267. A covariate that is balanced across arms *and* correlated with the outcome is
  exactly the input CUPED needs — theoretical variance reduction from `f9` alone is
  corr² ≈ 24.5%. This was checked **before** selecting the dataset, because a covariate set
  uncorrelated with the outcome would make Phase 5's CUPED demonstration vacuous.
- **Granularity:** 12,353,333 distinct feature vectors across 13,979,592 rows (88.4%), so
  these are unit-level attributes, not a handful of segment codes.

Qualified because the features are anonymised and randomly projected, so their timing cannot
be verified from the file. See caveat §6.3.

### `exposure` is post-treatment — confirmed empirically, not assumed

| treatment | exposure | rows |
|---|---|---|
| 0 | 0 | 2,096,937 |
| 1 | 0 | 11,454,443 |
| 1 | 1 | 428,212 |

`exposure = 1` occurs **only** under `treatment = 1`. It is a record of the treatment being
delivered. It is excluded from every covariate role — segmentation, CUPED, balance — and is
treated as an outcome. This is the same error class as `sum_gamerounds` in `SPEC.md` §5.2.

### Remaining criteria

| # | Criterion | Verdict |
|---|---|---|
| 2 | Event-level timestamps | **FAIL** — no time column of any kind. Peeking stays a simulation (§6.2). |
| 3 | Stated intended split ratio | **PARTIAL** — 85/15 is evident in the data but not documented as intent (§6.1). |
| 4 | Open licence, stable host | PASS — CC BY-NC-SA 4.0. Non-commercial; this repo links and never redistributes. |
| 5 | Scale for a meaningful SRM check | PASS — 14M units. |

Criterion 1 is the required one and it passes. Selected.

---

## 4. ASOS Digital Experiments Dataset — **REJECTED** (criterion 1)

Liu, Cardoso, Couturier & McCoy, NeurIPS 2021 Datasets & Benchmarks. `osf.io/64jsb`.

Real schema, 24,153 rows across 78 experiments:

`experiment_id`, `variant_id`, `metric_id`, `time_since_start`, `count_c`, `count_t`,
`mean_c`, `mean_t`, `variance_c`, `variance_t`

**Rejected: there are no units.** One row is a *summary statistic* for one
experiment × variant × metric × checkpoint — count, mean and variance per arm. There are no
per-unit rows, therefore no per-unit covariates, therefore criterion 1 fails at the schema
level. It also cannot be mapped to the two-table contract at all: `assignments` needs one row
per unit and `events` needs a `unit_id` foreign key, and neither exists. Reconstructing unit
rows from means and variances would be fabricating data, which `SPEC.md` §8 forbids.

**Noted, not used.** ASOS is the one candidate with a genuine time dimension — cumulative
metric values at day / half-day checkpoints, published specifically to support research on
adaptive stopping. That makes it the natural substrate for a *real* peeking analysis instead
of the simulated one in §7.6. It was considered and deferred: it would require a second
analysis path outside the two-table contract, which is architectural cost for one chart, and
"everything reads from exactly two tables" (`SPEC.md` §6) is load-bearing. Recorded as a
future option in `docs/decisions.md` (D-06) rather than silently dropped.

---

## 5. Kaggle "Marketing A/B testing" — **REJECTED** (criterion 1)

`faviovaz/marketing-ab-testing`. 588,101 rows, 588,101 distinct user ids, no duplicates.

Real schema: `column0`, `user id`, `test group`, `converted`, `total ads`,
`most ads day`, `most ads hour`.

Split: `ad` 564,577 (96.0%) / `psa` 23,524 (4.0%).
Conversion: `ad` 2.5547%, `psa` 1.7854%.

**Rejected.** Excluding the id, the group label and the outcome, the dataset has exactly
three remaining columns — `total ads`, `most ads day`, `most ads hour` — and all three
describe **campaign delivery that happened after users were assigned**. `total ads` is a
count of impressions served during the experiment; the "day/hour with the most ads" is a
property of that same post-assignment delivery. There are zero pre-treatment covariates. This
confirms the prediction in `IMPLEMENTATION_BRIEF.md` §3.1.

**The interesting part.** `total ads` is *balanced*: mean 24.823 (ad) vs 24.761 (psa),
SMD = +0.0014, p = 0.83. A naive "check balance, and if it passes call it pre-treatment"
rule would have accepted it — and it is a textbook collider, since ads seen is affected by
assignment and drives conversion. Segmenting on it reproduces the `sum_gamerounds` error
exactly. This is the empirical basis for the rule in §2: **timing decides, balance only
falsifies.**

Criteria 3–5 not evaluated; the procedure stops at the first failure.

---

## 6. Caveats accepted in selecting Criteo

Stated here so they appear in the write-up rather than surfacing in an interview.

### 6.1 There is no unit identifier, and no stated intended split

The file has no id column. `unit_id` is synthesised as the row ordinal in the adapter.
Two consequences, both documented at the point of use:

- **Duplicate-unit and cross-contamination detection become vacuous** on this dataset —
  they cannot fail by construction. They are still run, and reported as *not informative
  here* rather than as passes. A green check that cannot go red is worse than no check.
- **The SRM check is weakened.** The intended ratio is not published; 85/15 is inferred from
  the observed data. Testing observed counts against a ratio derived from those same counts
  is circular and cannot fail. The config declares 85/15 with an explicit note that it is
  inferred, and the readout labels the Criteo SRM result as non-diagnostic. Cookie Cats,
  where 50/50 is genuinely the intended design, is where the SRM check does real work.

### 6.2 No time dimension

Same shape as Cookie Cats: outcomes are precomputed flags, not timestamped events. So
criterion 2 fails, the peeking analysis remains a simulation, and metric horizons stay
declared-not-computed. The one thing Case Study 2 was hoped to add beyond covariates — a
real sequential analysis — it does not add.

### 6.3 The covariates are anonymised and randomly projected

Criteo's documentation states feature values were randomly projected to prevent recovering
the original user context. Two costs:

- **Pre-treatment status is unfalsified, not established.** The balance check found nothing,
  and the publisher describes them as user features from an incrementality test. That is the
  strongest claim the evidence supports, and it is weaker than what Cookie Cats' `userid` /
  `version` permit. Stated as a limitation in the Case Study 2 write-up.
- **The segmentation narrative is thin.** "Uplift is larger in the top quartile of `f9`" is
  not a sentence a product team can act on. The segmentation therefore demonstrates *method*
  — pre-treatment-only splits, multiple-comparison discipline — and explicitly does not
  claim a product insight. A dataset with named covariates would be better; none of the four
  candidates had both named covariates and unit-level rows.

### 6.4 What would have been chosen instead

If criterion 1 had failed on all four, the fallback in `IMPLEMENTATION_BRIEF.md` §3.1 was a
formal rescope to a single case study. That was not needed. It is recorded here so the
decision path is legible: the rescope rule was fixed *before* the evaluation ran, not chosen
afterwards to fit the result.

---

## 7. Reproducing this log

```bash
bash data/download.sh          # fetches all four candidates + Cookie Cats
bash docs/verify_selection.sh  # re-runs every number quoted above
```

Checksums for all files are in `data/checksums.sha256`. The datasets themselves are never
committed (`SPEC.md` §11).
