"""The blocking rule (SPEC.md §7.3).

    If any diagnostic returns BLOCK, the readout does not render an effect estimate.
    It renders the diagnostic failure and stops.

This is the most opinionated thing in the system and it is meant to be argued with, so the
argument is written down: a system that hands you a number it does not trust is worse than
no system at all. Not because the number is wrong — a wrong number with a loud caveat is
recoverable — but because numbers travel and caveats do not. The estimate gets pasted into
a deck, the deck outlives the context, and six weeks later a decision rests on a readout
whose own diagnostics said the assignment mechanism was broken.

So suppression is enforced by *not computing*, not by computing and hiding. See
readout/run.py: when the gate blocks, `results` is None and there is no number anywhere in
the process to leak into a surface by accident.

Four states, not three. NOT_APPLICABLE exists because a check that cannot fail must not
report as a pass (docs/decisions.md D-08) — Criteo has no unit identifier, so its duplicate
detection is vacuous, and rendering that as a green tick would tell a reader something
untrue about how hard this system looked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"
    NOT_APPLICABLE = "NOT_APPLICABLE"

    @property
    def severity(self) -> int:
        # NOT_APPLICABLE sits below PASS: it never escalates, and never reassures.
        return {"NOT_APPLICABLE": -1, "PASS": 0, "WARN": 1, "BLOCK": 2}[self.value]

    @property
    def symbol(self) -> str:
        return {"PASS": "PASS", "WARN": "WARN", "BLOCK": "BLOCK", "NOT_APPLICABLE": "n/a"}[self.value]


@dataclass(frozen=True)
class DiagnosticResult:
    name: str
    label: str
    status: Status
    headline: str            # one line, for a table row
    detail: str              # the reasoning, including why a threshold was chosen
    observed: dict = field(default_factory=dict)  # raw numbers, for tests and the scorecard


@dataclass(frozen=True)
class GateDecision:
    status: Status
    results: tuple[DiagnosticResult, ...]

    @property
    def estimates_permitted(self) -> bool:
        """The single question every output surface must ask before rendering a number."""
        return self.status is not Status.BLOCK

    @property
    def blocked_by(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.results if r.status is Status.BLOCK)

    @property
    def warnings(self) -> tuple[DiagnosticResult, ...]:
        """WARNs render adjacent to the estimate, never in a footnote (SPEC.md §7.3)."""
        return tuple(r for r in self.results if r.status is Status.WARN)

    @property
    def not_applicable(self) -> tuple[DiagnosticResult, ...]:
        return tuple(r for r in self.results if r.status is Status.NOT_APPLICABLE)

    def render(self) -> str:
        width = max(len(r.label) for r in self.results)
        lines = [f"Validity diagnostics: {self.status.value}"]
        for r in self.results:
            lines.append(f"  [{r.status.symbol:>5s}] {r.label:<{width}s}  {r.headline}")
        if self.status is Status.BLOCK:
            lines += [
                "",
                "  BLOCKED. No effect estimate is rendered.",
                "  " + ", ".join(self.blocked_by) + " must be resolved before this experiment",
                "  can be read out. See SPEC.md §7.3.",
            ]
        return "\n".join(lines)


def evaluate(results, config=None) -> GateDecision:
    """Reduce diagnostics to one decision: the worst status any check returned."""
    results = tuple(results)
    if not results:
        raise ValueError("gate evaluated with no diagnostics — refusing to imply a pass")
    worst = max(results, key=lambda r: r.status.severity).status
    if worst is Status.NOT_APPLICABLE:
        # Every check was inapplicable. Nothing failed, but nothing was verified either.
        worst = Status.PASS
    return GateDecision(status=worst, results=results)
