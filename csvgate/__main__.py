"""Command-line interface for reproducible CSV quality gates."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import DataError, compare, load_profile, profile, write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile CSV quality and compare against a baseline")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("profile", help="create a JSON quality profile")
    make.add_argument("source", type=Path)
    make.add_argument("output", type=Path)
    make.add_argument("--numeric", action="append", default=[], metavar="COLUMN")
    make.add_argument("--category", action="append", default=[], metavar="COLUMN")
    make.add_argument("--key", action="append", default=[], metavar="COLUMN")
    make.add_argument("--force", action="store_true")

    gate = commands.add_parser("compare", help="compare current profile with a baseline")
    gate.add_argument("baseline", type=Path)
    gate.add_argument("current", type=Path)
    gate.add_argument("output", type=Path)
    gate.add_argument("--max-missing-increase", type=float, default=0.05)
    gate.add_argument("--max-mean-shift-sd", type=float, default=3.0)
    gate.add_argument("--max-new-category-rate", type=float, default=0.01)
    gate.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "profile":
            result = profile(args.source, args.numeric, args.category, args.key)
            write_json(args.output, result, (args.source,), args.force)
            print(f"Profiled {result['rows']} rows and {len(result['columns'])} columns")
            return 0
        result = compare(load_profile(args.baseline), load_profile(args.current), args.max_missing_increase, args.max_mean_shift_sd, args.max_new_category_rate)
        write_json(args.output, result, (args.baseline, args.current), args.force)
        for violation in result["violations"]:
            print(violation)
        if result["passed"]:
            print("OK: current CSV profile passed the quality gate")
            return 0
        return 1
    except DataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
