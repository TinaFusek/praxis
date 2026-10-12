# Reading a query plan

A slow query is not a mystery. The plan says what the engine decided to do and
the profile says what that cost. The skill is reading them in the right order
and knowing which number lies.

Everything version-dependent below is marked. Index behaviour in particular
shifts between Neo4j releases — **check it against your own server with
`PROFILE` before quoting it to anyone.** That check takes two minutes and is the
difference between knowing and remembering.

---

## 1. `EXPLAIN` or `PROFILE`

```cypher
EXPLAIN MATCH (n:Account {aid: $id})-[:PAID*1..4]-(m) RETURN count(m);
```

Plans the query and **does not run it**. You get the operator tree and the
planner's *estimated* rows. Use it when you do not yet know whether the query is
safe to run — which is most of the time on an unfamiliar graph.

```cypher
PROFILE MATCH (n:Account {aid: $id})-[:PAID*1..4]-(m) RETURN count(m);
```

**Runs the query** and adds actual rows, db hits, memory and page-cache figures
per operator.

Two cautions. `PROFILE` on a write query performs the write. And `PROFILE` adds
measurement overhead, so its wall-clock time is not the query's real time —
another reason not to read the milliseconds.

---

## 2. Read db hits, not milliseconds

A **db hit** is one access to the storage engine: reading a node, a
relationship, a property, an index entry.

Time moves with the page cache, with what else the server is doing, and with
whether you ran the query a minute ago. Db hits do not. The same query on the
same data produces the same db hits on a cold cache and a warm one.

This is the difference between *"it took 278 ms the first time and 1 ms after"* —
which says nothing — and *"it costs 340 000 db hits"*, which is a property of
the query and the data.

The one number where cache matters is **page cache hits and misses**. High
misses mean the data is not resident; that is a hardware and sizing
conversation, not a query one.

Worth knowing: `COUNT { (n)--() }` compiles to `getDegree` and costs **zero db
hits** — the degree lives in node metadata. `size([(n)--() | 1])` walks the
relationships and does not. Two ways to write the same question, orders of
magnitude apart.

---

## 3. The operators worth recognising

Read the tree from the bottom. The leaves are how rows enter; everything above
is what happens to them.

**How rows enter**

| Operator | What it means |
|---|---|
| `NodeUniqueIndexSeek` | Best case. One node, by a unique constraint. |
| `NodeIndexSeek` | Index lookup by value or range. What you usually want. |
| `NodeIndexScan` | Reads the whole index. Better than a label scan, far worse than a seek. |
| `NodeIndexContainsScan` / `EndsWithScan` | The predicate could not seek. A TEXT index helps here; a RANGE index cannot. |
| `NodeByLabelScan` | Every node of a label, via the token lookup index. Fine on a small label, not on a large one. |
| `AllNodesScan` | **Every node in the database.** Almost always a missing label in the pattern. |

**How rows expand**

| Operator | What it means |
|---|---|
| `Expand(All)` | Follow relationships to nodes not yet bound. Normal. |
| `Expand(Into)` | Both ends already bound; checks whether the relationship exists. Cheap when the degree is low, expensive on a super-node. |
| `VarLengthExpand(All)` | Enumerates **paths**. This is the one that explodes. |
| `VarLengthExpand(Pruning)` | Enumerates **distinct reachable nodes**. The planner can use it when you never look at the paths themselves. |

That last pair is the single most useful thing in this file. Path count and
reachable-node count are different quantities. On one fixture, between depth 4
and depth 5 the reachable set grew 1.3% while the path count grew 23.6×. If your
query asks *"which nodes can I reach"* and not *"by which routes"*, say so in the
query — `RETURN DISTINCT m` rather than `RETURN p` — and the planner can prune.

**Red flags in the middle of a tree**

| Operator | Why it matters |
|---|---|
| `CartesianProduct` | Two disconnected patterns. Rows multiply. Almost always a missing relationship or a forgotten `WITH`. |
| `Filter` with huge input rows | The predicate is applied *after* the expansion that produced those rows. Move it into the pattern. |
| `Eager` | Materialises everything before continuing. In a write query this is where memory goes. |
| `EagerAggregation` | Normal for `count`/`sum`, but note it holds a row per group. |
| `Sort` on a large input | A range index can supply sorted order and remove this. |

---

## 4. Index types — what each one can actually do

There are six kinds, and three of them the planner will never choose for you.

**LOOKUP (token lookup).** Two exist by default: one for node labels, one for
relationship types. It is what makes `MATCH (n:Label)` a `NodeByLabelScan`
rather than an `AllNodesScan`. Rarely thought about, catastrophic if dropped.

**RANGE.** The default, and the one you create without thinking. Supports
equality, `<`, `>`, ranges, `STARTS WITH`, and `IS NOT NULL`. It can also supply
**sorted order**, which removes a `Sort` operator.

**TEXT.** Strings only. Supports equality, `STARTS WITH`, `ENDS WITH` and
`CONTAINS`. The last two are the reason it exists — a RANGE index cannot seek on
them and falls back to scanning. No ranges, no ordering.

**POINT.** Spatial: distance and bounding-box predicates.

**FULL-TEXT.** Lucene-backed: analyzers, scoring, fuzzy matching, boolean
queries. **The planner never picks it.** You call it:

```cypher
CALL db.index.fulltext.queryNodes('<index>', 'query terms') YIELD node, score
RETURN node, score LIMIT 10;
```

**VECTOR.** k-nearest-neighbour over embeddings, cosine or euclidean. **The
planner never picks this either.** You call it:

```cypher
CALL db.index.vector.queryNodes('<index>', 10, $embedding) YIELD node, score
RETURN node, score;
```

That "never automatically" is the part people get wrong. A full-text or vector
index sitting in `SHOW INDEXES` does nothing for an ordinary `MATCH`. If the
query does not call the procedure, the index is not involved — and this is
exactly how a GraphRAG retrieval layer ends up silently doing a label scan.

### When the planner ignores an index you thought it would use

- **A function wraps the property.** `WHERE toLower(c.name) = 'x'` cannot seek an
  index on `name`. Store the normalised form as its own property and index that.
- **Type mismatch.** The property holds a string, the parameter is an integer.
  No seek, and no error either.
- **Negation.** `WHERE NOT n.prop = $x` and `<>` generally cannot seek.
- **`OR` across different properties.** May become a scan, or a union of seeks,
  depending on version.
- **Property compared to another property** on the same node.
- **The index is not `ONLINE`.** Check `populationPercent` in `SHOW INDEXES`.
  A freshly created index on a large graph is not usable until it is built.
- **`CONTAINS` / `ENDS WITH` with only a RANGE index** — the plan shows a
  `ContainsScan`, which reads the whole index.
- **Composite indexes** need predicates on their properties, and the leading
  ones generally need equality. A composite on `(a, b)` does not serve a query
  that filters only on `b`.

---

## 5. Cardinality — where the planner is wrong

`EXPLAIN` gives estimated rows. `PROFILE` gives estimated **and** actual.

**Find the first operator, reading from the bottom, where the two diverge by an
order of magnitude.** Everything above it was planned on a false premise, so
that operator is where to look — not the one that took longest.

Why the estimate goes wrong:

**Stale statistics.** The planner uses label counts, relationship-type counts
and index selectivity. After a bulk load these can lag.

```cypher
CALL db.awaitIndexes();
CALL db.resampleOutdatedIndexes();
```

**Skew.** Index selectivity is an *average*. If one value covers half the rows —
a clearing account holding 55% of the payments — the average is wrong for
exactly the case you care about. This is the same super-node that ruins the
traversal, showing up in the planner.

**Correlated predicates.** The planner multiplies selectivities as if the
predicates were independent. `WHERE c.town = 'Bratislava' AND c.status =
'active'` is estimated as the product of two fractions, which is wrong whenever
the two are related — and in real registers they usually are.

**Variable-length paths.** Estimates for `*1..4` are rough by construction and
get worse with depth. This is precisely why a separate budget probe exists: the
planner's own estimate is not something to bet a client's afternoon on.

**Plan caching by parameter type.** A cached plan was chosen for the parameter
types it first saw. To force a fresh one:

```cypher
CYPHER replan=force PROFILE MATCH ...
```

### What to do about it

In order of preference:

1. **Rewrite the query.** Move predicates into the pattern; split into `WITH`
   stages so the planner gets a smaller intermediate; give it an anchor with a
   selective property.
2. **Add the right index**, or normalise the property so an index can be used.
3. **Hint — as a diagnostic, not a fix.**

```cypher
MATCH (n:Company)
USING INDEX n:Company(ico)
WHERE n.ico = $value
RETURN n;
```

Also `USING SCAN n:Label` and `USING JOIN ON n`.

If forcing the index makes the query dramatically faster, the planner's estimate
was wrong — and *that* is your finding. Understand why, fix the cause, and take
the hint back out. A hint left in the code is a plan frozen against data that
will keep changing.

---

## 6. A worked reading

The question: *how much does a four-hop traversal from this account cost?*

```cypher
PROFILE
MATCH (n:Account {aid: $id})
MATCH p = (n)-[:PAID*1..4]-(m)
RETURN count(p) AS paths;
```

Read it bottom-up and ask four things in this order:

1. **How did rows enter?** `NodeUniqueIndexSeek` on `aid` — one row. Good. If it
   said `NodeByLabelScan`, the constraint is missing or the predicate could not
   seek, and everything above is being done 1 838 times instead of once.
2. **Which expansion dominates?** `VarLengthExpand(All)`, and its db hits are
   effectively the whole query. If the question had been *which accounts* rather
   than *by which routes*, `RETURN DISTINCT m` would let the planner prune and
   the number would collapse.
3. **Where do estimated and actual diverge?** If the planner expected 40 000 rows
   out of the expansion and got 2 000 000, it is because of the super-node — and
   no amount of index work fixes that. The fix is excluding the node.
4. **Is there a `Filter` above the expansion with a large input?** If so, the
   predicate ran after two million rows already existed. Move it inside:
   `[:PAID*1..4]` becomes a pattern with `all(r IN relationships(p) WHERE ...)`,
   or better, a relationship property filter the planner can push down.

---

## 7. The checklist

```
[ ] EXPLAIN before PROFILE on an unfamiliar graph
[ ] never PROFILE a write query you are not ready to perform
[ ] db hits read, not milliseconds
[ ] no AllNodesScan
[ ] no CartesianProduct that was not intended
[ ] rows entering: seek, not scan — and if a scan, understood why
[ ] VarLengthExpand: (Pruning) if the paths are not needed, (All) only if they are
[ ] no Filter sitting above a large expansion
[ ] estimated vs actual compared per operator, bottom-up
[ ] first divergence identified, not the slowest operator
[ ] index state ONLINE and populationPercent 100
[ ] full-text and vector indexes called explicitly — the planner will not
[ ] any USING hint is temporary and its reason is written down
```

---

## What I have not verified

Index-predicate support and operator names differ across Neo4j versions, and the
table in §4 is from documentation plus testing on 2026.09 enterprise. Before
quoting any of it to a client, run `PROFILE` on their server and look at the
operator name yourself.

The planner statistics section describes behaviour I have read about and
partially observed; I have not measured how stale statistics must be before the
plan changes. That is a gap, and it is the kind of gap worth closing with an
experiment rather than more reading.
