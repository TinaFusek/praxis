#!/usr/bin/env python3
"""
Run a command N times on unchanged input and check the output is identical.

Non-determinism is the defect that does not announce itself. The run succeeds,
the number looks plausible, and it is a different number than last time. Nobody
notices until two people compare results.

Common causes, all of which this catches:
  - iterating a set or dict whose order is not fixed (Python randomises string
    hashes between processes, so this differs across runs, not within one)
  - ties broken by whatever arrived first
  - a timestamp, a temp path or a random seed leaking into the output
  - a query without ORDER BY where the engine is free to reorder

Usage:
    python determinism_check.py -- python scripts/blocking.py --data ./data
    python determinism_check.py --runs 5 --ignore '\\d{4}-\\d{2}-\\d{2}' -- ./run.sh
"""

import argparse
import difflib
import re
import subprocess
import sys


def normalise(text, patterns):
    """Blank out things that are allowed to differ — timestamps, durations,
    paths. Everything else must match exactly."""
    for p in patterns:
        text = re.sub(p, "<ignored>", text)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--ignore", action="append", default=[],
                    help="regex for output that is allowed to vary; repeatable")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="command to run, after --")
    args = ap.parse_args()

    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        sys.exit("give the command after --, e.g.  determinism_check.py -- python x.py")

    outputs = []
    for i in range(args.runs):
        print(f"run {i + 1}/{args.runs}: {' '.join(cmd)}", file=sys.stderr)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            sys.exit(f"run {i + 1} timed out after {args.timeout}s")
        if r.returncode != 0:
            print(r.stderr[-2000:], file=sys.stderr)
            sys.exit(f"run {i + 1} exited {r.returncode} — fix that before checking determinism")
        outputs.append(r.stdout)

    baseline = normalise(outputs[0], args.ignore)
    drifted = []
    for i, o in enumerate(outputs[1:], start=2):
        if normalise(o, args.ignore) != baseline:
            drifted.append(i)

    if not drifted:
        print(f"\nDETERMINISTIC — {args.runs} runs, identical output")
        return 0

    print(f"\nNON-DETERMINISTIC — runs {drifted} differ from run 1\n")
    diff = difflib.unified_diff(
        baseline.splitlines(keepends=True),
        normalise(outputs[drifted[0] - 1], args.ignore).splitlines(keepends=True),
        fromfile="run 1", tofile=f"run {drifted[0]}", n=1,
    )
    shown = 0
    for line in diff:
        print(line.rstrip())
        shown += 1
        if shown > 60:
            print("... (truncated)")
            break

    print("\nWhere to look, in this order:")
    print("  1. iteration over a set or dict — sort it")
    print("  2. ties in a sort — add a stable secondary key")
    print("  3. a timestamp or path in the output — add it to --ignore if it is benign")
    print("  4. a query without ORDER BY")
    return 1


if __name__ == "__main__":
    sys.exit(main())
