#!/usr/bin/env python3
"""
Profile an unfamiliar graph before doing anything else with it.

This is the first thing to run against a graph somebody else built. It answers,
in one pass: what is in here, what shape are the values, how big is it, where
will traversals explode, what is missing, and is it one graph or several.

It does not modify data. With --components it creates a named in-memory GDS
projection and drops it again in the same run; that is the one thing it writes,
and it writes it to the graph catalog, not to the store. Use --no-components to
skip it entirely.

Order matters, and each answer changes what the next one means:

  1. context      am I where I think I am
  2. counts       everything below is meaningless without these
  3. schema       what identity is enforced, and on which labels
  4. degree       decides whether a traversal is affordable at all
  5. components   is this one graph — a whole-graph question, cheap, and
                  invisible to every node-level check
  6. completeness the finding people forget to look for and then build on
  7. value shape  what is IN the properties, not just whether they are there

Steps 5 and 7 were added after a blind-graph exercise in which four findings
were invisible to steps 1-4 and 6.

    export NEO4J_PASSWORD=...        # never pass a password in argv
    python graph_profile.py --database neo4j
    python graph_profile.py --database neo4j --json > profile.json
    python graph_profile.py --database neo4j --quick        # skip value shape
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from neo4j import GraphDatabase


def scalar(session, query, key, default=None, **params):
    """Run a query that returns one value. Never let a failed probe kill the run —
    a missing procedure or a permission gap is itself a finding."""
    try:
        rec = session.run(query, params).single()
        return rec[key] if rec else default
    except Exception as e:
        return f"unavailable: {type(e).__name__}"


def rows(session, query, **params):
    try:
        return session.run(query, params).data()
    except Exception as e:
        return [{"error": f"{type(e).__name__}: {e}"}]


def failed(result):
    """True if a rows() call came back as an error rather than data."""
    return bool(result) and isinstance(result[0], dict) and "error" in result[0]


# --------------------------------------------------------------------------- #
# 5. components
# --------------------------------------------------------------------------- #

def components(session, graph_name):
    """Weakly connected components over the whole graph.

    This belongs in the profile, not in analysis. It is one call, it costs
    seconds, and it is the only way to see a subgraph that connects to nothing
    — which no sample, no spot check and no traversal will ever reach.
    """
    out = {"available": False}

    probe = rows(session, "CALL gds.version() YIELD version RETURN version")
    if failed(probe):
        out["note"] = ("GDS not installed — component structure not assessed. "
                       "This is a gap in the profile, not an absence of finding.")
        return out

    rows(session, "CALL gds.graph.drop($g, false) YIELD graphName RETURN graphName",
         g=graph_name)
    proj = rows(session, """
        CALL gds.graph.project($g, '*',
          {ALL: {type: '*', orientation: 'UNDIRECTED'}})
        YIELD graphName, nodeCount, relationshipCount
        RETURN graphName, nodeCount, relationshipCount
    """, g=graph_name)
    if failed(proj):
        out["note"] = f"projection failed: {proj[0]['error']}"
        return out

    try:
        out["available"] = True
        out["projection"] = proj[0]
        out["sizes"] = rows(session, """
            CALL gds.wcc.stream($g) YIELD nodeId, componentId
            RETURN componentId, count(*) AS size ORDER BY size DESC LIMIT 25
        """, g=graph_name)
        # What is inside the components that are neither the main one nor a
        # single orphan. This is the part worth reading.
        # collect ids, not nodes: only the six sampled per component are
        # materialised, so this stays cheap on a large main component.
        out["detail"] = rows(session, """
            CALL gds.wcc.stream($g) YIELD nodeId, componentId
            WITH componentId, collect(nodeId) AS ids
            WITH componentId, ids, size(ids) AS sz
            ORDER BY sz DESC SKIP 1 LIMIT 10
            WHERE sz > 1
            RETURN componentId, sz AS size,
                   [n IN [i IN ids[..6] | gds.util.asNode(i)] |
                    labels(n)[0] + ' ' +
                    coalesce(n.cid, n.aid, n.pid, n.uid, n.id, '?') + ' ' +
                    coalesce(n.name, '')] AS sample
        """, g=graph_name)
    finally:
        rows(session, "CALL gds.graph.drop($g, false) YIELD graphName RETURN graphName",
             g=graph_name)

    return out


# --------------------------------------------------------------------------- #
# 6. completeness — null, blank and present are three different states
# --------------------------------------------------------------------------- #

def completeness(session, labels):
    """A property can be empty in more than one way.

    'IS NOT NULL' alone reports an empty string as populated. Whether a blank
    field arrives as null or as '' is decided by whoever wrote the loader, and
    you will usually not be that person. LOAD CSV, for instance, converts empty
    fields to null — so blanks are rare after a CSV load and common after an
    application wrote the data directly.

    A completeness figure that does not say which state it counted is not a
    number.
    """
    out = []
    for row in labels if isinstance(labels, list) else []:
        label = row.get("label")
        if not label:
            continue
        total = row["count"]
        keys = rows(session, f"""
            MATCH (n:`{label}`) WITH n LIMIT 1000
            UNWIND keys(n) AS k RETURN DISTINCT k ORDER BY k
        """)
        if failed(keys):
            continue
        klist = [k["k"] for k in keys if k.get("k")]
        if not klist:
            continue
        res = rows(session, f"""
            UNWIND $keys AS k
            MATCH (n:`{label}`)
            RETURN k AS property,
                   count(*) AS total,
                   count(n[k]) AS not_null,
                   sum(CASE WHEN n[k] IS NOT NULL
                             AND trim(toString(n[k])) = '' THEN 1 ELSE 0 END) AS blank
        """, keys=klist)
        if failed(res):
            continue
        for r in res:
            present = r["not_null"] - r["blank"]
            out.append({
                "label": label,
                "property": r["property"],
                "total": total,
                "null": total - r["not_null"],
                "blank": r["blank"],
                "present": present,
                "pct_present": round(100.0 * present / total, 1) if total else None,
            })
    return sorted(out, key=lambda r: (r["pct_present"] is None, r["pct_present"]))


# --------------------------------------------------------------------------- #
# 7. value shape — what is IN the property
# --------------------------------------------------------------------------- #

def value_shape(session, labels, max_distinct_to_list=12):
    """Distinct counts, low-cardinality value lists, and string length classes.

    Three cheap questions that the completeness check cannot answer:

      How many distinct values?  A technical key with duplicates, or a field
      with ten values for a thousand rows, are both findings.

      What are they, if there are few?  Leftover test rows hide here.

      How long are they?  Grouping a string property by length separates date
      formats, identifier formats and outliers without knowing in advance what
      the formats are. It is coarse — two formats of the same length stay
      merged — so treat a single length class as unverified, not as proof.
    """
    out = []
    for row in labels if isinstance(labels, list) else []:
        label = row.get("label")
        if not label:
            continue
        total = row["count"]
        keys = rows(session, f"""
            MATCH (n:`{label}`) WITH n LIMIT 1000
            UNWIND keys(n) AS k RETURN DISTINCT k ORDER BY k
        """)
        if failed(keys):
            continue
        for kr in keys:
            k = kr.get("k")
            if not k:
                continue
            d = scalar(session,
                       f"MATCH (n:`{label}`) RETURN count(DISTINCT n.`{k}`) AS c",
                       "c", default=None)
            if not isinstance(d, int):
                continue
            entry = {"label": label, "property": k, "total": total, "distinct": d}

            if d <= max_distinct_to_list:
                vals = rows(session, f"""
                    MATCH (n:`{label}`) WHERE n.`{k}` IS NOT NULL
                    RETURN n.`{k}` AS value, count(*) AS n ORDER BY n DESC LIMIT 20
                """)
                if not failed(vals):
                    entry["values"] = vals

            lens = rows(session, f"""
                MATCH (n:`{label}`)
                WHERE n.`{k}` IS NOT NULL AND n.`{k}` IS :: STRING
                RETURN size(n.`{k}`) AS len, count(*) AS n,
                       collect(n.`{k}`)[0] AS example
                ORDER BY n DESC LIMIT 8
            """)
            if failed(lens):
                lens = rows(session, f"""
                    MATCH (n:`{label}`)
                    WHERE n.`{k}` IS NOT NULL AND toString(n.`{k}`) = n.`{k}`
                    RETURN size(n.`{k}`) AS len, count(*) AS n,
                           collect(n.`{k}`)[0] AS example
                    ORDER BY n DESC LIMIT 8
                """)
            if not failed(lens) and lens:
                entry["length_classes"] = lens

            out.append(entry)
    return out


# --------------------------------------------------------------------------- #

def profile(session, args):
    out = {
        "profiled_at": datetime.now(timezone.utc).isoformat(),
        "database": scalar(session, "CALL db.info() YIELD name RETURN name", "name"),
    }

    # --- 1. context --------------------------------------------------------
    out["components_version"] = rows(session, """
        CALL dbms.components() YIELD name, versions, edition
        RETURN name, versions[0] AS version, edition
    """)

    # --- 2. counts ---------------------------------------------------------
    out["labels"] = rows(session, """
        MATCH (n) UNWIND labels(n) AS label
        RETURN label, count(*) AS count ORDER BY count DESC
    """)
    out["relationship_types"] = rows(session, """
        MATCH ()-[r]->()
        RETURN type(r) AS type, count(*) AS count ORDER BY count DESC
    """)
    out["totals"] = {
        "nodes": scalar(session, "MATCH (n) RETURN count(n) AS c", "c"),
        "relationships": scalar(session, "MATCH ()-[r]->() RETURN count(r) AS c", "c"),
    }

    # --- 3. schema ---------------------------------------------------------
    out["constraints"] = rows(session, """
        SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties
        RETURN name, type, labelsOrTypes, properties
    """)
    out["indexes"] = rows(session, """
        SHOW INDEXES YIELD name, type, labelsOrTypes, properties, state
        RETURN name, type, labelsOrTypes, properties, state
    """)

    # --- 4. degree ---------------------------------------------------------
    # percentileCont is a native aggregation: no APOC, and no collecting every
    # degree into one list in memory. The earlier version used apoc.coll.sort,
    # which is deprecated and did both.
    #
    # Read max and p99. The mean is useless here: a graph with a mean degree of
    # 5 and a max of 15 000 is not a graph with a mean degree of 5.
    out["degree"] = rows(session, """
        MATCH (n)
        WITH COUNT { (n)--() } AS deg
        RETURN count(*) AS nodes,
               min(deg) AS min,
               percentileCont(deg, 0.50) AS p50,
               percentileCont(deg, 0.95) AS p95,
               percentileCont(deg, 0.99) AS p99,
               max(deg) AS max,
               round(avg(deg), 2) AS mean
    """)
    out["top_degree_nodes"] = rows(session, """
        MATCH (n)
        WITH n, COUNT { (n)--() } AS deg
        ORDER BY deg DESC LIMIT 15
        RETURN labels(n) AS labels, deg AS degree,
               [k IN keys(n) WHERE k IN ['uid','id','cid','aid','pid','name','code']
                | k + '=' + toString(n[k])] AS hint
    """)
    out["isolated_nodes"] = scalar(session, """
        MATCH (n) WHERE NOT (n)--() RETURN count(n) AS c
    """, "c")

    # --- 5. components -----------------------------------------------------
    out["wcc"] = ({"available": False, "note": "skipped (--no-components)"}
                  if args.no_components else components(session, args.graph_name))

    # --- 6. completeness ---------------------------------------------------
    out["property_completeness"] = completeness(session, out["labels"])

    # --- 7. value shape ----------------------------------------------------
    out["value_shape"] = ([] if args.quick
                          else value_shape(session, out["labels"]))

    return out


def report(p):
    def line(s=""):
        print(s)

    line(f"database: {p['database']}")
    for c in p["components_version"] if isinstance(p["components_version"], list) else []:
        if "name" in c:
            line(f"  {c['name']} {c.get('version')} ({c.get('edition')})")
    line()

    t = p["totals"]
    line(f"nodes {t['nodes']:,}   relationships {t['relationships']:,}")
    if p["isolated_nodes"]:
        line(f"isolated nodes: {p['isolated_nodes']:,}  "
             f"({100.0 * p['isolated_nodes'] / max(t['nodes'], 1):.1f}%)")
    line()

    line("labels")
    for r in p["labels"][:15]:
        if "label" in r:
            line(f"  {r['label']:<24}{r['count']:>12,}")
    line()

    line("relationship types")
    for r in p["relationship_types"][:15]:
        if "type" in r:
            line(f"  {r['type']:<24}{r['count']:>12,}")
    line()

    line("degree distribution — read max and p99, never the mean")
    for r in p["degree"]:
        if "error" not in r:
            line("  " + "  ".join(f"{k} {v:,}" for k, v in r.items()))
    line()

    line("highest-degree nodes — these are where traversals explode")
    for r in p["top_degree_nodes"][:8]:
        if "degree" in r:
            hint = ", ".join(r.get("hint") or []) or "-"
            line(f"  {str(r['labels']):<20}{r['degree']:>10,}   {hint}")
    line()

    w = p.get("wcc") or {}
    line("connected components")
    if not w.get("available"):
        line(f"  {w.get('note', 'not assessed')}")
    else:
        sizes = [r for r in w.get("sizes", []) if "size" in r]
        singles = sum(1 for r in sizes if r["size"] == 1)
        line(f"  {len(sizes)} shown"
             + (f", {singles} of them single nodes" if singles else ""))
        for r in sizes[:6]:
            line(f"    component {r['componentId']:<12}{r['size']:>10,}")
        for d in w.get("detail", []):
            if "size" in d:
                line(f"  component {d['componentId']} ({d['size']:,} nodes) is "
                     f"disconnected from the main graph:")
                for s in d.get("sample", [])[:4]:
                    line(f"    {s}")
    line()

    gaps = [r for r in p["property_completeness"]
            if r["pct_present"] is not None and r["pct_present"] < 99]
    line("property completeness — null, blank and present counted separately")
    if gaps:
        line(f"  {'property':<34}{'present':>9}{'null':>9}{'blank':>9}")
        for r in gaps[:20]:
            line(f"  {r['label'] + '.' + r['property']:<34}"
                 f"{r['pct_present']:>8.1f}%{r['null']:>9,}{r['blank']:>9,}")
        line("  rules built on these will silently misfire")
    else:
        line("  all sampled properties fully present")
    line()

    vs = p.get("value_shape") or []
    if vs:
        line("value shape")
        for r in vs:
            name = f"{r['label']}.{r['property']}"
            note = ""
            if r["distinct"] == r["total"]:
                note = "unique"
            elif r["distinct"] <= 12:
                note = "low cardinality"
            line(f"  {name:<34}{r['distinct']:>9,} distinct  {note}")
            for v in (r.get("values") or [])[:6]:
                line(f"      {str(v['value'])[:40]:<42}{v['n']:>9,}")
            lc = r.get("length_classes") or []
            if len(lc) > 1:
                line(f"      {len(lc)} length classes — "
                     f"more than one format is likely:")
                for L in lc[:5]:
                    line(f"        len {L['len']:<4}{L['n']:>9,}   {L['example']}")
        line()

    cons = [c for c in p["constraints"] if "name" in c]
    idx = [i for i in p["indexes"] if "name" in i]
    line(f"constraints: {len(cons)}   indexes: {len(idx)}")
    if not cons:
        line("  no constraints — node identity is not enforced anywhere")
    else:
        constrained = {tuple(c["labelsOrTypes"] or []) for c in cons}
        for lr in p["labels"]:
            if "label" in lr and (lr["label"],) not in constrained:
                line(f"  :{lr['label']} has no constraint — "
                     f"nothing enforces identity on it")
    line()
    line("An index makes lookups fast and enforces nothing. To test whether a")
    line("property identifies a row, try to create the constraint: drop any")
    line("range index on it first, or the failure will be about the index.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    ap.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    ap.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", "neo4j"))
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    ap.add_argument("--quick", action="store_true",
                    help="skip the value-shape pass (the expensive one)")
    ap.add_argument("--no-components", action="store_true",
                    help="skip WCC — no in-memory projection is created")
    ap.add_argument("--graph-name", default="__profile_tmp",
                    help="name for the temporary GDS projection")
    args = ap.parse_args()

    pw = os.environ.get("NEO4J_PASSWORD")
    if not pw:
        sys.exit("set NEO4J_PASSWORD in the environment")

    driver = GraphDatabase.driver(args.uri, auth=(args.user, pw))
    with driver.session(database=args.database) as s:
        p = profile(s, args)
    driver.close()

    if args.json:
        print(json.dumps(p, indent=2, default=str))
    else:
        report(p)


if __name__ == "__main__":
    main()
