# praxis

**Procedures I actually run, with the code that runs them.**

Not notes from a textbook. Each runbook is a sequence I have followed, with the
mistakes that shaped it named in place — most of them mine.

---

## Runbooks

**[First hour with a graph somebody else built](runbooks/new-graph.md)**
What to establish before writing a single query that answers the task: where you
actually are, what is in the graph, what is *in* the properties, what only shows
up when you aggregate, what shape the whole thing has, and what a traversal will
cost.

**[Cypher cookbook](runbooks/cypher-cookbook.md)**
Thirty-five queries filed under the question they answer, each with what it
costs and what it does not see. The drill at the bottom is the point: cover the
queries, read the questions, write the Cypher from memory.

**[Reading a query plan](runbooks/reading-a-query-plan.md)**
Why db hits and not milliseconds, which operators are red flags, what each of
the six index types can actually do — and which three the planner will never
choose for you — and how to find the point where its estimate went wrong.

**[Optimising a network you have just been handed](runbooks/network-optimisation-first-pass.md)**
"Can you optimise our network?" is four different tasks wearing one sentence.
Which question to ask first, how to write the objective so it can be measured,
which algorithm answers which question — and why betweenness centrality is not
a bottleneck finder.

**[First week on a client engagement](runbooks/first-week-with-a-client.md)**
The errors available before you write any code are larger than any query
mistake. What to ask, what to record, how to give an estimate, and what to do
when you find something bad.

**[Honest null](runbooks/honest-null.md)**
Why the system said "I don't know", and whether it was telling the truth. Eight
states behind one refusal, the order to test them in, the ground-truth set
without which coverage gaps cannot be told apart from retrieval bugs — and
cross-language retrieval, which belongs here because a language mismatch
produces a null that claims the fact does not exist. The last section maps the
same states onto a network and its monitoring, where "no alarm" and "no data"
draw the same flat line.

**[Before you publish a number](runbooks/before-you-publish-a-number.md)**
Twelve checks, each one a mistake I made and had to correct. Two became public
retractions. Weighted toward the errors that make you look *better*, because
those are the ones nobody finds for you.

---

## Scripts

```bash
pip install -r requirements.txt
export NEO4J_PASSWORD=...        # never pass credentials in argv
```

Every example below uses `<LABEL>`, `<REL>` and `<KEY>` placeholders. Read the
real ones out of the profile first. An example from another graph, pasted
verbatim, fails with `no :Person nodes with a 'uid' property` — which is how
that rule came to be written down.

### `graph_profile.py` — read-only profile of an unfamiliar graph

```bash
python scripts/graph_profile.py --database neo4j
python scripts/graph_profile.py --database neo4j --json > profile.json
python scripts/graph_profile.py --database neo4j --quick          # skip value shape
python scripts/graph_profile.py --database neo4j --no-components  # skip WCC
```

Seven passes, in an order where each answer changes what the next one means:
context, counts, schema, degree distribution, **connected components**,
**property completeness in three states**, and **value shape**.

The last three were added after running this against a graph I had not seen.
They matter for different reasons:

*Components* is one call and two seconds, and it is the only thing that sees a
subgraph connected to nothing. No sample reaches it, no spot check reaches it,
no traversal from a real record reaches it. It used to sit in the analysis
phase, where it would have been skipped.

*Completeness in three states* — null, blank and present counted separately —
because whoever wrote the loader decided which one you get. `LOAD CSV` turns
empty fields into `null`; an application writing directly usually leaves `''`,
which `IS NOT NULL` happily reports as populated. A completeness figure that
does not say which state it counted is not a number.

*Value shape* — distinct counts, low-cardinality value lists, and grouping
strings by length — because completeness says whether a property is there, not
what is in it. Four findings in one hour came from this pass alone: a field with
ten distinct values for fifteen hundred rows, leftover test records sitting in
the same label as production data, three date formats in one string column, and
one identifier that did not match the shape of the others.

It reads only. With `--components` (the default) it creates one named in-memory
GDS projection and drops it in the same run; nothing else is written.

### `traversal_budget.py` — estimate a traversal before running it

```bash
python scripts/traversal_budget.py --calibrate --label <LABEL> --rel <REL> \
                                   --key <KEY> --depth 4
python scripts/traversal_budget.py --check <ID> --label <LABEL> --rel <REL> \
                                   --key <KEY> --depth 4 --k 550 --budget 1000000
```

Start-node degree looks like the obvious cheap signal and does not work: a 10.6×
spread against observed path counts, and it sometimes ranks nodes backwards,
because degree says how many neighbours a node has, not what those neighbours
are.

Sum of neighbour degrees gives 1.7× at a comparable cost, and it holds across an
80× degree range. But the multiplier is a property of the graph, not of the
method — 1.0 on a star, 2–3 on a sparse ownership graph, 5–8 on a causal DAG, 26
on a payment graph at depth 3, 533–780 on small-world at depth 4 — so calibrate
per graph and never copy a threshold between deployments.

Three defects in this script were found by using it on an unfamiliar graph, and
all three were in how it *reported*, not in what it measured:

| Was | Is |
|---|---|
| A frontier of zero counted as a failed probe, with the status string printed in the `paths` column | Zero frontier is reported as a measurement: that node has no second-hop neighbours |
| The 2× spread rule applied regardless of magnitude — it "failed" on path counts of 2, 3 and 9 | The rule needs a minimum median path count before it fires |
| Probe cost quoted as ~3 ms, a property of the method | Cold and warm reported separately: 2 174 ms first call, 15.7 ms after |

And the accuracy figure worth quoting is the held-out one. On a node inside the
calibration set the estimate was 5.9% off; on a node outside it, 13.6%.

### `discrepancy_report.py` — data-quality checks, then an HTML report

```bash
python scripts/discrepancy_report.py --database neo4j --out report.html
python scripts/discrepancy_report.py --database neo4j \
       --rules examples/rules-register.json --out report.html
python scripts/discrepancy_report.py --example-rules      # print a starter rules file
```

Fourteen checks that need no knowledge of the schema, and four that cannot work
without it.

The generic fourteen are generated from what the database actually contains:
labels with no constraint, properties that are incomplete — with null, blank and
present counted separately — identifier-like properties that turn out to have
duplicates, orphan nodes, super-nodes, self-loops, relationship properties with
missing values, string properties holding more than one format, rare values in
low-cardinality columns, and disconnected components.

The other four take a small JSON rules file, because no amount of schema
discovery reveals that two relationship types contradict each other, that a
percentage column must not exceed 100, or which properties are mandatory.

Two things the report does that most quality reports do not. Every finding
carries **the query that produced it**, because a finding you cannot reproduce
is not a finding and a client will ask. And a check that errors is **counted in
the report** rather than dropped — a report whose denominator quietly shrank is
worse than one with a visible gap.

The severities are defaults. Whether a finding is a defect is a question for
whoever owns the data, and the report says so.

### `determinism_check.py` — same input, same output

```bash
python scripts/determinism_check.py --runs 3 -- python scripts/pipeline.py
python scripts/determinism_check.py --ignore '\d{4}-\d{2}-\d{2}' -- ./run.sh
```

Runs a command N times and diffs. Two runs on identical data once gave me 5 474
and 5 476 clusters — iteration over a set, ties broken by hash order. One
`sorted()` fixed it.

Non-determinism is the defect that does not announce itself: the run succeeds,
the number looks plausible, and nobody notices until two people compare results.

---

## Why this exists

Method transfers between domains; specific findings mostly do not. The same
questions come up whether the subject is archival paper, a retrieval system, or
a register of companies:

**What does the instrument actually measure?** A metric that scores a perfect
case at 0.333 instead of 0 will flatten a real signal into noise — the finding
does not change, the instrument does. A profile that counts `IS NOT NULL` will
report an empty string as data.

**What does the data make impossible?** Find the ceiling before tuning against
it. Anyone promising accuracy above it is either not measuring or overfitting.

**Which of my findings is not a finding?** Some structures are legitimate and
look like defects. Calling one a bug in front of a client costs more than
missing it, because it is said out loud and has to be walked back. Findings and
questions-for-the-owner are two lists, not one.

**What did I not check?** A report without that block implies coverage it does
not have.

---

## Scope

Examples come from synthetic data and from systems I built. Figures quoted in
the runbooks are from those measurements — they demonstrate method and are not
benchmarks of anything.

Nothing here contains a client's data, internals, or identifying detail.

---

## Related

- [VERITAS](https://github.com/TinaFusek/veritas) — benchmark that scores the
  reasoning path of a GraphRAG system rather than its answer text
- [NORMA](https://github.com/TinaFusek/norma) — assessing whether a graph
  platform can sit underneath an agent that writes its own queries
- [CONCORDIA](https://github.com/TinaFusek/concordia) — entity resolution on
  synthetic register data, every stage measured separately

MIT licensed. Martina Fusková, PhD
