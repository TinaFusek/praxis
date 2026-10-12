#!/usr/bin/env python3
"""
Estimate what a variable-length traversal will cost, before running it.

A human writes a traversal once and reviews it. An agent, or a generated query,
writes a new one on every call. Something has to decide, cheaply, whether the
expensive query is worth starting.

The obvious signal is the start node's degree. Measured across 20 start nodes it
has a 10.6x spread against observed path counts and sometimes ranks nodes
backwards, because degree says how many neighbours a node has, not what those
neighbours are. A degree-5 node whose neighbours are hubs has an enormous second
frontier.

Sum of neighbour degrees drops the spread to 1.7x at a comparable cost, because
each neighbour's degree is read from node metadata with no database access.

    frontier ~= SUM over neighbours m of degree(m)
    paths(depth) ~= frontier * k^(depth-2)

k is a property of the graph, not of the method. Calibrated values so far:
1.0 on a star, 2-3 on a sparse ownership graph, 5-8 on a causal DAG, 26 on a
payment graph at depth 3, 533-780 on a small-world graph at depth 4. Three
orders of magnitude apart, so a threshold copied between deployments is wrong
by a hundredfold. Calibrate per graph, and re-calibrate when the data grows.

    export NEO4J_PASSWORD=...
    python traversal_budget.py --calibrate --label <LABEL> --rel <REL> \\
                               --key <KEY> --depth 4
    python traversal_budget.py --check <ID> --label <LABEL> --rel <REL> \\
                               --key <KEY> --depth 4 --k 550 --budget 1000000

<LABEL>, <REL> and <KEY> are placeholders. Read the real ones out of
graph_profile.py before running this — the labels of some other graph will
produce "no nodes with a `uid` property" and nothing else.

Three things this script got wrong until a blind-graph exercise found them:

  * A frontier of zero was counted as a failed probe. It is not a failure, it
    is a measurement: that start node has no second-hop neighbours.
  * The spread threshold was applied regardless of magnitude. A 3.9x spread
    over path counts of 2, 3 and 9 is noise; the same 3.9x over millions is a
    finding. The rule now requires a minimum magnitude before it fires.
  * The probe cost was quoted as a property of the method. It is a property of
    the cache: 15.7 ms warm, 2 174 ms on a cold first call against the same
    graph. Cold and warm are now reported separately.
"""

import argparse
import os
import statistics as st
import sys
import time

from neo4j import GraphDatabase

# Below this many paths, ratios between start nodes are dominated by noise and
# the spread test says nothing. Raise it if your graph is large.
DEFAULT_MIN_MAGNITUDE = 1_000


def frontier(session, label, rel, key, uid):
    """One hop out, then each neighbour's degree from metadata. Cheap, and its
    cost does not grow with degree — but the first call against a cold cache is
    two orders of magnitude slower than the rest."""
    t0 = time.perf_counter()
    rec = session.run(
        f"MATCH (n:`{label}` {{{key}: $uid}})-[:`{rel}`]-(m) "
        f"RETURN sum(COUNT {{ (m)-[:`{rel}`]-() }}) AS frontier", uid=uid).single()
    return (rec["frontier"] or 0) if rec else 0, (time.perf_counter() - t0) * 1000


def count_paths(session, label, rel, key, uid, depth, timeout):
    t0 = time.perf_counter()
    try:
        rec = session.run(
            f"MATCH (n:`{label}` {{{key}: $uid}}) "
            f"MATCH p = (n)-[:`{rel}`*1..{depth}]-(m) RETURN count(p) AS paths",
            {"uid": uid}, timeout=timeout).single()
        return rec["paths"], (time.perf_counter() - t0) * 1000, "ok"
    except Exception as e:
        return None, (time.perf_counter() - t0) * 1000, type(e).__name__


def sample_starts(session, label, key, n, seed):
    rows = session.run(
        f"MATCH (n:`{label}`) RETURN n.`{key}` AS uid ORDER BY n.`{key}`").data()
    rows = [r for r in rows if r["uid"] is not None]
    if not rows:
        return []
    stride = max(1, len(rows) // n)
    return [r["uid"] for r in rows[(seed % stride)::stride][:n]]


def calibrate(session, args):
    starts = sample_starts(session, args.label, args.key, args.starts, args.seed)
    if not starts:
        sys.exit(f"no :{args.label} nodes with a `{args.key}` property — "
                 f"read the real label and key out of graph_profile.py")

    print(f"calibrating on {len(starts)} start nodes, depth {args.depth}\n")
    hdr = (f"{'start':<20}{'frontier':>12}{'probe ms':>10}"
           f"{'paths':>14}{'ms':>9}{'ratio':>12}   status")
    print(hdr)
    print("-" * len(hdr))

    ratios, paths_seen, probe_ms = [], [], []
    n_errors, n_zero_frontier = 0, 0

    for uid in starts:
        f, pms = frontier(session, args.label, args.rel, args.key, uid)
        probe_ms.append(pms)
        paths, ms, status = count_paths(
            session, args.label, args.rel, args.key, uid, args.depth, args.timeout)

        # Three outcomes, and they are not the same thing. The old version
        # collapsed the last two into "failed" and printed the status string in
        # the paths column, which is how a zero frontier came to be reported as
        # a failure with the word "ok" under "paths".
        if status != "ok":
            n_errors += 1
            print(f"{str(uid):<20}{f:>12,}{pms:>10.1f}{'-':>14}{ms:>9.0f}"
                  f"{'-':>12}   {status}")
            continue
        if not f:
            n_zero_frontier += 1
            paths_seen.append(paths or 0)
            print(f"{str(uid):<20}{f:>12,}{pms:>10.1f}{(paths or 0):>14,}{ms:>9.0f}"
                  f"{'n/a':>12}   zero frontier")
            continue

        ratio = paths / f
        ratios.append(ratio)
        paths_seen.append(paths)
        print(f"{str(uid):<20}{f:>12,}{pms:>10.1f}{paths:>14,}{ms:>9.0f}"
              f"{ratio:>12,.1f}   ok")

    print("-" * len(hdr))

    if not ratios:
        print("\nno start node produced a usable ratio.")
        if n_zero_frontier:
            print(f"  {n_zero_frontier} had a frontier of zero — no second-hop")
            print("  neighbours. That is a fact about the graph, not an error:")
            print("  this label is sparsely connected through this relationship.")
        if n_errors:
            print(f"  {n_errors} probe(s) errored — lower --depth or raise --timeout")
        sys.exit(1)

    lo, hi = min(ratios), max(ratios)
    spread = hi / lo
    median_paths = st.median(paths_seen) if paths_seen else 0

    print(f"\nk (paths per unit of frontier, depth {args.depth})")
    print(f"  median {st.median(ratios):,.1f}   min {lo:,.1f}   max {hi:,.1f}"
          f"   spread {spread:.1f}x")
    print(f"  measured on {len(ratios)} start node(s) of {len(starts)} sampled")

    # Probe cost: the first call is cold. Reporting one median hides that by a
    # factor of ~100.
    if probe_ms:
        print(f"  probe cost: first (cold) {probe_ms[0]:.1f} ms", end="")
        if len(probe_ms) > 1:
            print(f"   median of the rest (warm) {st.median(probe_ms[1:]):.1f} ms")
        else:
            print()

    if n_zero_frontier:
        print(f"  {n_zero_frontier} start node(s) had a frontier of zero — "
              f"excluded from k, not failures")
    if n_errors:
        print(f"  {n_errors} start node(s) errored — counted, not dropped")

    print()
    if median_paths < args.min_magnitude:
        print(f"MAGNITUDE TOO LOW for the spread test.")
        print(f"  median path count {median_paths:,.0f}, threshold "
              f"{args.min_magnitude:,}")
        print("  At this scale a ratio of 1 versus 4 is two paths versus eight.")
        print("  The spread figure above is noise, not evidence about the")
        print("  predictor. Either this traversal is cheap enough that it needs")
        print("  no budget, or calibrate at a greater depth.")
    elif spread > args.max_spread:
        print(f"SPREAD {spread:.1f}x — above {args.max_spread}x, and the "
              f"magnitude is large enough for that to mean something.")
        print("  The predictor does not explain the cost on this graph.")
        print("  Do not use this k as a budget gate; measure instead.")
    else:
        print(f"USABLE — spread {spread:.1f}x at a median of {median_paths:,.0f} "
              f"paths.")
        print(f"\n  --k {st.median(ratios):.0f}")
        print("\n  Re-calibrate when the graph grows. Any --check on one of the")
        print("  start nodes above is measured on the set k was tuned on, so it")
        print("  is optimistic; quote a held-out node instead.")


def check(session, args):
    if args.k is None:
        sys.exit("--check needs --k; run --calibrate first")

    f, pms = frontier(session, args.label, args.rel, args.key, args.check)
    if args.depth <= 2:
        est = f
    else:
        est = f * (args.k ** (args.depth - 2))

    print(f"start node   {args.check}")
    print(f"frontier     {f:,}          (probe {pms:.1f} ms — cold if this is")
    print(f"                             the first call against this graph)")
    print(f"depth        {args.depth}")
    print(f"k            {args.k:,.0f}")
    print(f"estimate     {est:,.0f} paths")
    print(f"budget       {args.budget:,}")
    print()

    if f == 0:
        print("FRONTIER ZERO — this node has no second-hop neighbours through")
        print("this relationship. The traversal is free and returns little.")
        return 0

    if est <= args.budget:
        print("WITHIN BUDGET — run the path-oriented traversal")
        return 0

    over = est / args.budget
    print(f"OVER BUDGET by {over:.1f}x — do not run this as written")
    print()
    print("Options, cheapest first:")
    safe = args.depth
    while safe > 1 and f * (args.k ** max(0, safe - 2)) > args.budget:
        safe -= 1
    print(f"  1. cap the depth at {safe}")
    print("  2. switch to node-oriented traversal — a visited set saturates at")
    print("     the reachable node count instead of the path count")
    print("  3. exclude the highest-degree node on the path, if the profile")
    print("     showed one; it is usually what the estimate is made of")
    print("  4. filter the relationships inside the pattern, not after it")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    ap.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    ap.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", "neo4j"))
    ap.add_argument("--label", required=True, metavar="LABEL")
    ap.add_argument("--rel", required=True, metavar="REL")
    ap.add_argument("--key", default="uid", metavar="KEY",
                    help="business key property — read it out of the profile")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--check", metavar="ID")
    ap.add_argument("--k", type=float, help="calibrated multiplier")
    ap.add_argument("--budget", type=int, default=1_000_000)
    ap.add_argument("--starts", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-spread", type=float, default=2.0,
                    help="spread above this means the predictor does not hold")
    ap.add_argument("--min-magnitude", type=int, default=DEFAULT_MIN_MAGNITUDE,
                    help="below this median path count the spread test is noise")
    args = ap.parse_args()

    if not args.calibrate and not args.check:
        sys.exit("use --calibrate or --check <id>")
    pw = os.environ.get("NEO4J_PASSWORD")
    if not pw:
        sys.exit("set NEO4J_PASSWORD in the environment")

    driver = GraphDatabase.driver(args.uri, auth=(args.user, pw))
    with driver.session(database=args.database) as s:
        rc = calibrate(s, args) if args.calibrate else check(s, args)
    driver.close()
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
