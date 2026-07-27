"""How often daily peeking manufactures a significant result (SPEC.md §7.6).

This is a simulation, and it has to be, because Cookie Cats has no time dimension — there
are no timestamps to replay, so there is no real sequential analysis available on this
dataset (SPEC.md §5.2). Everything here is calibrated to the real experiment's sample size
and baseline rate, and it simulates the null: **there is no true effect anywhere in it.**
Every "significant" result it produces is a false positive by construction.

Why this appears before the results in the memo rather than in an appendix: it is a
statement of protocol, not a curiosity. "This experiment is read once, at day 7, and here
is the arithmetic showing why" is a commitment made in advance. The same sentence after
the results reads as a justification for the number that was found.

And the failure mode is organisational before it is statistical. Nobody sets out to
p-hack. What happens is that a stakeholder asks "how's the test looking?" on day 2, and
the honest answer — "we don't know yet and looking now makes the final answer worse" — is
socially expensive to give. The chart exists to make that answer cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class PeekingResult:
    n_per_arm: int
    baseline_rate: float
    n_checks: int
    alpha: float
    simulations: int
    single_read_fpr: float
    peeking_fpr: float
    cumulative_fpr: tuple[float, ...]  # false-positive rate after each successive check

    @property
    def inflation(self) -> float:
        return self.peeking_fpr / self.single_read_fpr if self.single_read_fpr else float("nan")

    def render(self) -> str:
        return (
            f"  simulated {self.simulations:,} experiments with NO true effect,\n"
            f"  {self.n_per_arm:,} units per arm, baseline {self.baseline_rate:.2%}, "
            f"alpha {self.alpha}\n"
            f"  read once at the end        false positives {self.single_read_fpr:.2%}\n"
            f"  checked daily for {self.n_checks} days   false positives {self.peeking_fpr:.2%}"
            f"   ({self.inflation:.1f}x)"
        )


def simulate_peeking(
    n_per_arm: int,
    baseline_rate: float,
    *,
    n_checks: int = 14,
    alpha: float = 0.05,
    simulations: int = 10_000,
    seed: int = 20260727,
) -> PeekingResult:
    """Run `simulations` A/A experiments and count how often each reading protocol lies.

    Both arms are drawn from the same rate, so the null is true by construction. Units
    accrue evenly across `n_checks` checkpoints, and at each checkpoint a two-proportion
    z-test runs on the data accumulated *so far* — the cumulative structure is what makes
    successive looks correlated, and it is why the inflation is much less than the
    n_checks x alpha a naive multiple-comparisons argument would predict.
    """
    if not 0 < baseline_rate < 1:
        raise ValueError("baseline_rate must be a proportion in (0,1)")

    rng = np.random.default_rng(seed)
    per_check = n_per_arm // n_checks
    if per_check < 1:
        raise ValueError(f"{n_per_arm} units cannot be split across {n_checks} checkpoints")

    # Per-checkpoint arrivals, accumulated: shape (simulations, n_checks).
    control = np.cumsum(rng.binomial(per_check, baseline_rate, (simulations, n_checks)), axis=1)
    treatment = np.cumsum(rng.binomial(per_check, baseline_rate, (simulations, n_checks)), axis=1)
    n_seen = np.arange(1, n_checks + 1) * per_check

    p_c = control / n_seen
    p_t = treatment / n_seen
    pooled = (control + treatment) / (2 * n_seen)
    se = np.sqrt(pooled * (1 - pooled) * (2 / n_seen))
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(se > 0, (p_t - p_c) / se, 0.0)

    # scipy's norm.sf on a 10k x 14 array is needlessly slow; the two-sided normal
    # threshold is a constant, so compare |z| against it directly.
    from scipy import stats
    critical = stats.norm.ppf(1 - alpha / 2)
    significant = np.abs(z) > critical

    stopped_by_check = np.cumsum(significant, axis=1) > 0
    return PeekingResult(
        n_per_arm=n_per_arm,
        baseline_rate=baseline_rate,
        n_checks=n_checks,
        alpha=alpha,
        simulations=simulations,
        single_read_fpr=float(significant[:, -1].mean()),
        peeking_fpr=float(stopped_by_check[:, -1].mean()),
        cumulative_fpr=tuple(stopped_by_check.mean(axis=0).tolist()),
    )


def write_chart(result: PeekingResult, path: str | Path) -> Path:
    """One chart, roughly half a page (SPEC.md §7.6 caps the scope at this)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    checks = np.arange(1, result.n_checks + 1)
    fig, ax = plt.subplots(figsize=(7.2, 4.0))

    ax.plot(checks, np.array(result.cumulative_fpr) * 100, marker="o", linewidth=2,
            color="#b2182b", label="Checked every day, stop at first p < 0.05")
    ax.axhline(result.single_read_fpr * 100, linestyle="--", linewidth=1.6, color="#2166ac",
               label=f"Read once at the end ({result.single_read_fpr:.1%})")
    ax.axhline(result.alpha * 100, linestyle=":", linewidth=1.2, color="#666666",
               label=f"Nominal false-positive rate ({result.alpha:.0%})")

    ax.set_xlabel("Days the experiment has been running (checked daily)")
    ax.set_ylabel("False positives (%)")
    ax.set_title(
        "Peeking inflates false positives\n"
        f"{result.simulations:,} simulated experiments with no true effect",
        fontsize=11,
    )
    ax.set_xticks(checks)
    ax.set_ylim(0, max(result.cumulative_fpr) * 100 * 1.35)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
