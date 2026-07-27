# Implementation Brief — Experiment Readout System

**Status:** Phase 0, not started
**Companion to:** `SPEC.md` (authoritative)
**Owner:** Shreyas Jain
**Type:** Portfolio project, personal. No client, no employer.

---

## 0. Read this first

**`SPEC.md` is the authoritative design document. This brief does not replace it and does
not redesign anything in it.** The spec is complete, opinionated and self-contained; its
§6 two-table contract, §7 requirements, §9 phasing and §12 open questions all stand as
written.

What this brief adds is the part the spec deliberately leaves open: **how to actually get
moving**, given that the spec declares Phase 0 blocking and nothing has been built.

Order of reading for a fresh session:

1. `SPEC.md` in full. It is long. Read it anyway — §5.2 and §12 contain decisions that will
   otherwise get re-litigated and wasted.
2. This brief, §1 onward.

**Two behaviours the spec asks for that are easy to violate under time pressure:**

- Do not fabricate a dataset, a schema, or a finding. Where the spec says "verify on pull,"
  the number is a recollection and has not been confirmed.
- Do not build the standard Kaggle notebook version. The spec's §10 names it an explicit
  anti-goal: load CSV → chi-square → report p-value is negative signal.

---

## 1. Why this project is being built now

Straight from the spec's §2, restated because it should drive prioritization decisions:

This portfolio has **two named gaps**. SQL is claimed on every resume variant and appears
on every target JD, but across nine public repositories there is exactly one SQL artifact
and it is a course lab. Experimentation is a stated requirement on the target Product
Analyst JD and nothing in the portfolio speaks to it at all.

This project closes both, and it is the only one of the three current workstreams that
closes the second. That makes it the highest-leverage of the three for interview outcomes,
even though it is the least far along.

**Practical consequence for sequencing:** phases 1–4 are a complete shippable project on
their own (spec §9). If time is short, ship 1–4 and stop. A coherent single-case-study
readout beats a half-built two-case-study system.

---

## 2. Current state

```
experiment-readout-system/
├── SPEC.md                    # complete, v1
└── IMPLEMENTATION_BRIEF.md    # this file
```

That is everything. Specifically:

- **Not a git repository.** No `git init` has been run.
- No data, no code, no config, no `.gitignore`.
- Not on GitHub.

---

## 3. Phase 0 — the blocking work

The spec forbids implementation code before Phase 0 closes. Phase 0 has **two** items; the
spec names one as blocking and one as non-blocking, but they should be done together
because they are both dataset-inspection work and both cheap.

### 3.1 Resolve Case Study 2 (spec §5.3, §12 Q1) — BLOCKING

Required: a second public experiment dataset with **genuine pre-treatment covariates**.
This is the criterion that matters; without it the segmentation and CUPED work (spec §7.8)
is impossible and the "reusable system" claim is not supportable.

**Candidate families to evaluate. Schemas below are unverified — inspect the real files
before committing to any of them, and do not write any of these descriptions into the repo
as fact.**

| Candidate | Why it might work | What to check |
|---|---|---|
| **Criteo Uplift** (Diemert et al.) | Large, openly licensed, carries a block of numeric features that are plausibly pre-treatment | Are the features genuinely pre-randomization, or derived post-exposure? Anonymized feature names weaken the segmentation narrative — decide if that is acceptable. Check for any time dimension. |
| **ASOS Digital Experiments Dataset** | Real e-commerce experiments, published with day-level metric time series per variant | Grain is likely aggregated per experiment-day, not per unit. If so, the two-table contract may not map — that is itself a documentable finding, but check before choosing it. |
| **Upworthy Research Archive** | Thousands of genuine headline/image A/B tests with impressions and clicks | Randomization unit is likely the impression, not the user. Confirm whether unit-level assignment exists. |
| **Kaggle "Marketing A/B testing"** | Convenient size, clean | Likely **fails criterion 1** — its extra columns (ad counts, most-active day/hour) look post-treatment, the same trap as `sum_gamerounds` in §5.2. Verify; if confirmed post-treatment, reject it and say so in the repo. |

**Evaluation procedure.** For each candidate, in order, until one passes:

1. Pull the file. Print the real schema and row count — do not trust documentation.
2. For every non-outcome column, ask: *could the treatment have influenced this?* If yes,
   it is post-treatment and does not count toward criterion 1.
3. Confirm there is a stated or inferable intended split ratio (needed for the SRM check).
4. Confirm licensing permits linking from a public repo.
5. Record the verdict — pass or reject, with the reason — in a new `docs/dataset_selection.md`.

**That rejection log is a deliverable, not scratch work.** A written record of datasets
evaluated and rejected on methodological grounds is exactly the judgment signal the spec's
§10 is asking for, and it costs nothing extra to keep.

**Fallback rule — decide it now, not later.** If no candidate passes criterion 1 after
evaluating all four, **formally rescope to a single case study** rather than proceeding with
a dataset that cannot support segmentation. Record the rescope in `SPEC.md` §12 Q1 with the
reasoning. Do not stretch a post-treatment covariate into a pre-treatment role to save the
scope; that is the exact error §5.2 already documents.

### 3.2 Verify the Cookie Cats headline (spec §12 Q2) — do it now, not in Phase 3

The spec schedules this for Phase 1 and warns it is an unverified recollection. Pull it
forward into Phase 0, because **the entire project framing depends on it**:

> day-1 retention shows little difference between variants; day-7 retention favours
> `gate_30`, meaning moving the gate later is harmful.

Load the file, compute both retention rates by variant, and check whether the divergence is
actually there and in the stated direction.

- **If it holds:** proceed as specced. The memo's spine is intact.
- **If it does not:** the project is still viable, but the memo's spine moves to whatever
  the data does show, and the pre-written resume bullet in spec §13 must not be used.
  Update spec §12 Q2 with the real numbers before writing anything downstream.

This is ~20 minutes of work that de-risks the entire write-up. Doing it in Phase 3, as
specced, means discovering a broken premise after three phases of implementation.

### 3.3 Repository setup

Cheap, and unblocks everything after:

- `git init`, branch `main`
- `.gitignore`: `data/`, `*.duckdb`, `.venv/`, `__pycache__/`, `.DS_Store`
- `requirements.txt`: `duckdb>=1.0`, `pandas`, `scipy`, `numpy`, `pyyaml`
- `data/download.sh` — fetches datasets. **Datasets are never committed** (spec §11).
- Scaffold the empty directory tree exactly as spec §11 lays it out
- Do **not** push to GitHub until the README is a real decision memo. An empty-shell repo
  with a stub README is worse than no repo — it is the first thing a recruiter clicks.

### Phase 0 exit condition

- [ ] Case Study 2 selected with a verified schema, **or** formally rescoped to one case study
- [ ] `docs/dataset_selection.md` records every candidate evaluated and why it passed/failed
- [ ] Cookie Cats day-1 vs day-7 divergence confirmed or corrected, with real numbers in `SPEC.md` §12 Q2
- [ ] Repo initialized, `.gitignore` correct, no data files staged
- [ ] **Report back before writing implementation code**

---

## 4. Phases 1–4 — the shippable core

Follow spec §9 exactly. Notes below are execution guidance only, not design changes.

### Phase 1 — Contract, DuckDB load, Cookie Cats adapter

Spec §6 and §7.1. The adapter is the only module allowed to know Cookie Cats' column names.

The awkward bit is real and the spec flags it: `retention_1` / `retention_7` are precomputed
booleans, not events. Map each `true` to a synthetic `return` event with a null `event_at`,
and let the config declare the horizon as precomputed. **Document the adapter's reasoning
in a docstring** — it is a genuine impedance mismatch and the write-up should own it rather
than hide it.

Exit: row counts match the source file exactly, and every dropped row is counted.

### Phase 2 — Diagnostics and the blocking rule

Spec §7.2 and §7.3. Build the diagnostics before any effect estimate exists in the codebase,
so the ordering constraint is enforced by construction rather than by discipline.

The blocking rule is the project's most opinionated design choice. Implement it as a hard
gate in `readout/gate.py` that suppresses effect estimates in *every* output surface, and
test it with a deliberately corrupted fixture (spec §7.3). That test is the proof, so write
it first.

Expected wrinkle, per spec §7.2: Cookie Cats' uneven split should land between the warn
(p < 0.05) and block (p < 0.001) thresholds. That is a feature — it makes the SRM check a
real decision point. The memo must state the threshold, the observed p-value, and why the
experiment was still considered interpretable.

Exit: corrupted input triggers BLOCK, demonstrably.

### Phase 3 — Power, SQL metrics, bootstrap

Spec §7.4 and §7.5.

**The SQL/Python split is a hard requirement, not a preference.** All aggregation lives in
`sql/02_metrics.sql` using CTEs and window functions. Python does bootstrap, power, and
simulation only. This is the requirement that makes the project count as SQL evidence — if
metric aggregation drifts into pandas because it was more convenient, the project has failed
its primary purpose.

Power analysis output must appear **before** results in every surface (spec §7.4). Ordering
is specified as a requirement.

Exit: day-1 vs day-7 contrast reproduced and verified against the raw file.

### Phase 4 — Decision memo

Spec §7.7. The memo is the README. First screen states the decision.

Test it the way spec §10 does: can a non-technical reader state the decision correctly after
reading only the first screen? If not, rewrite the opening rather than adding explanation
further down.

Include the "what this data cannot tell us" section covering the §5.2 constraints. A written,
correct explanation of an analysis that was *declined* on methodological grounds is on the
spec's success-criteria checklist.

**Phases 1–4 complete = ship it.** Push to GitHub. Phases 5–6 are upside.

---

## 5. Phases 5–6 — upside

Only after 1–4 are pushed and the README reads as a decision memo.

- **Phase 5** (spec §7.8): second dataset through the *unmodified* contract. If the contract
  needs changes, that is a finding to document, not a failure to hide. Segmentation on
  pre-treatment covariates only. CUPED with before/after variance reduction reported.
- **Phase 6** (spec §7.9): Tableau scorecard, parameterized so either experiment loads
  without workbook edits. Publish to Tableau Public, link from README.

Spec §12 Q3 leaves Tableau-vs-static-HTML open until Phase 6. The argument for Tableau is
that it adds a second artifact to an existing Tableau Public profile, which is a portfolio
consideration rather than a technical one. Decide it then.

---

## 6. Acceptance criteria

From spec §10, restated as a checklist:

- [ ] A non-technical reader states the decision correctly after one screen
- [ ] At least one `.sql` file doing real analytical work — CTEs and window functions
- [ ] Power analysis appears before results in every output surface
- [ ] The repo contains a written, correct explanation of a *declined* analysis
- [ ] Deliberately corrupted input produces a BLOCK, demonstrably
- [ ] Every methodological choice has a stated reason in the repo
- [ ] No fabricated data, schema, or finding anywhere

---

## 7. Do not do these

| Don't | Why |
|---|---|
| Write implementation code before Phase 0 closes | Spec §9. The Case Study 2 answer changes the architecture's surface area. |
| Segment on `sum_gamerounds` | Post-treatment, collider bias. Spec §5.2, marked "do not revisit." |
| Do metric aggregation in pandas | Spec §7.5. SQL is the whole point of the project. |
| Put the peeking analysis in an appendix | Spec §7.6. It goes before results, as stated protocol. |
| Generate a synthetic Hotstar experiment | Spec §8. Manufacturing an effect and then "finding" it collapses under one interview question. |
| Use the spec §13 resume bullet 2 before verifying §12 Q2 | It is contingent on data that has not been checked. |
| Add Bayesian A/B testing | Spec §8, explicitly parked. |
| Push a stub repo to GitHub | First thing a recruiter clicks. Push when the memo is real. |

---

## 8. Suggested first session

1. Read `SPEC.md` fully.
2. Do §3.3 repo setup — 15 minutes, unblocks everything.
3. Do §3.2 Cookie Cats verification — pull the data, compute both retention rates by
   variant, compare against the spec's stated expectation.
4. Start §3.1 Case Study 2 evaluation. Work the candidate list in order. Log every verdict.
5. **Stop and report.** State: whether the Cookie Cats headline held (with real numbers),
   which Case Study 2 candidate passed or whether a rescope is recommended, and what the
   evaluation log says.

Do not start Phase 1 in the same session. Phase 0's answers determine what Phase 1 builds.
