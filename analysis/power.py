"""Power and minimum detectable effect (SPEC.md §7.4).

Computed from sample size and baseline rate alone — it never touches the observed effect.
That is the entire point of reporting it *before* results: an MDE calculated after seeing
the answer is a rationalisation, and post-hoc "observed power" is a known statistical
error (it is a deterministic function of the p-value and tells you nothing new).

What the MDE is for: it answers "could this experiment have detected an effect worth
caring about, if one existed?" A null result from an experiment whose MDE is larger than
the effect the business cares about is not evidence of no effect. It is evidence that the
experiment was not capable of answering its own question — a different and much less
useful statement, and one teams routinely misread as a green light.
"""

from __future__ import annotations

from dataclasses import dataclass

from scipy import stats

DEFAULT_ALPHA = 0.05
DEFAULT_POWER = 0.80


@dataclass(frozen=True)
class PowerResult:
    baseline_rate: float
    n_control: int
    n_treatment: int
    alpha: float
    power: float
    mde_absolute: float
    mde_relative: float
    practical_effect: float | None

    @property
    def can_answer_its_own_question(self) -> bool | None:
        """Whether the experiment could detect the effect the business cares about."""
        if self.practical_effect is None:
            return None
        return self.mde_relative <= self.practical_effect

    def render(self) -> str:
        lines = [
            f"  baseline rate        {self.baseline_rate:.4%}",
            f"  sample               {self.n_control:,} control / {self.n_treatment:,} treatment",
            f"  alpha {self.alpha}, power {self.power:.0%}",
            f"  minimum detectable   {self.mde_absolute:+.4%} absolute "
            f"({self.mde_relative:.2%} relative)",
        ]
        if self.practical_effect is not None:
            verdict = "YES" if self.can_answer_its_own_question else "NO"
            lines.append(
                f"  worth acting on      {self.practical_effect:.2%} relative"
                f"  ->  could this experiment have detected it? {verdict}"
            )
        return "\n".join(lines)


def minimum_detectable_effect(
    baseline_rate: float,
    n_control: int,
    n_treatment: int,
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
    practical_effect: float | None = None,
) -> PowerResult:
    """Smallest absolute difference in rates detectable at `alpha` with `power`.

    Standard two-sample normal approximation:

        MDE = (z_{1-alpha/2} + z_{power}) * sqrt(p(1-p) * (1/n_c + 1/n_t))

    The variance is evaluated at the baseline rate rather than at each arm's own rate,
    since the treatment rate is unknown at design time — which is when this number is
    supposed to be computed.
    """
    if not 0 < baseline_rate < 1:
        raise ValueError(f"baseline_rate must be a proportion strictly in (0,1), got {baseline_rate}")
    if n_control <= 0 or n_treatment <= 0:
        raise ValueError("both arms need a positive sample size")

    z_alpha = stats.norm.ppf(1 - alpha / 2)
    z_power = stats.norm.ppf(power)
    se = (baseline_rate * (1 - baseline_rate) * (1 / n_control + 1 / n_treatment)) ** 0.5
    mde_absolute = (z_alpha + z_power) * se

    return PowerResult(
        baseline_rate=baseline_rate,
        n_control=n_control,
        n_treatment=n_treatment,
        alpha=alpha,
        power=power,
        mde_absolute=mde_absolute,
        mde_relative=mde_absolute / baseline_rate,
        practical_effect=practical_effect,
    )


def required_sample_size(
    baseline_rate: float,
    relative_effect: float,
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
) -> int:
    """Units per arm needed to detect `relative_effect` — the inverse question.

    Used in the memo to say what a re-run would cost, rather than just noting that the
    experiment was underpowered and leaving the reader with nowhere to go.
    """
    z_alpha = stats.norm.ppf(1 - alpha / 2)
    z_power = stats.norm.ppf(power)
    delta = baseline_rate * relative_effect
    return int(
        ((z_alpha + z_power) ** 2 * 2 * baseline_rate * (1 - baseline_rate)) / (delta**2)
    ) + 1
