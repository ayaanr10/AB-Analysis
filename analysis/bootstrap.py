"""Bootstrap confidence intervals (SPEC.md §7.5, 10,000 resamples).

Why bootstrap at all, when a two-proportion z-interval would do: it makes fewer
assumptions, and it is far easier to explain to the people who have to act on the readout.
"We re-ran the experiment 10,000 times using only the players we actually observed, and
95% of those re-runs landed between −1.4pp and −0.2pp" needs no distributional argument.

Two resampling paths, because the honest naive implementation does not survive contact
with a 14M-row dataset.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEFAULT_RESAMPLES = 10_000
DEFAULT_CONFIDENCE = 0.95

# Above this, index-based resampling costs O(B*n) gathers and stops being sensible.
# Only reached by mean_value metrics; binary metrics take the exact path at any n.
LARGE_SAMPLE = 2_000_000


@dataclass(frozen=True)
class BootstrapInterval:
    point: float
    low: float
    high: float
    resamples: int
    confidence: float
    method: str

    @property
    def excludes_zero(self) -> bool:
        return (self.low > 0) or (self.high < 0)


def _percentiles(draws: np.ndarray, confidence: float) -> tuple[float, float]:
    alpha = (1.0 - confidence) / 2.0
    return tuple(np.quantile(draws, [alpha, 1.0 - alpha]))


def _binary_draws(successes: int, n: int, rng, resamples: int) -> np.ndarray:
    """Exact bootstrap distribution of the mean of n Bernoulli values.

    Resampling n values with replacement from a sample containing `successes` ones and
    `n - successes` zeros gives a resample sum distributed exactly Binomial(n, successes/n).
    So this is not an approximation of the bootstrap — it *is* the bootstrap, computed in
    closed form. It costs the same whether n is 90 thousand or 14 million, which is what
    makes 10,000 resamples affordable on Criteo.
    """
    return rng.binomial(n, successes / n, size=resamples) / n


def _value_draws(values: np.ndarray, rng, resamples: int) -> np.ndarray:
    """Index-resampling bootstrap for arbitrary unit-level values.

    Chunked: the full index matrix for 10,000 resamples of 90k units would be ~7 GB, so
    replicates are drawn in batches and only their means are kept.
    """
    n = values.size
    out = np.empty(resamples, dtype=np.float64)
    batch = max(1, min(resamples, 20_000_000 // max(n, 1)))
    for start in range(0, resamples, batch):
        size = min(batch, resamples - start)
        idx = rng.integers(0, n, size=(size, n))
        out[start:start + size] = values[idx].mean(axis=1)
    return out


def bootstrap_difference(
    control_values: np.ndarray | None,
    treatment_values: np.ndarray | None,
    *,
    control_summary: tuple[int, int] | None = None,
    treatment_summary: tuple[int, int] | None = None,
    binary: bool = False,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    relative: bool = False,
    seed: int = 20260727,
) -> BootstrapInterval:
    """CI for treatment mean minus control mean (or the relative version).

    Pass unit-level arrays, or — for binary metrics — `(successes, n)` summaries and
    `binary=True`, which takes the closed-form path and never materialises the units.

    The arms are resampled independently, which is the right structure here: units were
    randomised into one arm or the other, so the two samples are independent and the
    uncertainty in each contributes separately.
    """
    rng = np.random.default_rng(seed)

    if binary:
        if control_summary is None or treatment_summary is None:
            raise ValueError("binary=True requires (successes, n) summaries for both arms")
        c_success, c_n = control_summary
        t_success, t_n = treatment_summary
        control_draws = _binary_draws(c_success, c_n, rng, resamples)
        treatment_draws = _binary_draws(t_success, t_n, rng, resamples)
        point_c, point_t = c_success / c_n, t_success / t_n
        method = f"exact binomial bootstrap, {resamples:,} resamples"
    else:
        if control_values is None or treatment_values is None:
            raise ValueError("non-binary bootstrap requires unit-level values for both arms")
        if control_values.size > LARGE_SAMPLE or treatment_values.size > LARGE_SAMPLE:
            raise ValueError(
                f"index resampling refused above {LARGE_SAMPLE:,} units "
                f"(got {max(control_values.size, treatment_values.size):,}). "
                "Use a binary metric, or add an explicit subsampling policy — silently "
                "reducing the resample count would understate the interval."
            )
        control_draws = _value_draws(control_values, rng, resamples)
        treatment_draws = _value_draws(treatment_values, rng, resamples)
        point_c, point_t = float(control_values.mean()), float(treatment_values.mean())
        method = f"index-resampling bootstrap, {resamples:,} resamples"

    if relative:
        # A relative effect is undefined when a resampled control rate is 0. That never
        # happens at realistic sample sizes, but on a tiny arm it does, and dividing
        # anyway produces inf/nan that quietly poison the percentiles. Those draws are
        # dropped and the loss is recorded rather than hidden.
        usable = control_draws != 0
        kept = int(usable.sum())
        if kept == 0:
            return BootstrapInterval(float("nan"), float("nan"), float("nan"),
                                     resamples, confidence,
                                     f"{method}; relative effect undefined (control rate 0)")
        draws = (treatment_draws[usable] - control_draws[usable]) / control_draws[usable]
        point = (point_t - point_c) / point_c if point_c else float("nan")
        if kept < resamples:
            method += f"; {resamples - kept:,} draw(s) dropped where control resampled to 0"
    else:
        draws = treatment_draws - control_draws
        point = point_t - point_c

    low, high = _percentiles(draws, confidence)
    return BootstrapInterval(
        point=point, low=float(low), high=float(high),
        resamples=resamples, confidence=confidence, method=method,
    )
