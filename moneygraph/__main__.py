"""Batch CLI. The API/UI owner provides the separate application integration."""
import argparse
from datetime import date
from pathlib import Path
import sys
from time import perf_counter

from .config import PipelineConfig


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m moneygraph")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Calculate roles and publish a complete snapshot")
    run.add_argument("--data", type=Path, required=True)
    run.add_argument("--out", type=Path, default=Path("out"))
    run.add_argument("--period-start", type=date.fromisoformat, default=date(2026, 7, 1))
    run.add_argument("--period-end", type=date.fromisoformat, default=date(2026, 7, 31))
    run.add_argument("--resolution", type=float, default=1.0)
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--top-limit", type=int, default=30)
    args = parser.parse_args(argv)
    started = perf_counter()
    try:
        from .pipeline import run_pipeline
        config = PipelineConfig(period_start=args.period_start, period_end=args.period_end,
                                resolution=args.resolution, seed=args.seed, top_limit=args.top_limit)
        snapshot = run_pipeline(args.data, args.out, config=config)
    except (OSError, ValueError, ImportError) as error:
        print(f"Calculation failed: {error}", file=sys.stderr)
        return 1
    print(f"run_id: {snapshot.run_id}")
    print(f"Results: {snapshot.directory}")
    print(f"Elapsed: {perf_counter()-started:.3f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
