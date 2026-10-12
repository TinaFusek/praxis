# First hour with a graph somebody else built

You have been given access to a Neo4j instance and a task. Before you write a
single query that answers the task, spend an hour finding out what you are
holding.

Skipping this is how you end up three weeks in, explaining why a number is wrong.

**Every command below uses `<LABEL>`, `<REL>`, `<KEY>` placeholders on purpose.**
Read the real ones out of step 2. A worked example from a different graph,
pasted verbatim, produces `no :Person nodes with a 'uid' property` — which is
how this rule got written.

Timings are from a 4 000-node / 24 000-relationship graph. Scale the budget, not
the order.

---

## 1. Establish where you actually are — 2 min

Four attempts at a tenant-isolation probe failed before I noticed the queries
were running against a different database than the UI showed. Nothing was wrong
with the queries. What was missing was verification of context.

```cypher
CALL db.info() YIELD name RETURN name;
```
```cypher
CALL dbms.components() YIELD name, versions, edition
RETURN name, versions[0] AS version, edition;
```
```cypher
SHOW DATABASES;
```

Write all three into the header of your findings, with the words "verified with
`CALL db.info()`, not read from the UI".

The same class of error has four faces, and all four have bitten me:

| Looks configured | Actually applies | How to check |
|---|---|---|
| Database in the UI dropdown | Database the session uses | `CALL db.info()` |
| `server.directories.import` in the config | Path the server reads | `SHOW SETTINGS` |
| Heap size in the container config | Flags the JVM received | `docker exec … ps -o args=` |
| A path printed by the server | A path that exists on disk | `Test-Path` / `ls` |

The fourth is the newest and the least obvious: a setting is a declaration of
intent, and the directory it names may simply not have been created yet.

**Every probe asserts its own context before measuring.** A client that takes
the database as an explicit parameter — the driver — cannot drift; a UI that
infers it can.

**Never retype a path, an id or a key out of a result.** Derive it:

```powershell
$dbms = Get-ChildItem "$HOME\.Neo4jDesktop2\Data\dbmss" -Filter "dbms-<prefix>*"
$imp  = Join-Path $dbms.FullName "import"
```

Eight characters of prefix are enough to find it; let the machine supply the
rest. I transposed two characters of a UUID and spent ten minutes on it while
writing this runbook.

---

## 2. Profile before you analyse — 5 min

```bash
export NEO4J_PASSWORD=...
python scripts/graph_profile.py --database neo4j
python scripts/graph_profile.py --database neo4j --json > profile.json
```

Keep the JSON. You will want it when you rewrite this runbook.

Read the output in this order, because each answer changes what the next one
means:

**Counts.** Labels, relationship types, totals. If a label you were told about
has zero nodes, stop and ask before doing anything else. This is also where you
read the real `<LABEL>`, `<REL>` and `<KEY>` for every command below.

**Constraints, and which label does *not* have one.** No constraint means node
identity is not enforced, which means duplicates are possible and your counts
may already be wrong. Two constraints out of three labels is more dangerous than
none, because it looks deliberate. And note: the constraint you need is usually
on the *business* key, not on the technical one — a unique `cid` prevents
nothing if the duplicates carry different `cid` values.

**Degree distribution.** Look at max and p99, never the mean. A graph with a
mean degree of 5 and a max of 15 000 is not a graph with a mean degree of 5 — it
is a graph with a super-node, and your traversals will die on it.

**Components.** One call, two seconds, and it is the only thing that sees a
subgraph connected to nothing. It used to be in the analysis phase of this
runbook. It found a 56-node component that nothing else in the hour would have
reached, so it now runs inside the profile.

**Property completeness — null, blank and present counted separately.** A
property that is 60% populated breaks every rule built on it, silently, and
nothing errors. Say this to the client *before* you analyse, not after they
question the result.

The three states matter because whoever wrote the loader decided which one you
get. `LOAD CSV` converts empty fields to `null`, so after a CSV load the blanks
have already become nulls; an application writing directly usually leaves `''`,
which `IS NOT NULL` reports as populated. A completeness figure that does not
say which state it counted is not a number.

---

## 3. Look at the values, not just at whether they exist — 8 min

This step did not exist in the first version of this runbook. Four findings in
one hour came from it and from nowhere else.

Three questions per property. `graph_profile.py` now answers them, but run them
by hand too when something looks off:

**How many distinct values?**

```cypher
MATCH (n:`<LABEL>`) RETURN count(*) AS total, count(DISTINCT n.`<KEY>`) AS distinct;
```

Equal means unique. Far smaller means either a genuine category or a field that
is coarser than its name suggests — ten towns for fifteen hundred companies is
not an address.

**What are they, when there are few?**

```cypher
MATCH (n:`<LABEL>`) RETURN n.`<PROP>` AS value, count(*) AS n ORDER BY n DESC;
```

Leftover test records live here, in the same label as production data.

**How long are they?**

```cypher
MATCH (n:`<LABEL>`) WHERE n.`<PROP>` IS NOT NULL
RETURN size(n.`<PROP>`) AS len, count(*) AS n, collect(n.`<PROP>`)[0] AS example
ORDER BY n DESC;
```

Grouping a string by length separates date formats, identifier formats and
outliers without knowing in advance what the formats are. It is coarse: two
formats of the same length stay merged, so a single length class is *unverified*,
not proof of consistency.

**And test identity directly.** The cheapest way to ask "does this property
identify a row" is to try to enforce it:

```cypher
DROP INDEX <index_on_that_property> IF EXISTS;
CREATE CONSTRAINT tmp_check FOR (n:`<LABEL>`) REQUIRE n.`<PROP>` IS UNIQUE;
```

If duplicates exist, the error names one. Then put it back:

```cypher
DROP CONSTRAINT tmp_check IF EXISTS;
CREATE INDEX <index_on_that_property> IF NOT EXISTS FOR (n:`<LABEL>`) ON (n.`<PROP>`);
```

The `DROP INDEX` first is not optional. A range index on the same property
makes the constraint fail with `22N73 constraint conflicts with existing index`
— which looks exactly like a failure and is a different failure. **Read the
error code, not the outcome.**

---

## 4. Aggregate — 6 min

Nothing in this section is visible in a single row, in a sample of ten, or in
any node-level profile.

**Counts that must agree.**

```cypher
MATCH (n:`<LABEL>`) RETURN count(n);
MATCH (n:`<LABEL>`)-[:`<REL>`]->() RETURN count(DISTINCT n);
```

A difference of one is still a finding. Chase it:

```cypher
MATCH (n:`<LABEL>`) WHERE NOT (n)-[:`<REL>`]->()
RETURN n, COUNT { (n)--() } AS degree;
```

**Missing numeric values on relationships.**

```cypher
MATCH ()-[r:`<REL>`]->()
RETURN count(r) AS total, count(r.`<PROP>`) AS with_value,
       count(r) - count(r.`<PROP>`) AS missing;
```

`sum()` ignores nulls silently, so every total is low by an unknown amount and
nothing complains. Report `with_value + missing == total` beside every
aggregate — the same rule as `n_scored + n_excluded == set size`.

**Sums grouped by one endpoint.**

```cypher
MATCH ()-[r:`<REL>`]->(n:`<LABEL>`)
WITH n, sum(r.`<PROP>`) AS total, count(r) AS contributors
RETURN n, round(total, 3) AS total, contributors ORDER BY total DESC LIMIT 10;
```

Ask whether the total can exceed a domain limit. Shares summing above 100% mean
either the numbers are wrong or the column holds more than one kind of number.
You cannot tell which from the data, and that is the point: it becomes a
question, not a finding.

---

## 5. Look at the shape — 5 min

**Is it one graph?** Already in the profile. If it is not, look at what is in
the other components before anything else. A component that transacts only with
itself is invisible to every query that starts from a real record.

**Are there paths that return to where they started?** Start short and grow.

```cypher
MATCH (a)-[r]->(a) RETURN type(r) AS type, count(*) AS n;
```

```cypher
MATCH p = (a:`<LABEL>`)-[:`<REL>`*2..3]->(a)
RETURN [n IN nodes(p) | n.`<KEY>`] AS cycle LIMIT 10;
```

Two things to know before reporting any of it.

*A cycle of length n returns n rows* — the same cycle seen from each of its
members. Five rows are not five cycles.

*Not every cycle is a defect, and the two need different checks.* A node
pointing at itself is almost always an error. A three-company cross-holding is
legal, common, and deliberate — reporting it as a bug costs more than missing
it. A check that flags "cycles" produces both and distinguishes nothing; a check
that flags "cycles of length 1" produces only the error.

What a legitimate cycle does break is your method: ownership through a cycle
cannot be computed by traversal, because the traversal does not terminate. It
converges to a finite value and needs a matrix solve. That sentence — *"you have
cross-holdings, so effective ownership is solved rather than traversed, and here
is what that costs"* — is the deliverable.

**Longer cycles are the expensive case.** Budget them first (step 6). Then
exclude the super-node explicitly, on every node of the path, and put the filter
*inside* the pattern:

```cypher
MATCH p = (a:`<LABEL>`)-[:`<REL>`*5..5]->(a)
WHERE a.`<KEY>` <> '<SUPERNODE>'
  AND all(n IN nodes(p) WHERE n.`<KEY>` <> '<SUPERNODE>')
  AND all(r IN relationships(p) WHERE r.`<PROP>` IS NOT NULL
                                  AND r.`<PROP>` > <THRESHOLD>)
RETURN [n IN nodes(p) | n.`<KEY>`] AS cycle LIMIT 10;
```

And pick the threshold from the distribution **of the set you are searching**,
not of the whole graph. Percentiles computed with the super-node included
describe a different population.

---

## 6. Budget your traversals — 6 min

```bash
python scripts/traversal_budget.py --calibrate --label <LABEL> --rel <REL> \
                                   --key <KEY> --depth 4
```

This calibrates the multiplier for *this* graph. Copying a threshold between
deployments is wrong by two to three orders of magnitude — the same predictor
gives 1.0 on a star, 2–3 on a sparse ownership graph, 5–8 on a causal DAG, 26 on
a payment graph at depth 3, 533–780 on a small-world graph at depth 4.

```bash
python scripts/traversal_budget.py --check <ID> --label <LABEL> --rel <REL> \
                                   --key <KEY> --depth 4 --k 550 --budget 1000000
```

**Three things to know about reading the output.**

*A spread figure needs a magnitude to mean anything.* A 3.9× spread over path
counts of 2, 3 and 9 is noise. The same 3.9× over millions is evidence. The
script now refuses to apply the rule below a threshold; before it did not, and
it told me the predictor had failed when the real answer was that the traversal
was too cheap to need a budget.

*The probe is cheap warm, not cheap.* 15.7 ms warm, 2 174 ms cold on the same
graph. If you quote one number, quote the cold one.

*An estimate checked on a calibration node is not an estimate.* Held out, the
error was 13.6%; on a tuned node, 5.9%. Only the first may be quoted.

**What to know about the shape of the cost.** Path count and reachable node
count are different quantities, and only the first predicts cost. On one
fixture, between depth 4 and depth 5 the reachable set grew 1.3% — the graph was
already exhausted — while paths grew 23.6×, and that factor held across four
consecutive levels.

And there is no cliff. Depth 7 completed: 3.8 billion paths in 26 minutes.
Nothing crashed. **The limit is practical, not technical** — past a minute
nobody waits — which is more useful than a crash point, because it tells you
where to set a budget rather than where the edge is.

---

## 7. Know what the graph cannot tell you — 3 min

Two questions that look alike and are not:

**Is this record in the data?** A query answers it.

**Is this record represented as structure?** Only a structural query answers it,
and a model reading the text cannot.

The practical consequence: when an answer is missing, there are three states,
not two. *Absent* — not in the source at all. *Missed* — in the graph, retrieval
failed to reach it. *Unlinked* — the text exists but was never turned into
structure. Absent and unlinked look identical in the output. Only a structural
query separates them, and they need completely different fixes.

And before tuning anything, find the ceiling. On one register pair, records
belonging to *different people* were identical on every observable field. No
model separates them; no reviewer separates them. With given name, surname and
town alone the precision ceiling was **0.441**; adding a birth date raised it to
**0.979**. That is not a model result. It is a property of the data, and it is a
number you take to whoever owns it.

---

## 8. Write down what you did not check — 5 min

Before handing anything over:

- Which database, which version, which configuration — verified at the process
- What you measured, and on how many samples
- Which properties were incomplete, in which of the three ways, and by how much
- Which findings you could not classify from the data alone
- What you did not assess: cluster behaviour, load, real production data

**A report without that last block implies coverage it does not have.**

And keep two lists apart. **Findings** are things the data shows. **Questions
for the data owner** are things the data cannot decide — what a super-node
represents, whether a column means capital or votes, whether a group of
companies is expected to be isolated. Putting a question in the findings list is
how you get the one mistake that costs more than a miss: calling something a
defect in front of a client and having to walk it back.

---

## The checklist

```
[ ] CALL db.info() + dbms.components() — am I where I think I am
[ ] every path/id/key derived, not retyped
[ ] graph_profile.py — counts, constraints, degree tail, components,
    completeness in three states, value shape
[ ] real <LABEL> <REL> <KEY> read out of the profile before any command
[ ] distinct counts, value lists, length classes for every property
[ ] constraint-conflict test run (drop the range index first)
[ ] counts that must agree, compared
[ ] numeric relationship properties: with_value + missing == total
[ ] sums grouped by endpoint, checked against a domain limit
[ ] self-loops and short cycles separated from each other
[ ] traversal_budget.py --calibrate — k for THIS graph
[ ] any quoted estimate is held out of the calibration set
[ ] max degree and p99 known, not the mean
[ ] ceiling established — what do the fields permit at best
[ ] findings and questions-for-the-owner kept in separate lists
[ ] "not assessed" block written
```

---

## Where this came from

Every rule above replaced a mistake. The ones added in the most recent pass came
from running this runbook against a graph I had not seen, in one hour, and
recording what it missed:

- The examples were pasted verbatim and failed — hence placeholders (§ intro)
- Components sat in "analysis" and would have been skipped — moved into the
  profile (§2)
- Four findings were invisible to the node profile — hence value shape (§3)
- Two more needed an aggregate — hence §4
- The constraint trick failed for the wrong reason — hence the `DROP INDEX`
  (§3)
- The budget script reported a zero frontier as a failure, applied a spread
  rule below the magnitude where it means anything, and quoted a warm cache
  time as a property of the method — all three fixed, and all three are in §6
  because reading the output wrongly is as expensive as not running it
