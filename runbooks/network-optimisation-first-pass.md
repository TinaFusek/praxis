# Optimising a network you have just been handed

"Can you optimise our network?" is not a task. It is four different tasks
wearing the same sentence, and the first hour decides which one you are doing.

This runbook is domain-agnostic on purpose. A telecom topology, a field-service
routing problem, a hand-off chain between teams and a distribution network are
different subjects with the same skeleton: things, the links between them,
something that flows, a cost, and a small set of things anyone is allowed to
change.

`<NODE>`, `<LINK>`, `<WEIGHT>`, `<CAPACITY>` are placeholders throughout.

---

## 0. Before any of this

Run `new-graph.md` first. Optimising a graph you have not profiled means tuning
against defects you have not found, and the number you produce will be wrong in
a way that looks right.

In particular you need, before you start: the degree tail, the components, the
completeness of whatever property you are about to use as a weight, and the
budget multiplier `k`.

---

## 1. Find out which network this is — 30 min, day one

Do not ask "what will I be working on". Ask:

> **What flows through this network, and what does the customer pay for?**

That one question separates the four cases, because the answer names the unit
the objective has to be written in.

| What flows | What it usually is | What "optimise" turns out to mean |
|---|---|---|
| Packets, calls, bandwidth | Telecom / IP topology | Latency, congestion, resilience, capacity planning |
| People and vehicles | Field service, logistics | Travel time, jobs per day, SLA hit rate |
| Work items, tickets, documents | Process network | Handover delay, rework, queue length |
| Goods | Distribution | Stock-out rate, transport cost, lead time |

Four more questions, in this order, and write the answers down verbatim:

1. **What is the number you want to move, and in which direction?**
   If nobody can name it, there is no optimisation problem yet — there is a
   measurement problem, and that is a different engagement. Say so early.

2. **What is it now?** No baseline, no result. A result without a baseline is an
   opinion with a decimal point.

3. **What are you allowed to change?** This is the question people skip, and it
   is the one that decides whether the work is useful. A perfect answer that
   requires laying new fibre, when the budget covers reconfiguration only, is
   worth nothing.

4. **What must not get worse?** Every optimisation trades something away. If the
   customer does not name the constraint, you will find it by breaking it.

The third and fourth answers are the actual specification. The first two are how
you will be judged.

---

## 2. Write the objective as a formula — 30 min

Before any algorithm:

```
minimise   <what>
subject to <what must not get worse>
by changing <what you are allowed to change>
measured as <a query that returns one number>
```

If you cannot fill all four lines, do not proceed. Go back to §1.

That last line is not decoration. Write the query now, run it on today's data,
and keep the output. It is your baseline and it is also your regression test —
every candidate change gets measured with the same query, unchanged.

**Two failure modes to name out loud:**

*An optimisation without a cost function is a preference.* "Make it better"
resolves to whatever the analyst happens to like.

*The thing you are allowed to change is usually not the thing that is wrong.*
Say that in the report. A finding the customer cannot act on is still worth
reporting — but it must be labelled, so nobody expects it in the numbers.

---

## 3. Build the projection carefully — 20 min

The projection decides which questions are askable. Three decisions, and all
three are wrong by default.

**Direction.** A physical link is usually undirected; a payment, a handover or a
dependency is not. Some algorithms refuse a directed graph; others silently
answer a different question on one.

**Weight.** Almost every useful algorithm needs a weight, and almost every
graph's weight property is incomplete. Check it before projecting:

```cypher
MATCH ()-[r:`<LINK>`]->()
RETURN count(r) AS total, count(r.`<WEIGHT>`) AS with_weight,
       count(r) - count(r.`<WEIGHT>`) AS missing,
       min(r.`<WEIGHT>`) AS min, max(r.`<WEIGHT>`) AS max;
```

A missing weight is not zero. In a shortest-path algorithm a missing weight
treated as zero becomes the **preferred** route — the cheapest path in the graph
runs through the edges you know least about. This is the single most common way
a network optimisation produces a confidently wrong answer.

Decide explicitly: exclude those edges, or impute a value and say so in the
report. Never let the default decide.

**Scope.** Project the subgraph you are optimising, not the whole database. A
clearing account, a shared gateway or a catch-all node connects everything to
everything and makes every distance short.

```cypher
CALL gds.graph.project(
  'net',
  '<NODE>',
  {LINK: {type: '<LINK>', orientation: 'UNDIRECTED',
          properties: {`<WEIGHT>`: {property: '<WEIGHT>',
                                    defaultValue: null}}}}
);
```

For anything more selective, use a Cypher projection. The procedure name for
that has changed across GDS versions, so check what your server has rather than
copying it from anywhere:

```cypher
CALL gds.list() YIELD name WHERE name CONTAINS 'project' RETURN name;
```

And when you are done:

```cypher
CALL gds.graph.drop('net', false) YIELD graphName RETURN graphName;
```

A stale projection is stale data that looks current. Drop it in the same session
that made it.

---

## 4. Pick the algorithm from the question, not the list

This table is the core of the runbook. Read the middle column, not the left one.

| Question the customer actually asked | Method | Watch out for |
|---|---|---|
| What is the best route from A to B? | `gds.shortestPath.dijkstra` | Needs a weight. Unweighted "shortest" means fewest hops, which is rarely what anyone means. |
| What if that route is unavailable? | `gds.shortestPath.yens` (k shortest) | The 2nd-best route often shares an edge with the best, so it fails at the same time. Check overlap. |
| How far apart is everything? | `gds.allShortestPaths.dijkstra` | O(n²) output. Sample, or restrict the source set. |
| Which nodes are the bottlenecks? | `gds.betweenness` | **Assumes all traffic takes shortest paths.** Real traffic does not. See below. |
| Where is the capacity limit? | max-flow / min-cut | Not necessarily shipped with GDS — check `gds.list()`. Otherwise extract the subgraph and use `networkx` in Python. |
| Where should we put a depot / cache / hub? | `gds.closeness`, or k-median on the distance matrix | Closeness ignores demand. Weight by how much each node actually needs. |
| What is the cheapest way to connect all of this? | `gds.spanningTree` (minimum weight) | Gives a tree: no redundancy at all. Almost never buildable as-is. |
| Cheapest way to connect *these specific* sites? | `gds.steinerTree` | Approximate. State that in the report. |
| Where does it fall apart if one thing dies? | articulation points / bridges | Check `gds.list()` for availability. Otherwise: remove each node, re-run WCC, see what splits. |
| What are the natural regions? | `gds.louvain` / `gds.leiden` | Leiden needs an undirected projection. And a chain has no community structure — if the graph is a line, these will invent groups. |
| Which parts are not connected at all? | `gds.wcc` | Already in the profile. If you find one here, your profile was incomplete. |
| Who depends on whom, in a cycle? | `gds.scc` | Strongly connected components: the directed version, and a different answer from WCC. |

### The distinction worth being right about

**Betweenness centrality answers "which node lies on many shortest paths."**
**Min-cut answers "where does capacity actually run out."**

They are not the same question and they frequently disagree. Betweenness assumes
every unit of flow takes a shortest path; real networks route around congestion,
follow policy, or are simply configured differently. A node with high betweenness
and enormous spare capacity is not a bottleneck. A node with modest betweenness
and one saturated link is.

Reporting betweenness as "your bottleneck" is a mistake that survives the whole
engagement, because it sounds authoritative and is never checked. If you have
capacity data, use it. If you do not, say **"high betweenness under a
shortest-path assumption"** — in those words — and put the assumption in the
report.

---

## 5. Measure the baseline before touching anything — 30 min

Run the objective query from §2 and save the output to a file with a date in the
name.

Then, for every number you are about to quote, three things:

- **n** — over how many nodes, links, or observations
- **the period** — if the data is time-stamped, which window
- **what is excluded** — the edges with missing weights, the disconnected
  components, the test records

Every one of those exclusions came out of the profile in §0. If you cannot list
them, you did not do §0.

---

## 6. Propose changes, and write the prediction first

For each candidate change, before running anything:

```
change:       what exactly is modified
prediction:   the objective moves from X to Y, because <mechanism>
cost:         what it takes to do
risk:         what could get worse
```

**Write the prediction before the measurement.** A prediction written afterwards
is not a prediction, and the only way to find out whether your model of the
network is right is to be wrong in writing.

Then evaluate each one with the *same, unchanged* query from §2. If you improve
the query while evaluating, you have two variables and no result.

**Do not evaluate on the data you tuned on.** If you chose the intervention by
looking at last month's traffic, measuring it on last month's traffic tells you
nothing. Hold a period back, or say plainly that the numbers are optimistic.
Measured on a held-out case, my own estimator was 13.6% off; on the tuning case,
5.9%. Only the first was worth quoting.

---

## 7. What to hand over

**The number, with its baseline.** "From 4.2 to 3.1" — never "improved by 26%"
without both ends.

**The assumption behind it.** Especially if it is the shortest-path assumption.

**What you could not assess.** Capacity data you did not have, a time period you
did not cover, seasonal behaviour, failure modes.

**What is wrong but out of scope.** The finding they cannot act on today is
often the most valuable thing in the report — as long as it is in its own
section and not mixed into the numbers.

**Questions only they can answer.** Some of what you found is not a defect. A
link that looks redundant may be a contractual obligation; a node that looks
isolated may be a customer who left.

---

## The checklist

```
[ ] new-graph.md run first — profile before optimising
[ ] what flows, and what the customer pays for — answered in their words
[ ] the number to move, and its current value
[ ] what they are ALLOWED to change
[ ] what must not get worse
[ ] objective written as a formula, and as one query
[ ] projection: direction decided, weight completeness checked,
    missing weights excluded or imputed explicitly
[ ] hub / gateway / catch-all node excluded or justified
[ ] algorithm chosen from the question, not from the list
[ ] betweenness reported as "under a shortest-path assumption", or not at all
[ ] baseline saved to a dated file before any change
[ ] n, period and exclusions stated beside every number
[ ] prediction written before each measurement
[ ] evaluation on held-out data, or labelled optimistic
[ ] projection dropped
[ ] "not assessed" and "out of scope" sections written
```

---

## The two sentences to keep

*An optimisation without a cost function is a preference.*

*The thing you are allowed to change is usually not the thing that is wrong —
report both, in separate sections.*
