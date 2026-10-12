#!/usr/bin/env python3
"""
Run a set of data-quality checks against a Neo4j graph and write an HTML report.

The point of this script is that it does not know your schema. It discovers the
labels, relationship types and properties first, then generates the checks from
what it found. A check written against label names works on one database and
silently finds nothing on every other one — which looks identical to a clean
result.

Fourteen generic checks run on any graph. Four more need a rules file, because
no amount of schema discovery reveals that two relationship types contradict
each other, or that a percentage column must not exceed 100.

    export NEO4J_PASSWORD=...          # never a password in argv
    python discrepancy_report.py --database neo4j --out report.html
    python discrepancy_report.py --database neo4j --rules rules.json --out report.html

Every check is read-only. With components enabled (the default) the script
creates one named in-memory GDS projection and drops it before returning;
--no-components skips that.

Each finding carries a severity and, more usefully, the query that produced it.
A finding you cannot reproduce is not a finding, and a client will ask.
"""

import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone

from neo4j import GraphDatabase

CRITICAL, WARNING, INFO = "critical", "warning", "info"

# Properties below this are reported as incomplete.
COMPLETE_THRESHOLD = 99.0
# A property is treated as identifier-like above this distinct ratio.
IDENTIFIER_RATIO = 0.90
# A degree this many times the 99th percentile is a super-node.
SUPERNODE_FACTOR = 10
# At or below this many distinct values, the values themselves are listed.
LOW_CARDINALITY = 12


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #

class Graph:
    def __init__(self, session):
        self.s = session
        self.errors = []

    def rows(self, query, **params):
        try:
            return self.s.run(query, params).data()
        except Exception as e:
            self.errors.append({"query": query.strip(), "error": f"{type(e).__name__}: {e}"})
            return None

    def one(self, query, key, default=None, **params):
        r = self.rows(query, **params)
        if not r:
            return default
        return r[0].get(key, default)


class Report:
    def __init__(self):
        self.findings = []
        self.context = {}
        self.not_assessed = []

    def add(self, severity, check, title, detail, query, rows=None, columns=None):
        self.findings.append({
            "severity": severity, "check": check, "title": title,
            "detail": detail, "query": query.strip(),
            "rows": rows or [], "columns": columns or [],
        })

    def skip(self, reason):
        self.not_assessed.append(reason)

    def count(self, severity):
        return sum(1 for f in self.findings if f["severity"] == severity)


# --------------------------------------------------------------------------- #
# schema discovery — everything below is generated from this
# --------------------------------------------------------------------------- #

def discover(g):
    schema = {"labels": [], "rel_types": [], "properties": {}, "rel_properties": {}}

    labels = g.rows("""
        MATCH (n) UNWIND labels(n) AS label
        RETURN label, count(*) AS count ORDER BY count DESC
    """) or []
    schema["labels"] = [r for r in labels if r.get("label")]

    rels = g.rows("""
        MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC
    """) or []
    schema["rel_types"] = [r for r in rels if r.get("type")]

    # Sampled: keys() on every node of a large label is not worth the scan.
    for row in schema["labels"]:
        label = row["label"]
        keys = g.rows(f"""
            MATCH (n:`{label}`) WITH n LIMIT 1000
            UNWIND keys(n) AS k RETURN DISTINCT k ORDER BY k
        """) or []
        schema["properties"][label] = [k["k"] for k in keys if k.get("k")]

    for row in schema["rel_types"]:
        rtype = row["type"]
        keys = g.rows(f"""
            MATCH ()-[r:`{rtype}`]->() WITH r LIMIT 1000
            UNWIND keys(r) AS k RETURN DISTINCT k ORDER BY k
        """) or []
        schema["rel_properties"][rtype] = [k["k"] for k in keys if k.get("k")]

    return schema


# --------------------------------------------------------------------------- #
# generic checks
# --------------------------------------------------------------------------- #

def check_constraints(g, schema, rep):
    cons = g.rows("""
        SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties
        RETURN name, type, labelsOrTypes, properties
    """)
    if cons is None:
        rep.skip("Constraints could not be listed — insufficient privileges?")
        return
    constrained = set()
    for c in cons:
        for l in (c.get("labelsOrTypes") or []):
            constrained.add(l)

    unprotected = [r for r in schema["labels"] if r["label"] not in constrained]
    if not unprotected:
        return

    sev = CRITICAL if cons else WARNING
    rep.add(
        sev, "constraints",
        f"{len(unprotected)} label(s) have no constraint",
        "Nothing enforces node identity on these labels, so duplicates are "
        "possible and every count over them may already be wrong. Note that a "
        "constraint on a technical key protects nothing if the duplicates carry "
        "different technical keys — the constraint has to sit on the business "
        "identity." + ("" if cons else " There are no constraints at all in this database."),
        "SHOW CONSTRAINTS;",
        [{"label": r["label"], "nodes": r["count"]} for r in unprotected],
        ["label", "nodes"],
    )


def check_completeness(g, schema, rep):
    """Null, blank and present counted separately.

    Which of the three you get was decided by whoever wrote the loader. LOAD CSV
    turns empty fields into null; an application writing directly usually leaves
    an empty string, which IS NOT NULL happily reports as populated.
    """
    incomplete, blanks = [], []
    for row in schema["labels"]:
        label, total = row["label"], row["count"]
        props = schema["properties"].get(label) or []
        if not props or not total:
            continue
        res = g.rows(f"""
            UNWIND $keys AS k
            MATCH (n:`{label}`)
            RETURN k AS property, count(*) AS total, count(n[k]) AS not_null,
                   sum(CASE WHEN n[k] IS NOT NULL AND trim(toString(n[k])) = ''
                            THEN 1 ELSE 0 END) AS blank
        """, keys=props)
        for r in (res or []):
            present = r["not_null"] - r["blank"]
            pct = round(100.0 * present / r["total"], 1) if r["total"] else 0.0
            entry = {"property": f"{label}.{r['property']}", "present": f"{pct}%",
                     "null": r["total"] - r["not_null"], "blank": r["blank"],
                     "of": r["total"]}
            if pct < COMPLETE_THRESHOLD:
                incomplete.append(entry)
            if r["blank"]:
                blanks.append(entry)

    if incomplete:
        incomplete.sort(key=lambda e: float(e["present"].rstrip("%")))
        rep.add(
            WARNING, "completeness",
            f"{len(incomplete)} propert(ies) below {COMPLETE_THRESHOLD}% populated",
            "Every rule built on an incomplete property silently excludes the "
            "rows that lack it, returns a smaller number, and does not error. "
            "Say this to the data owner before presenting any analysis, not "
            "after the result is questioned.",
            "MATCH (n:`<LABEL>`)\n"
            "RETURN count(*) AS total, count(n.`<PROP>`) AS not_null,\n"
            "       sum(CASE WHEN n.`<PROP>` IS NOT NULL\n"
            "                 AND trim(toString(n.`<PROP>`)) = '' THEN 1 ELSE 0 END) AS blank;",
            incomplete, ["property", "present", "null", "blank", "of"],
        )
    if blanks:
        rep.add(
            WARNING, "blank_strings",
            f"{len(blanks)} propert(ies) contain empty strings",
            "An empty string is not null. A completeness check written as "
            "IS NOT NULL reports these as populated. Both states exist here, so "
            "any figure that does not say which one it counted is not a number.",
            "MATCH (n:`<LABEL>`) WHERE trim(toString(n.`<PROP>`)) = '' RETURN count(*);",
            blanks, ["property", "present", "null", "blank", "of"],
        )


def check_identifier_duplicates(g, schema, rep):
    """A property whose values are nearly, but not quite, unique is a business
    key with duplicates in it. That is worth more than any similarity score."""
    for row in schema["labels"]:
        label, total = row["label"], row["count"]
        if total < 10:
            continue
        for prop in schema["properties"].get(label) or []:
            stats = g.rows(f"""
                MATCH (n:`{label}`)
                RETURN count(n.`{prop}`) AS non_null,
                       count(DISTINCT n.`{prop}`) AS distinct_values
            """)
            if not stats:
                continue
            non_null = stats[0]["non_null"]
            distinct = stats[0]["distinct_values"]
            if non_null < 10 or distinct == non_null:
                continue
            if distinct / non_null < IDENTIFIER_RATIO:
                continue                      # a category, not an identifier
            dups = g.rows(f"""
                MATCH (n:`{label}`) WHERE n.`{prop}` IS NOT NULL
                WITH n.`{prop}` AS value, count(*) AS n
                WHERE n > 1
                RETURN value, n ORDER BY n DESC, value LIMIT 25
            """) or []
            rep.add(
                CRITICAL, "duplicate_identifier",
                f"{label}.{prop} looks like an identifier and has duplicates",
                f"{distinct:,} distinct values across {non_null:,} populated rows "
                f"({round(100.0*distinct/non_null,1)}% unique). Two records, one "
                "real-world entity: every count is inflated and every aggregate "
                "keyed on this property double-counts.\n\n"
                "Confirm it in two seconds by trying to enforce it — drop any "
                "range index on the property first, or the failure will be about "
                "the index and not about the data.",
                f"MATCH (n:`{label}`) WHERE n.`{prop}` IS NOT NULL\n"
                f"WITH n.`{prop}` AS value, count(*) AS n WHERE n > 1\n"
                f"RETURN value, n ORDER BY n DESC;",
                dups, ["value", "n"],
            )


def check_orphans(g, schema, rep):
    orphans = []
    for row in schema["labels"]:
        label = row["label"]
        c = g.one(f"MATCH (n:`{label}`) WHERE NOT (n)--() RETURN count(n) AS c", "c", 0)
        if c:
            orphans.append({"label": label, "orphans": c, "of": row["count"],
                            "pct": f"{round(100.0*c/row['count'],1)}%"})
    if orphans:
        rep.add(
            WARNING, "orphans",
            f"{sum(o['orphans'] for o in orphans):,} node(s) have no relationship at all",
            "An isolated node is invisible to every traversal. Check whether the "
            "missing property values correlate with the missing relationships — a "
            "perfect correlation usually means two sources were merged and one "
            "carried neither.",
            "MATCH (n:`<LABEL>`) WHERE NOT (n)--() RETURN count(n);",
            orphans, ["label", "orphans", "of", "pct"],
        )


def check_degree(g, rep):
    d = g.rows("""
        MATCH (n) WITH COUNT { (n)--() } AS deg
        RETURN count(*) AS nodes, min(deg) AS min,
               percentileCont(deg, 0.50) AS p50,
               percentileCont(deg, 0.95) AS p95,
               percentileCont(deg, 0.99) AS p99,
               max(deg) AS max, round(avg(deg), 2) AS mean
    """)
    if not d:
        return
    d = d[0]
    p99, mx = d["p99"] or 0, d["max"] or 0
    if p99 and mx > SUPERNODE_FACTOR * p99:
        top = g.rows("""
            MATCH (n) WITH n, COUNT { (n)--() } AS deg
            ORDER BY deg DESC LIMIT 10
            RETURN labels(n)[0] AS label, deg AS degree,
                   coalesce(n.name, n.uid, n.id, n.cid, n.aid, n.pid, '?') AS node
        """) or []
        rep.add(
            CRITICAL, "supernode",
            f"Super-node: max degree {mx:,.0f} against a 99th percentile of {p99:,.0f}",
            f"One node carries {round(mx/p99)}x what the 99th percentile node "
            "carries. Two separate consequences, and they are not the same.\n\n"
            "Traversal: any unbounded path query reaching this node expands into "
            f"{mx:,.0f} branches at that step.\n\n"
            "Meaning: connecting entities through a shared hub is not a "
            "relationship between them. Left in, almost everything connects to "
            "almost everything and the answer is true and worthless. What the "
            "node represents is a question for the data owner — and it should be "
            "asked before anything is ranked, because one super-node reorders "
            "every centrality score in the graph.",
            "MATCH (n) WITH n, COUNT { (n)--() } AS deg\n"
            "ORDER BY deg DESC LIMIT 10\n"
            "RETURN labels(n), deg;",
            top, ["label", "node", "degree"],
        )
    else:
        rep.context["degree"] = d


def check_self_loops(g, rep):
    loops = g.rows("""
        MATCH (a)-[r]->(a)
        RETURN type(r) AS type, labels(a)[0] AS label,
               coalesce(a.name, a.uid, a.id, a.cid, a.aid, a.pid, '?') AS node
        LIMIT 25
    """)
    if loops:
        rep.add(
            WARNING, "self_loops",
            f"{len(loops)} self-loop(s): a node related to itself",
            "Usually an error, and it makes any recursive query non-terminating "
            "unless the traversal tracks visited nodes.\n\n"
            "Keep this check separate from any cycle check. A cycle of length 1 "
            "is almost always a defect; a longer cycle is often legitimate — "
            "cross-ownership between companies is legal and deliberate. A check "
            "that flags 'cycles' produces both and distinguishes nothing.",
            "MATCH (a)-[r]->(a) RETURN type(r), labels(a), count(*);",
            loops, ["label", "node", "type"],
        )


def check_relationship_property_gaps(g, schema, rep):
    gaps = []
    for row in schema["rel_types"]:
        rtype, total = row["type"], row["count"]
        for prop in schema["rel_properties"].get(rtype) or []:
            r = g.rows(f"""
                MATCH ()-[r:`{rtype}`]->()
                RETURN count(r) AS total, count(r.`{prop}`) AS with_value
            """)
            if not r:
                continue
            missing = r[0]["total"] - r[0]["with_value"]
            if missing:
                gaps.append({"property": f"{rtype}.{prop}",
                             "with_value": r[0]["with_value"],
                             "missing": missing, "of": total})
    if gaps:
        rep.add(
            WARNING, "missing_rel_values",
            f"{len(gaps)} relationship propert(ies) have missing values",
            "sum() ignores nulls silently, so every total over these is low by "
            "an unknown amount and nothing complains. Report "
            "with_value + missing == total beside every aggregate, and check "
            "that it adds up.",
            "MATCH ()-[r:`<REL>`]->()\n"
            "RETURN count(r) AS total, count(r.`<PROP>`) AS with_value,\n"
            "       count(r) - count(r.`<PROP>`) AS missing;",
            gaps, ["property", "with_value", "missing", "of"],
        )


def check_format_classes(g, schema, rep):
    """Group a string property by length. Coarse — two formats of the same
    length stay merged — so one class is unverified, not proof."""
    mixed = []
    for row in schema["labels"]:
        label = row["label"]
        for prop in schema["properties"].get(label) or []:
            lens = g.rows(f"""
                MATCH (n:`{label}`)
                WHERE n.`{prop}` IS NOT NULL AND toString(n.`{prop}`) = n.`{prop}`
                RETURN size(n.`{prop}`) AS len, count(*) AS n,
                       collect(n.`{prop}`)[0] AS example
                ORDER BY n DESC LIMIT 6
            """)
            if lens and len(lens) > 1:
                for L in lens:
                    mixed.append({"property": f"{label}.{prop}", "length": L["len"],
                                  "rows": L["n"], "example": L["example"]})
    if mixed:
        props = len({m["property"] for m in mixed})
        rep.add(
            WARNING, "format_classes",
            f"{props} string propert(ies) hold more than one format",
            "Values of different lengths in one column usually mean more than "
            "one written format — dates especially. Stored as strings they sort "
            "as text, so any ORDER BY or range filter over them is wrong and "
            "nothing errors.\n\n"
            "Grouping by length is coarse: two formats of the same length stay "
            "merged, so a single length class is unverified rather than clean.",
            "MATCH (n:`<LABEL>`) WHERE n.`<PROP>` IS NOT NULL\n"
            "RETURN size(n.`<PROP>`) AS len, count(*) AS n,\n"
            "       collect(n.`<PROP>`)[0] AS example ORDER BY n DESC;",
            mixed, ["property", "length", "rows", "example"],
        )


def check_low_cardinality(g, schema, rep):
    """Leftover test records live in a status-like property, in the same label
    as production data."""
    out = []
    for row in schema["labels"]:
        label, total = row["label"], row["count"]
        if total < 50:
            continue
        for prop in schema["properties"].get(label) or []:
            d = g.one(f"MATCH (n:`{label}`) RETURN count(DISTINCT n.`{prop}`) AS c", "c")
            if not isinstance(d, int) or d < 2 or d > LOW_CARDINALITY:
                continue
            vals = g.rows(f"""
                MATCH (n:`{label}`) WHERE n.`{prop}` IS NOT NULL
                RETURN toString(n.`{prop}`) AS value, count(*) AS n
                ORDER BY n DESC LIMIT 15
            """) or []
            rare = [v for v in vals if v["n"] <= max(5, total * 0.005)]
            if rare:
                for v in vals:
                    out.append({"property": f"{label}.{prop}", "value": v["value"],
                                "rows": v["n"], "of": total})
    if out:
        rep.add(
            INFO, "low_cardinality",
            "Rare values in low-cardinality properties",
            "A handful of rows carrying a value that almost nothing else "
            "carries. Leftover test records live here, in the same label as "
            "production data. Small in itself; the reason to report it is that "
            "it shows test and production are not separated, so nothing stops "
            "the next test row from being large and not harmless.",
            "MATCH (n:`<LABEL>`) RETURN n.`<PROP>` AS value, count(*) AS n ORDER BY n DESC;",
            out, ["property", "value", "rows", "of"],
        )


def check_components(g, rep, graph_name):
    if g.rows("CALL gds.version() YIELD version RETURN version") is None:
        rep.skip("Connected components not assessed — the GDS plugin is not "
                 "installed. This is a gap in the report, not an absence of "
                 "finding: a disconnected subgraph is invisible to every other "
                 "check here.")
        return

    g.rows("CALL gds.graph.drop($g, false) YIELD graphName RETURN graphName", g=graph_name)
    proj = g.rows("""
        CALL gds.graph.project($g, '*', {ALL: {type: '*', orientation: 'UNDIRECTED'}})
        YIELD graphName, nodeCount, relationshipCount
        RETURN graphName, nodeCount, relationshipCount
    """, g=graph_name)
    if not proj:
        rep.skip("Connected components not assessed — the projection failed.")
        return

    try:
        sizes = g.rows("""
            CALL gds.wcc.stream($g) YIELD nodeId, componentId
            RETURN componentId, count(*) AS size ORDER BY size DESC LIMIT 25
        """, g=graph_name) or []
        detail = g.rows("""
            CALL gds.wcc.stream($g) YIELD nodeId, componentId
            WITH componentId, collect(nodeId) AS ids
            WITH componentId, ids, size(ids) AS sz
            ORDER BY sz DESC SKIP 1 LIMIT 10
            WHERE sz > 1
            RETURN componentId, sz AS size,
                   [n IN [i IN ids[..6] | gds.util.asNode(i)] |
                    labels(n)[0] + ' ' +
                    coalesce(n.name, n.cid, n.aid, n.pid, n.uid, n.id, '?')] AS sample
        """, g=graph_name) or []
    finally:
        g.rows("CALL gds.graph.drop($g, false) YIELD graphName RETURN graphName", g=graph_name)

    if len(sizes) <= 1:
        return

    islands = [d for d in detail if d.get("size", 0) > 1]
    if islands:
        rep.add(
            CRITICAL, "components",
            f"{len(islands)} disconnected subgraph(s) of more than one node",
            "No query starting from a node in the main component ever reaches "
            "these. They are invisible to sampling, to spot checks and to every "
            "traversal — and a group that transacts only with itself is usually "
            "the part that matters most.\n\n"
            "Only a whole-graph algorithm finds this. Nothing else in this "
            "report would have.",
            "CALL gds.graph.project('tmp','*',{ALL:{type:'*',orientation:'UNDIRECTED'}});\n"
            "CALL gds.wcc.stream('tmp') YIELD nodeId, componentId\n"
            "RETURN componentId, count(*) AS size ORDER BY size DESC;",
            [{"component": d["componentId"], "nodes": d["size"],
              "sample": ", ".join(d.get("sample") or [])} for d in islands],
            ["component", "nodes", "sample"],
        )

    singles = sum(1 for s in sizes if s["size"] == 1)
    if singles:
        rep.context["singleton_components"] = singles


# --------------------------------------------------------------------------- #
# rules-driven checks — these cannot be discovered
# --------------------------------------------------------------------------- #

def check_conflicting_relationships(g, rules, rep):
    for pair in rules.get("conflicting_relationships") or []:
        a, b = pair[0], pair[1]
        rows = g.rows(f"""
            MATCH (x)-[:`{a}`]->(y)
            MATCH (x)-[:`{b}`]->(y)
            RETURN coalesce(x.name, x.cid, x.aid, x.pid, x.uid, x.id, '?') AS `from`,
                   coalesce(y.name, y.cid, y.aid, y.pid, y.uid, y.id, '?') AS `to`
            LIMIT 50
        """)
        if rows:
            rep.add(
                CRITICAL, "conflicting_relationships",
                f"{len(rows)} pair(s) are both {a} and {b}",
                "The same two nodes carry two relationships that cannot both be "
                "true. One of the two sources is wrong, or they mean different "
                "things and the model has merged them.",
                f"MATCH (x)-[:`{a}`]->(y)\nMATCH (x)-[:`{b}`]->(y)\nRETURN x, y;",
                rows, ["from", "to"],
            )


def check_aggregate_limits(g, rules, rep):
    for rule in rules.get("aggregate_limits") or []:
        rtype, prop = rule["relationship"], rule["property"]
        limit = rule.get("max")
        label = rule.get("label", "")
        lbl = f":`{label}`" if label else ""
        rows = g.rows(f"""
            MATCH ()-[r:`{rtype}`]->(n{lbl})
            WITH n, sum(r.`{prop}`) AS total, count(r) AS contributors
            WHERE total > $limit
            RETURN coalesce(n.name, n.cid, n.aid, n.pid, n.uid, n.id, '?') AS node,
                   round(total, 4) AS total, contributors
            ORDER BY total DESC LIMIT 50
        """, limit=limit)
        if rows:
            rep.add(
                CRITICAL, "aggregate_limit",
                rule.get("name", f"{rtype}.{prop} exceeds {limit} on {len(rows)} node(s)"),
                f"Incoming {prop} sums above {limit}. Either the values are wrong, "
                "or they are not shares of the same thing — voting rights, capital "
                "and economic interest are three different numbers and are "
                "routinely stored in one column. This cannot be decided from the "
                "data; it is a question for the owner.\n\n"
                "Note what was needed to see it: invisible in any single row, in "
                "any sample, and in any node-level profile.",
                f"MATCH ()-[r:`{rtype}`]->(n{lbl})\n"
                f"WITH n, sum(r.`{prop}`) AS total WHERE total > {limit}\n"
                f"RETURN n, total ORDER BY total DESC;",
                rows, ["node", "total", "contributors"],
            )


def check_required_properties(g, rules, rep):
    for label, props in (rules.get("required_properties") or {}).items():
        for prop in props:
            c = g.one(f"""
                MATCH (n:`{label}`)
                WHERE n.`{prop}` IS NULL OR trim(toString(n.`{prop}`)) = ''
                RETURN count(n) AS c
            """, "c", 0)
            if c:
                rep.add(
                    CRITICAL, "required_property",
                    f"{c:,} :{label} node(s) are missing the required {prop}",
                    "Declared as required in the rules file. Every rule keyed on "
                    "this property silently excludes these rows.",
                    f"MATCH (n:`{label}`) WHERE n.`{prop}` IS NULL RETURN count(n);",
                )


def check_cycles(g, rules, rep):
    for rule in rules.get("cycles") or []:
        rtype = rule["relationship"]
        lo, hi = rule.get("min_length", 2), rule.get("max_length", 3)
        rows = g.rows(f"""
            MATCH p = (a)-[:`{rtype}`*{lo}..{hi}]->(a)
            RETURN [n IN nodes(p) |
                    coalesce(n.name, n.cid, n.aid, n.pid, n.uid, n.id, '?')] AS cycle
            LIMIT 30
        """)
        if rows:
            rep.add(
                WARNING, "cycles",
                f"{len(rows)} cycle(s) of length {lo}–{hi} in {rtype}",
                "A cycle of length n returns n rows — the same cycle seen from "
                "each of its members. These are not n separate cycles.\n\n"
                "And a cycle is not automatically a defect. Cross-holdings "
                "between companies are legal and deliberate. What a legitimate "
                "cycle breaks is the method, not the data: a value that "
                "propagates around it cannot be computed by traversal, because "
                "the traversal does not terminate. It converges, and obtaining "
                "it needs a matrix solve.\n\n"
                "Report these under questions for the owner, not under defects.",
                f"MATCH p = (a)-[:`{rtype}`*{lo}..{hi}]->(a) RETURN nodes(p);",
                [{"cycle": " -> ".join(str(x) for x in r["cycle"])} for r in rows],
                ["cycle"],
            )


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #

CSS = """
:root{--bg:#FAF7F2;--card:#FFFFFF;--ink:#221A1C;--ink2:#6B5B55;--rule:#E3DAD0;
--crit:#8C2F3D;--crit-bg:#F7E5E7;--warn:#8E6420;--warn-bg:#F6EBD8;
--info:#38566B;--info-bg:#E4ECF1;--accent:#5E2233;}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#16100F;--card:#211918;
--ink:#F0E7DF;--ink2:#A89890;--rule:#382B29;--crit:#E5899A;--crit-bg:#2E181C;
--warn:#DCAE63;--warn-bg:#2B2116;--info:#8FB6CE;--info-bg:#1A242B;--accent:#E29FAE;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,
BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:32px 20px 60px}
h1{font-size:1.9rem;margin:0 0 6px;letter-spacing:-.02em}
.sub{color:var(--ink2);margin:0 0 4px}
.meta{font:12px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink2);margin-top:14px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:22px 0 30px}
.tile{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:12px 14px}
.tile .n{font-size:1.5rem;font-weight:700;font-variant-numeric:tabular-nums}
.tile .l{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--ink2)}
.tile.critical .n{color:var(--crit)} .tile.warning .n{color:var(--warn)} .tile.info .n{color:var(--info)}
.f{background:var(--card);border:1px solid var(--rule);border-left-width:4px;
border-radius:8px;margin-bottom:16px;overflow:hidden}
.f.critical{border-left-color:var(--crit)} .f.warning{border-left-color:var(--warn)}
.f.info{border-left-color:var(--info)}
.fh{padding:14px 16px 10px;display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap}
.fh h2{font-size:1.02rem;margin:0;flex:1;min-width:220px;letter-spacing:-.01em}
.sev{font:700 10px/1.6 ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;
padding:2px 8px;border-radius:3px;white-space:nowrap}
.sev.critical{color:var(--crit);background:var(--crit-bg)}
.sev.warning{color:var(--warn);background:var(--warn-bg)}
.sev.info{color:var(--info);background:var(--info-bg)}
.fb{padding:0 16px 14px}
.fb p{margin:0 0 10px;color:var(--ink2)}
.fb p:last-child{margin-bottom:0}
details{margin-top:10px;border-top:1px solid var(--rule);padding-top:10px}
summary{cursor:pointer;font-size:12px;color:var(--ink2);letter-spacing:.03em}
pre{font:12px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);
border:1px solid var(--rule);border-radius:6px;padding:11px 13px;overflow-x:auto;margin:10px 0 0}
.scroll{overflow-x:auto;margin-top:10px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{text-align:left;font:600 10px/1.8 ui-monospace,monospace;letter-spacing:.09em;
text-transform:uppercase;color:var(--ink2);border-bottom:1px solid var(--rule);padding:0 10px 5px}
td{padding:6px 10px;border-bottom:1px solid var(--rule);font-variant-numeric:tabular-nums;
vertical-align:top;max-width:380px;overflow-wrap:anywhere}
tr:last-child td{border-bottom:none}
.note{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:16px;margin-top:26px}
.note h2{font-size:1.02rem;margin:0 0 10px}
.note ul{margin:0;padding-left:20px;color:var(--ink2)}
.note li{margin-bottom:7px}
.clean{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:26px;text-align:center;color:var(--ink2)}
footer{margin-top:30px;padding-top:16px;border-top:1px solid var(--rule);
font:11px/1.8 ui-monospace,monospace;color:var(--ink2)}
"""


def render(rep, schema, args):
    e = html.escape

    def table(cols, rows):
        if not rows or not cols:
            return ""
        head = "".join(f"<th>{e(str(c))}</th>" for c in cols)
        body = ""
        for r in rows[:60]:
            body += "<tr>" + "".join(
                f"<td>{e(str(r.get(c, '')))}</td>" for c in cols) + "</tr>"
        more = (f"<p style='font-size:12px;margin-top:8px'>"
                f"{len(rows) - 60:,} further row(s) not shown.</p>") if len(rows) > 60 else ""
        return (f"<div class='scroll'><table><thead><tr>{head}</tr></thead>"
                f"<tbody>{body}</tbody></table></div>{more}")

    order = {CRITICAL: 0, WARNING: 1, INFO: 2}
    findings = sorted(rep.findings, key=lambda f: order[f["severity"]])

    blocks = ""
    for f in findings:
        detail = "".join(f"<p>{e(p)}</p>" for p in f["detail"].split("\n\n"))
        blocks += f"""
<article class="f {f['severity']}">
  <div class="fh">
    <h2>{e(f['title'])}</h2>
    <span class="sev {f['severity']}">{f['severity']}</span>
  </div>
  <div class="fb">
    {detail}
    {table(f['columns'], f['rows'])}
    <details><summary>Query that produced this</summary><pre>{e(f['query'])}</pre></details>
  </div>
</article>"""

    if not findings:
        blocks = ("<div class='clean'>No findings at the configured thresholds. "
                  "That is not the same as a clean graph — read the "
                  "&ldquo;not assessed&rdquo; section below.</div>")

    gaps = list(rep.not_assessed)
    gaps.append("Whether any duplicate pair is genuinely the same entity. Only a "
                "lookup against an authoritative source answers that.")
    gaps.append("Semantics. This report finds structural and completeness "
                "defects; it cannot tell you that a correctly-formed value is the "
                "wrong value.")
    gaps.append("Anything requiring domain knowledge that was not supplied in the "
                "rules file — contradictory relationship types, aggregate limits, "
                "required properties.")
    gaps.append("Load behaviour, concurrency, growth and production data.")
    if not args.rules:
        gaps.insert(0, "No rules file was supplied, so the four domain checks "
                       "(conflicting relationships, aggregate limits, required "
                       "properties, cycles) did not run.")

    errs = ""
    if rep.context.get("errors"):
        rows = rep.context["errors"]
        errs = f"""<div class="note"><h2>Checks that failed to run</h2>
        {table(['query','error'], rows)}
        <p style="font-size:12px;margin-top:10px">A check that errored is counted
        here rather than dropped. A report whose denominator quietly shrank is
        worse than one with a gap in it.</p></div>"""

    labels = ", ".join(f"{r['label']} {r['count']:,}" for r in schema["labels"][:6])
    rels = ", ".join(f"{r['type']} {r['count']:,}" for r in schema["rel_types"][:6])

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Discrepancy report — {e(rep.context.get('database','graph'))}</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>Discrepancy report</h1>
<p class="sub">Generic data-quality checks, generated from the schema this
database actually has.</p>
<p class="meta">
database {e(str(rep.context.get('database','?')))} ·
{e(str(rep.context.get('version','?')))}<br>
{rep.context.get('nodes',0):,} nodes · {rep.context.get('relationships',0):,} relationships<br>
{e(labels)}<br>{e(rels)}<br>
generated {e(rep.context.get('generated',''))}
</p>

<div class="tiles">
  <div class="tile critical"><div class="n">{rep.count(CRITICAL)}</div><div class="l">Critical</div></div>
  <div class="tile warning"><div class="n">{rep.count(WARNING)}</div><div class="l">Warning</div></div>
  <div class="tile info"><div class="n">{rep.count(INFO)}</div><div class="l">Info</div></div>
  <div class="tile"><div class="n">{len(schema['labels'])}</div><div class="l">Labels</div></div>
  <div class="tile"><div class="n">{len(schema['rel_types'])}</div><div class="l">Rel types</div></div>
</div>

{blocks}

<div class="note">
  <h2>Not assessed</h2>
  <ul>{''.join(f'<li>{e(g)}</li>' for g in gaps)}</ul>
  <p style="font-size:12px;margin-top:12px">A report without this section implies
  coverage it does not have.</p>
</div>

{errs}

<footer>
Generated by discrepancy_report.py · read-only ·
credentials taken from the environment, never from the command line<br>
Severity is a default, not a judgement about this data. Whether a finding is a
defect is a question for whoever owns it.
</footer>
</div></body></html>"""


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    ap.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    ap.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", "neo4j"))
    ap.add_argument("--rules", help="JSON file of domain rules — see --example-rules")
    ap.add_argument("--out", default="discrepancy-report.html")
    ap.add_argument("--no-components", action="store_true",
                    help="skip WCC — no in-memory projection is created")
    ap.add_argument("--graph-name", default="__discrepancy_tmp")
    ap.add_argument("--example-rules", action="store_true",
                    help="print an example rules file and exit")
    args = ap.parse_args()

    if args.example_rules:
        print(json.dumps({
            "conflicting_relationships": [["SAFE_WITH", "CONTRAINDICATED_WITH"]],
            "required_properties": {"Company": ["cid", "name"]},
            "aggregate_limits": [{
                "name": "Incoming ownership above 100%",
                "relationship": "OWNS", "property": "pct",
                "label": "Company", "max": 1.0
            }],
            "cycles": [{"relationship": "OWNS", "min_length": 2, "max_length": 3}]
        }, indent=2))
        return

    pw = os.environ.get("NEO4J_PASSWORD")
    if not pw:
        sys.exit("set NEO4J_PASSWORD in the environment")

    rules = {}
    if args.rules:
        with open(args.rules, encoding="utf-8") as fh:
            rules = json.load(fh)

    rep = Report()
    driver = GraphDatabase.driver(args.uri, auth=(args.user, pw))
    with driver.session(database=args.database) as session:
        g = Graph(session)

        rep.context["database"] = g.one("CALL db.info() YIELD name RETURN name", "name", "?")
        comp = g.rows("""CALL dbms.components() YIELD name, versions, edition
                         RETURN name + ' ' + versions[0] + ' (' + edition + ')' AS v""")
        rep.context["version"] = comp[0]["v"] if comp else "?"
        rep.context["nodes"] = g.one("MATCH (n) RETURN count(n) AS c", "c", 0)
        rep.context["relationships"] = g.one("MATCH ()-[r]->() RETURN count(r) AS c", "c", 0)
        rep.context["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        schema = discover(g)

        check_constraints(g, schema, rep)
        check_completeness(g, schema, rep)
        check_identifier_duplicates(g, schema, rep)
        check_orphans(g, schema, rep)
        check_degree(g, rep)
        check_self_loops(g, rep)
        check_relationship_property_gaps(g, schema, rep)
        check_format_classes(g, schema, rep)
        check_low_cardinality(g, schema, rep)
        if args.no_components:
            rep.skip("Connected components skipped (--no-components).")
        else:
            check_components(g, rep, args.graph_name)

        check_conflicting_relationships(g, rules, rep)
        check_aggregate_limits(g, rules, rep)
        check_required_properties(g, rules, rep)
        check_cycles(g, rules, rep)

        rep.context["errors"] = g.errors

    driver.close()

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render(rep, schema, args))

    print(f"{rep.count(CRITICAL)} critical · {rep.count(WARNING)} warning · "
          f"{rep.count(INFO)} info")
    if rep.context["errors"]:
        print(f"{len(rep.context['errors'])} check(s) errored — listed in the report")
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
