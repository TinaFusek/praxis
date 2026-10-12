# Cypher cookbook

Thirty-five queries, filed under the question they answer rather than under the
clause they use. These are the ones I actually reached for during a one-hour
blind profile of a graph I had not seen.

`<LABEL>`, `<REL>`, `<KEY>`, `<PROP>` are placeholders. Read the real ones out
of §1 before using anything below §1.

Each entry carries what it costs and, where it matters, **what it does not
see** — which is usually the more useful half.

**The drill is at the bottom.** Cover the queries, read the questions, write the
Cypher from memory. Reading this file teaches nothing; producing it under time
pressure at a client's desk is the skill.

---

## 1. Where am I, and what is this?

**1.1 Which database is this session actually using?**

```cypher
CALL db.info() YIELD name RETURN name;
```

Free. Run it first, every time. A UI infers the database; the driver takes it as
a parameter. Four of my probes once measured the wrong database because I
trusted the dropdown.

**1.2 Which version and edition?**

```cypher
CALL dbms.components() YIELD name, versions, edition
RETURN name, versions[0] AS version, edition;
```

Free. Edition decides what you can offer: multi-database and role-based access
control are enterprise-only, and that changes the answer to "can you isolate
tenants".

**1.3 What does the server think its configuration is?**

```cypher
SHOW SETTINGS YIELD name, value
WHERE name IN ['server.directories.import',
               'server.memory.heap.max_size',
               'db.transaction.timeout',
               'dbms.memory.transaction.total.max']
RETURN name, value;
```

Free. **What it does not see:** whether the path exists, or whether the JVM
actually received the flags. A setting is a declaration of intent. Check the
filesystem and `ps -o args=` separately.

**1.4 What labels exist, and how many of each?**

```cypher
MATCH (n) UNWIND labels(n) AS label
RETURN label, count(*) AS count ORDER BY count DESC;
```

Full node scan. On a large graph use the cheap approximation instead:

```cypher
CALL db.labels() YIELD label
CALL { WITH label MATCH (n) WHERE label IN labels(n) RETURN count(*) AS c }
RETURN label, c ORDER BY c DESC;
```

**1.5 What relationship types, and how many?**

```cypher
MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC;
```

**1.6 Totals.**

```cypher
MATCH (n) RETURN count(n) AS nodes;
MATCH ()-[r]->() RETURN count(r) AS relationships;
```

Counted separately on purpose. If they disagree with what the load reported,
something did not land, and a profile of a partial load looks exactly like a
result.

---

## 2. What is enforced, and what only looks enforced?

**2.1 Constraints.**

```cypher
SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties
RETURN name, type, labelsOrTypes, properties;
```

**2.2 Indexes.**

```cypher
SHOW INDEXES YIELD name, type, labelsOrTypes, properties, state, populationPercent
RETURN name, type, labelsOrTypes, properties, state, populationPercent;
```

An index makes lookups fast and **enforces nothing**. Two constraints across
three labels is more dangerous than none, because it looks deliberate. Check
which label was left out.

**2.3 What properties does a label carry?**

```cypher
MATCH (n:`<LABEL>`) RETURN keys(n) AS props LIMIT 1;
```

Sampled from one node, so it is a starting point, not an inventory. For the real
inventory:

```cypher
MATCH (n:`<LABEL>`) WITH n LIMIT 1000
UNWIND keys(n) AS k RETURN DISTINCT k ORDER BY k;
```

**2.4 Does this property identify a row?**

```cypher
DROP INDEX <index_name> IF EXISTS;
CREATE CONSTRAINT tmp_check FOR (n:`<LABEL>`) REQUIRE n.`<PROP>` IS UNIQUE;
```

If duplicates exist the error names one. Then restore:

```cypher
DROP CONSTRAINT tmp_check IF EXISTS;
CREATE INDEX <index_name> IF NOT EXISTS FOR (n:`<LABEL>`) ON (n.`<PROP>`);
```

The `DROP INDEX` first is not optional. A range index on the same property makes
this fail with `22N73 constraint conflicts with existing index` — which looks
identical to a failure and is a different failure. **Read the error code.**

---

## 3. Where will a traversal explode?

**3.1 Degree distribution.**

```cypher
MATCH (n) WITH COUNT { (n)--() } AS deg
RETURN count(*) AS nodes, min(deg) AS min,
       percentileCont(deg, 0.50) AS p50,
       percentileCont(deg, 0.95) AS p95,
       percentileCont(deg, 0.99) AS p99,
       max(deg) AS max;
```

`COUNT { (n)--() }` compiles to `getDegree` — **zero database accesses**, the
count is in node metadata. `percentileCont` is a native aggregation, so nothing
is collected into a list.

Read max and p99. Never the mean. A mean of 5 with a max of 15 000 is not a
graph with a mean of 5.

**3.2 The biggest nodes.**

```cypher
MATCH (n) WITH n, COUNT { (n)--() } AS deg
ORDER BY deg DESC LIMIT 15
RETURN labels(n) AS labels, deg,
       [k IN keys(n) WHERE k IN ['uid','id','cid','aid','pid','name'] |
        k + '=' + toString(n[k])] AS hint;
```

**3.3 Isolated nodes.**

```cypher
MATCH (n) WHERE NOT (n)--() RETURN count(n) AS isolated;
MATCH (n:`<LABEL>`) WHERE NOT (n)--() RETURN n LIMIT 20;
```

**3.4 The frontier — what a traversal will cost from here.**

```cypher
MATCH (n:`<LABEL>` {`<KEY>`: $id})-[:`<REL>`]-(m)
RETURN sum(COUNT { (m)-[:`<REL>`]-() }) AS frontier;
```

~15 ms warm, ~2 s cold. Start-node degree is the obvious signal and it does not
work — 10.6× spread against observed path counts, and it sometimes ranks nodes
backwards, because degree says how many neighbours a node has, not what those
neighbours are.

---

## 4. Is the data there, and what is in it?

**4.1 Null, blank and present — three states, not two.**

```cypher
UNWIND ['<PROP1>','<PROP2>','<PROP3>'] AS k
MATCH (n:`<LABEL>`)
RETURN k AS property, count(*) AS total, count(n[k]) AS not_null,
       sum(CASE WHEN n[k] IS NOT NULL AND trim(toString(n[k])) = ''
                THEN 1 ELSE 0 END) AS blank
ORDER BY not_null;
```

`n[k]` is dynamic property access — one query instead of one per property.

`IS NOT NULL` alone reports an empty string as populated. Which state you get
was decided by whoever wrote the loader: **`LOAD CSV` converts empty fields to
null**, so blanks are rare after a CSV load and common when an application wrote
the rows directly.

**4.2 How many distinct values?**

```cypher
MATCH (n:`<LABEL>`)
RETURN count(*) AS total, count(DISTINCT n.`<PROP>`) AS distinct_values;
```

Equal means unique. Far smaller means a category — or a field coarser than its
name suggests. Ten towns for fifteen hundred companies is not an address.

**4.3 What are the values, when there are few?**

```cypher
MATCH (n:`<LABEL>`) RETURN n.`<PROP>` AS value, count(*) AS n ORDER BY n DESC;
```

Leftover test records live here, in the same label as production data.

**4.4 How long are the strings?**

```cypher
MATCH (n:`<LABEL>`) WHERE n.`<PROP>` IS NOT NULL
RETURN size(n.`<PROP>`) AS len, count(*) AS n, collect(n.`<PROP>`)[0] AS example
ORDER BY n DESC;
```

Separates date and identifier formats without knowing in advance what they are.
**What it does not see:** two formats of the same length. `2011-06-05` and
`01.02.2011` are both ten characters. A single length class is *unverified*, not
proof of consistency.

**4.5 What do they start with?**

```cypher
MATCH (n:`<LABEL>`) WHERE n.`<PROP>` IS NOT NULL
RETURN left(n.`<PROP>`, 2) AS prefix, size(n.`<PROP>`) AS len, count(*) AS n
ORDER BY n DESC;
```

Country codes, system prefixes, and the one row that came from somewhere else.

**4.6 Duplicate values on a property.**

```cypher
MATCH (n:`<LABEL>`) WHERE n.`<PROP>` IS NOT NULL
WITH n.`<PROP>` AS v, collect(n.`<KEY>`) AS ids
WHERE size(ids) > 1
RETURN v, ids ORDER BY size(ids) DESC LIMIT 20;
```

**4.7 Duplicates after normalising.**

```cypher
MATCH (n:`<LABEL>`)
WITH apoc.text.clean(n.`<PROP>`) AS key,
     collect(n.`<KEY>`) AS ids, collect(n.`<PROP>`) AS originals
WHERE size(ids) > 1
RETURN key, ids, originals[0..3] ORDER BY size(ids) DESC LIMIT 20;
```

Without APOC:

```cypher
MATCH (n:`<LABEL>`)
WITH toLower(replace(replace(n.`<PROP>`, ' ', ''), '.', '')) AS key,
     collect(n.`<KEY>`) AS ids
WHERE size(ids) > 1
RETURN key, ids ORDER BY size(ids) DESC LIMIT 20;
```

Neither strips diacritics. `apoc.text.clean` does. **Two different patterns hide
in this output** and they need different answers: several records that genuinely
share a name, and one record entered twice under two spellings. Only looking at
the other properties separates them.

---

## 5. What only shows up after grouping?

**5.1 Counts that must agree.**

```cypher
MATCH (n:`<LABEL>`) RETURN count(n) AS total;
MATCH (n:`<LABEL>`)-[:`<REL>`]->() RETURN count(DISTINCT n) AS with_rel;
```

A difference of one is still a finding.

**5.2 Which ones are missing it?**

```cypher
MATCH (n:`<LABEL>`) WHERE NOT (n)-[:`<REL>`]->()
RETURN n, COUNT { (n)--() } AS degree ORDER BY degree DESC LIMIT 20;
```

Order by degree. The interesting missing record is usually the large one.

**5.3 Missing numeric values on a relationship.**

```cypher
MATCH ()-[r:`<REL>`]->()
RETURN count(r) AS total, count(r.`<PROP>`) AS with_value,
       count(r) - count(r.`<PROP>`) AS missing;
```

`sum()` ignores nulls silently, so every total is low by an unknown amount and
nothing complains. Report `with_value + missing == total` beside every
aggregate — the same rule as `n_scored + n_excluded == set size`.

**5.4 Sums grouped by one endpoint.**

```cypher
MATCH ()-[r:`<REL>`]->(n:`<LABEL>`)
WITH n, sum(r.`<PROP>`) AS total, count(r) AS contributors
RETURN n.`<KEY>`, round(total, 3) AS total, contributors
ORDER BY total DESC LIMIT 10;
```

Then ask whether the total can exceed a domain limit. Shares above 100% mean
either the numbers are wrong or the column holds more than one kind of number —
capital, votes and economic interest are three things routinely stored in one.
You cannot tell which from the data, and that is the point: it becomes a
question for the owner, not a finding.

**5.5 Distribution of a numeric property — on the set you will actually search.**

```cypher
MATCH (s:`<LABEL>`)-[r:`<REL>`]->(d:`<LABEL>`)
WHERE r.`<PROP>` IS NOT NULL
  AND s.`<KEY>` <> '<EXCLUDE>' AND d.`<KEY>` <> '<EXCLUDE>'
RETURN count(r) AS n,
       round(percentileCont(r.`<PROP>`, 0.5)) AS p50,
       round(percentileCont(r.`<PROP>`, 0.9)) AS p90,
       round(max(r.`<PROP>`)) AS max;
```

The exclusion is the point. Percentiles computed over the whole graph describe
a different population from the one you are about to filter.

---

## 6. What shape is the whole thing?

**6.1 Project.**

```cypher
CALL gds.graph.project('tmp', '*', {ALL: {type: '*', orientation: 'UNDIRECTED'}});
```

Orientation matters. WCC on a directed projection answers a different question,
and some algorithms refuse a directed graph outright.

**6.2 Components.**

```cypher
CALL gds.wcc.stream('tmp') YIELD nodeId, componentId
RETURN componentId, count(*) AS size ORDER BY size DESC LIMIT 25;
```

Two seconds on a small graph, and the only thing that sees a subgraph connected
to nothing. No sample reaches it, no spot check reaches it, no traversal from a
real record reaches it.

**6.3 What is inside the smaller components?**

```cypher
CALL gds.wcc.stream('tmp') YIELD nodeId, componentId
WITH componentId, collect(nodeId) AS ids
WITH componentId, ids, size(ids) AS sz
ORDER BY sz DESC SKIP 1 LIMIT 10
WHERE sz > 1
RETURN componentId, sz,
       [n IN [i IN ids[..6] | gds.util.asNode(i)] |
        labels(n)[0] + ' ' + coalesce(n.`<KEY>`, '?') + ' ' +
        coalesce(n.name, '')] AS sample;
```

Collect ids, not nodes. Only the six sampled per component are materialised, so
this stays cheap even when the main component is large.

**6.4 Drop the projection.**

```cypher
CALL gds.graph.drop('tmp', false) YIELD graphName RETURN graphName;
```

`false` means "do not fail if it is not there". Always clean up: a stale
projection is stale data that looks current.

**6.5 Self-loops.**

```cypher
MATCH (a)-[r]->(a) RETURN type(r) AS type, count(*) AS n;
```
```cypher
MATCH (a)-[r]->(a)
RETURN labels(a)[0] AS label, coalesce(a.cid, a.aid, a.pid) AS id,
       type(r) AS type, properties(r) AS props;
```

**6.6 Short cycles.**

```cypher
MATCH p = (a:`<LABEL>`)-[:`<REL>`*2..3]->(a)
RETURN [n IN nodes(p) | n.`<KEY>`] AS cycle,
       [r IN relationships(p) | r.`<PROP>`] AS props LIMIT 10;
```

**A cycle of length n returns n rows** — the same cycle seen from each of its
members. Five rows are not five cycles.

And not every cycle is a defect. A node pointing at itself is almost always an
error; a three-company cross-holding is legal and deliberate. A check that flags
"cycles" produces both and distinguishes nothing; a check that flags "cycles of
length 1" produces only the error.

**6.7 Long cycles — the expensive one.**

```cypher
MATCH p = (a:`<LABEL>`)-[:`<REL>`*5..5]->(a)
WHERE a.`<KEY>` <> '<EXCLUDE>'
  AND all(n IN nodes(p) WHERE n.`<KEY>` <> '<EXCLUDE>')
  AND all(r IN relationships(p) WHERE r.`<PROP>` IS NOT NULL
                                  AND r.`<PROP>` > <THRESHOLD>)
WITH p, [r IN relationships(p) | r.`<PROP>`] AS vals,
        [r IN relationships(p) | r.date] AS dates
WHERE apoc.coll.max(vals) / apoc.coll.min(vals) < 1.15
RETURN [n IN nodes(p) | n.`<KEY>`] AS cycle, vals,
       apoc.coll.min(dates) AS first, apoc.coll.max(dates) AS last
ORDER BY first LIMIT 10;
```

Three things make this affordable, and all three belong **inside** the pattern
rather than after it: exclude the super-node on every node of the path, filter
the property in `all(...)`, and constrain the *ratio* rather than the magnitude.
Money that circulates comes back almost intact; a large payment on its own is
not a signal.

Budget it before running it (§3.4). Five unbounded hops through a graph with a
super-node is the exact shape that does not finish.

---

## 7. Is this query affordable?

**7.1 Plan it without running it.**

```cypher
EXPLAIN MATCH (n:`<LABEL>` {`<KEY>`: $id})-[:`<REL>`*1..4]-(m) RETURN count(m);
```

**7.2 Run it and measure it.**

```cypher
PROFILE MATCH (n:`<LABEL>` {`<KEY>`: $id})-[:`<REL>`*1..4]-(m) RETURN count(m);
```

Read **db hits**, not milliseconds. Time moves with the cache; db hits do not.
See `reading-a-query-plan.md`.

**7.3 Force a decision, to find out what the planner was avoiding.**

```cypher
MATCH (n:`<LABEL>`)
USING INDEX n:`<LABEL>`(`<PROP>`)
WHERE n.`<PROP>` = $value
RETURN n;
```

A hint is a diagnostic, not a fix. If forcing the index makes the query faster,
the planner's estimate was wrong, and *that* is the finding — the hint is what
you remove once you know why.

---

## The drill

Cover everything above. For each question, write the Cypher from memory, then
check. Twenty minutes, three times a week.

```
 1  Which database am I connected to?
 2  Which version and edition?
 3  Where does the server think its import directory is?
 4  Which labels exist and how many of each?
 5  Which relationship types and how many?
 6  Total nodes, total relationships.
 7  Which constraints exist?
 8  Which indexes, and are they online?
 9  Which properties does this label carry?
10  Does this property identify a row?
11  Degree distribution: min, p50, p95, p99, max.
12  The fifteen highest-degree nodes, with something readable to identify them.
13  How many nodes have no relationship at all?
14  What is the frontier from this start node?
15  For every property on this label: null, blank and present, separately.
16  How many distinct values does this property have?
17  List the values, when there are few.
18  Group this string property by length.
19  Group it by its first two characters.
20  Which values of this property appear on more than one node?
21  The same, after normalising the string.
22  Count of this label, versus count of those with this relationship.
23  Which ones are missing it, largest first?
24  How many relationships of this type have no value for this property?
25  Sum a relationship property grouped by its target.
26  Percentiles of a relationship property, excluding one node.
27  Project the whole graph, undirected.
28  Component sizes.
29  What is inside the components that are neither the main one nor singletons?
30  Drop the projection.
31  Are there self-loops, and on which relationship type?
32  Cycles of length 2 to 3.
33  A five-hop cycle, with the super-node excluded and a ratio constraint.
34  Plan a query without running it.
35  Run it and read its db hits.
```
