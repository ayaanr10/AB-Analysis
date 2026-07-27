"""One command, one config path (SPEC.md §7.10).

    python -m readout.cli readout   --config config/cookie_cats.yaml
    python -m readout.cli memo      --config config/cookie_cats.yaml --out README.md
    python -m readout.cli scorecard --config config/cookie_cats.yaml
    python -m readout.cli diagnose  --config config/cookie_cats.yaml

Every subcommand goes through the same `run_experiment`, so no surface can reach an effect
estimate by a route that skips the gate.

Exit codes: 0 normally, 2 when the gate blocked. A blocked readout is a successful run of
the system and a failed experiment, and CI should be able to tell the difference.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .memo import render_memo
from .run import run_experiment
from .scorecard import render_scorecard

BLOCKED_EXIT = 2


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, help="path to the experiment config YAML")
    parser.add_argument("--database", default=":memory:", help="DuckDB path (default: in-memory)")
    parser.add_argument("--resamples", type=int, default=10_000,
                        help="bootstrap resamples (default: 10,000, per SPEC.md §7.5)")
    parser.add_argument("--no-peeking", action="store_true",
                        help="skip the peeking simulation")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="readout", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("readout", "full readout to the terminal"),
        ("memo", "generate the decision memo (markdown)"),
        ("scorecard", "dense side-by-side scorecard"),
        ("diagnose", "validity diagnostics only — never computes an effect"),
    ):
        p = sub.add_parser(name, help=help_text)
        _add_common(p)
        if name == "memo":
            p.add_argument("--out", type=Path, help="write here instead of stdout")

    args = parser.parse_args(argv)

    readout = run_experiment(
        args.config,
        database=args.database,
        resamples=args.resamples,
        peeking=not args.no_peeking,
    )

    if args.command == "diagnose":
        print(readout.load_report.render())
        print()
        print(readout.gate.render())
    elif args.command == "readout":
        print(readout.render_cli())
    elif args.command == "scorecard":
        print(render_scorecard(readout))
    elif args.command == "memo":
        text = render_memo(readout)
        if args.out:
            args.out.write_text(text)
            print(f"wrote {args.out} ({len(text.splitlines()):,} lines)", file=sys.stderr)
        else:
            print(text)

    return BLOCKED_EXIT if readout.blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
