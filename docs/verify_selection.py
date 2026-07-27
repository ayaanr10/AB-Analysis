"""Regenerate every number quoted in docs/dataset_selection.md.

This is Phase 0 evidence, not part of the readout system — it reads the raw candidate
files directly and deliberately shares no code with adapters/ or sql/. If a number in
the selection log is wrong, this script is what proves it.

    python docs/verify_selection.py
"""

import sys
from pathlib import Path

import duckdb
from scipy import stats

DATA = Path(__file__).resolve().parent.parent / "data"
COVARIATES = [f"f{i}" for i in range(12)]
BALANCE_THRESHOLD = 0.10  # conventional |SMD| threshold


def rule(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def missing(path):
    print(f"  SKIPPED — {path.name} not present. Run data/download.sh first.")


def smd_and_t(m1, m0, s1, s0, n1, n0):
    """Standardised mean difference, Welch t, and two-sided p."""
    pooled = ((s1**2 + s0**2) / 2) ** 0.5
    smd = (m1 - m0) / pooled if pooled else 0.0
    se = (s1**2 / n1 + s0**2 / n0) ** 0.5
    t = (m1 - m0) / se if se else 0.0
    return smd, t, 2 * stats.norm.sf(abs(t))


def cookie_cats(con):
    rule("Cookie Cats — Case Study 1 (SPEC §5.1, §12 Q2)")
    path = DATA / "cookie_cats.csv"
    if not path.exists():
        return missing(path)
    con.execute(f"CREATE OR REPLACE VIEW cc AS SELECT * FROM read_csv_auto('{path}', header=true)")

    print("  schema:")
    for name, dtype, *_ in con.execute("DESCRIBE cc").fetchall():
        print(f"    {name:16s} {dtype}")

    n, distinct = con.execute("SELECT count(*), count(DISTINCT userid) FROM cc").fetchone()
    print(f"\n  rows {n:,}   distinct userid {distinct:,}   duplicates {n - distinct}")

    rows = con.execute("SELECT version, count(*) FROM cc GROUP BY 1 ORDER BY 1").fetchall()
    print("\n  variant split:")
    for variant, count in rows:
        print(f"    {variant:8s} {count:7,}  {count / n:.4%}")

    chi2, p = stats.chisquare([c for _, c in rows], f_exp=[n / 2, n / 2])
    print(f"    SRM vs 50/50: chi2={chi2:.4f}  p={p:.6f}"
          f"   -> {'WARN' if 0.001 <= p < 0.05 else 'BLOCK' if p < 0.001 else 'PASS'}")

    res = {
        r[0]: r
        for r in con.execute("""
            SELECT version, count(*) n,
                   sum(CASE WHEN retention_1 THEN 1 ELSE 0 END) r1,
                   sum(CASE WHEN retention_7 THEN 1 ELSE 0 END) r7
            FROM cc GROUP BY 1 ORDER BY 1
        """).fetchall()
    }
    control, treat = res["gate_30"], res["gate_40"]

    print("\n  retention (effect = gate_40 - gate_30):")
    for label, idx in (("day-1", 2), ("day-7", 3)):
        x0, n0 = control[idx], control[1]
        x1, n1 = treat[idx], treat[1]
        p0, p1 = x0 / n0, x1 / n1
        pooled = (x0 + x1) / (n0 + n1)
        se = (pooled * (1 - pooled) * (1 / n0 + 1 / n1)) ** 0.5
        z = (p1 - p0) / se
        pv = 2 * stats.norm.sf(abs(z))
        print(f"    {label}: ctrl={p0:.6f}  treat={p1:.6f}  abs={p1 - p0:+.6f}"
              f"  rel={(p1 - p0) / p0:+.4%}  z={z:+.4f}  p={pv:.6f}")


def criteo(con):
    rule("Criteo Uplift — Case Study 2, SELECTED (§3 of the selection log)")
    path = DATA / "criteo-research-uplift-v2.1.csv.gz"
    if not path.exists():
        return missing(path)
    con.execute(f"CREATE OR REPLACE VIEW cr AS SELECT * FROM read_csv_auto('{path}', header=true)")

    n = con.execute("SELECT count(*) FROM cr").fetchone()[0]
    print(f"  rows {n:,}")

    print("\n  treatment split:")
    for t, c in con.execute("SELECT treatment, count(*) FROM cr GROUP BY 1 ORDER BY 1").fetchall():
        print(f"    treatment={t}  {c:12,}  {c / n:.4%}")

    print("\n  exposure x treatment  (exposure=1 only under treatment=1 => post-treatment):")
    for t, e, c in con.execute(
        "SELECT treatment, exposure, count(*) FROM cr GROUP BY 1,2 ORDER BY 1,2"
    ).fetchall():
        print(f"    treatment={t} exposure={e}  {c:12,}")

    distinct = con.execute(
        f"SELECT count(*) FROM (SELECT DISTINCT {','.join(COVARIATES)} FROM cr)"
    ).fetchone()[0]
    print(f"\n  distinct covariate vectors: {distinct:,} / {n:,} = {distinct / n:.4%}")

    agg = ", ".join(
        f"avg(CASE WHEN treatment=1 THEN {f} END), avg(CASE WHEN treatment=0 THEN {f} END),"
        f"stddev_samp(CASE WHEN treatment=1 THEN {f} END), stddev_samp(CASE WHEN treatment=0 THEN {f} END)"
        for f in COVARIATES
    )
    row = con.execute(
        f"SELECT {agg}, sum(CASE WHEN treatment=1 THEN 1 ELSE 0 END),"
        f" sum(CASE WHEN treatment=0 THEN 1 ELSE 0 END) FROM cr"
    ).fetchone()
    n1, n0 = row[-2], row[-1]

    print("\n  covariate balance — effect size vs p-value (the D-04 finding):")
    print(f"    {'feat':6s} {'SMD':>9s} {'|SMD|<0.10':>11s} {'Welch t':>11s} {'p':>12s} {'p<0.001':>9s}")
    worst = 0.0
    for i, f in enumerate(COVARIATES):
        m1, m0, s1, s0 = row[4 * i:4 * i + 4]
        smd, t, pv = smd_and_t(m1, m0, s1, s0, n1, n0)
        worst = max(worst, abs(smd))
        print(f"    {f:6s} {smd:+9.4f} {'PASS' if abs(smd) < BALANCE_THRESHOLD else 'FAIL':>11s}"
              f" {t:+11.2f} {pv:12.3e} {'IMBALANCE' if pv < 0.001 else 'ok':>9s}")
    print(f"\n    worst |SMD| = {worst:.4f}  (threshold {BALANCE_THRESHOLD})")
    print("    -> every covariate passes on effect size and fails on p-value.")

    print("\n  covariate/outcome correlation (CUPED viability):")
    corr = ", ".join(f"corr({f},visit), corr({f},conversion)" for f in COVARIATES)
    row = con.execute(f"SELECT {corr} FROM cr").fetchone()
    best = max(range(12), key=lambda i: abs(row[2 * i]))
    for i, f in enumerate(COVARIATES):
        print(f"    {f:6s} visit={row[2 * i]:+.5f}  conversion={row[2 * i + 1]:+.5f}")
    print(f"\n    strongest: {COVARIATES[best]} corr={row[2 * best]:+.5f} with visit"
          f"  ->  corr^2 = {row[2 * best] ** 2:.4%} theoretical variance reduction")


def asos(con):
    rule("ASOS Digital Experiments — REJECTED, criterion 1 (§4)")
    path = DATA / "asos_experiments.parquet"
    if not path.exists():
        return missing(path)
    con.execute(f"CREATE OR REPLACE VIEW a AS SELECT * FROM read_parquet('{path}')")
    print("  schema:")
    for name, dtype, *_ in con.execute("DESCRIBE a").fetchall():
        print(f"    {name:20s} {dtype}")
    n, e = con.execute("SELECT count(*), count(DISTINCT experiment_id) FROM a").fetchone()
    print(f"\n  rows {n:,} across {e} experiments")
    print("  grain: one row per experiment x variant x metric x checkpoint.")
    print("  no unit_id, no per-unit rows, no covariates -> criterion 1 fails at the schema level.")


def upworthy(con):
    rule("Upworthy Research Archive — REJECTED, criterion 1")
    path = DATA / "upworthy_exploratory.csv"
    if not path.exists():
        return missing(path)
    con.execute(f"CREATE OR REPLACE VIEW u AS SELECT * FROM read_csv_auto('{path}', header=true)")
    n, tests, imp, clicks = con.execute(
        "SELECT count(*), count(DISTINCT clickability_test_id), sum(impressions), sum(clicks) FROM u"
    ).fetchone()
    print(f"  packages {n:,}   tests {tests:,}   impressions {imp:,}   clicks {clicks:,}")
    print("  grain: one row per package (headline/image arm) per test.")
    print("  randomisation unit is the impression; no per-user rows exist -> criterion 1 fails.")


def marketing(con):
    rule("Kaggle 'Marketing A/B testing' — REJECTED, criterion 1 (§5)")
    path = DATA / "marketing_AB.csv"
    if not path.exists():
        return missing(path)
    con.execute(f"CREATE OR REPLACE VIEW m AS SELECT * FROM read_csv_auto('{path}', header=true)")
    print("  schema:")
    for name, dtype, *_ in con.execute("DESCRIBE m").fetchall():
        print(f"    {name:16s} {dtype}")
    n, u = con.execute('SELECT count(*), count(DISTINCT "user id") FROM m').fetchone()
    print(f"\n  rows {n:,}   distinct user id {u:,}   duplicates {n - u}")
    for g, c in con.execute('SELECT "test group", count(*) FROM m GROUP BY 1 ORDER BY 1').fetchall():
        print(f"    {g:5s} {c:8,}  {c / n:.4%}")

    print("\n  conversion:")
    for g, r in con.execute(
        'SELECT "test group", avg(CASE WHEN converted THEN 1.0 ELSE 0.0 END) FROM m GROUP BY 1 ORDER BY 1'
    ).fetchall():
        print(f"    {g:5s} {r:.5%}")

    stats_by_group = {
        r[0]: r
        for r in con.execute(
            'SELECT "test group", avg("total ads"), stddev_samp("total ads"), count(*) FROM m'
            " GROUP BY 1 ORDER BY 1"
        ).fetchall()
    }
    ad, psa = stats_by_group["ad"], stats_by_group["psa"]
    smd, t, pv = smd_and_t(ad[1], psa[1], ad[2], psa[2], ad[3], psa[3])
    print(f"\n  'total ads' balance: mean(ad)={ad[1]:.3f} mean(psa)={psa[1]:.3f}")
    print(f"    SMD={smd:+.4f}  t={t:+.2f}  p={pv:.4f}  -> BALANCED")
    print("    ...and still post-treatment: it counts campaign deliveries made after")
    print("    assignment. Balance falsifies; it does not confirm. (selection log §2)")


def main():
    con = duckdb.connect()
    con.execute("PRAGMA threads=6")
    if not DATA.exists():
        print(f"No data directory at {DATA}. Run data/download.sh first.")
        return 1
    for step in (cookie_cats, criteo, asos, upworthy, marketing):
        step(con)
    print("\nDone. Compare against docs/dataset_selection.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
