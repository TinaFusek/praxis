# Before you publish a number

Every item on this list is a mistake I made and had to correct, in most cases
after the number was already written down. Two of them became public retractions.

The pattern worth noticing: **an error that makes you look worse gets found
immediately. An error that makes you look better does not.** So the checks are
weighted toward the second kind.

---

## 1. Does any metric default to a passing value on missing input?

I audited my own scorer and found four silent fallbacks:

| Condition | Returned | Should return |
|---|---|---|
| golden path empty | F1 = 1.0 | None, excluded |
| `answerable` flag missing | assumed True | None, excluded |
| zero claims in the answer | grounded = 1.0 | None, excluded |
| zero evidence-carrying claims | penalty = 0.0 | None, excluded |

**All four inflated.** That is not coincidence — it is the reason they survived.

**The rule: missing input returns None and is excluded from the aggregate. It
never defaults to the passing value.**

And then the number that makes the exclusion visible:

```
n_scored + n_excluded == size of the question set
```

If those do not add up, something was dropped silently. Print all three.

---

## 2. Does the same input give the same output?

```bash
python scripts/determinism_check.py --runs 3 -- python scripts/pipeline.py
```

Two runs on identical data gave 5 474 and 5 476 clusters. The cause was
iteration over a set: Python randomises string hashes between processes, so
ties between equal-scoring items broke differently each time. The fix was one
`sorted()`.

In anything that assigns identities to people, a result that shifts between runs
is unusable regardless of accuracy.

---

## 3. Is the metric independent of the thing it should not measure?

The first version of a cluster impurity metric was `entities / n`. It is
size-dependent: a perfectly clean three-record cluster scores 0.333, not 0.

That flattened the whole table so completely that a real, monotone relationship
looked like noise. Corrected to `(entities − 1) / (n − 1)`, the same data showed
a clean signal across six bands.

**The finding did not change. The instrument did.**

Before trusting a metric, feed it a known-perfect case and a known-worst case.
If perfect does not score at the boundary, the metric is wrong, not the data.

---

## 4. Is the number computed on the set you tuned on?

Critic agreement went 77% → 92% → 85% across three prompt revisions, measured on
the same thirteen questions the prompts were tuned against. Those numbers are
optimistic and I say so every time I quote them.

Tune on one set, report on another. If there is only one set, say that the
numbers are optimistic — do not quietly present them as held out.

*(The 85% is worth keeping for a different reason: the regression is the finding.
A model-based critic cannot see the schema, so a structural gap is invisible to
it by construction. That is evidence the deterministic layer is not replaceable
by a better prompt.)*

---

## 5. Did the configuration reach the process?

I reported a heap-size result from runs that had silently all used the same
heap. The container appended its own JVM flags after mine and the JVM takes the
last one. Both sweeps ran at 2 GB.

```bash
docker exec "$container" ps -o args= | grep -o 'Xm[sx][0-9]*[gm]'
```

```cypher
SHOW SETTINGS YIELD name, value WHERE name = 'server.memory.heap.max_size';
```

**Verify at the process, never at the config file.** The same rule caught a wrong
import path and four wrong-database probes.

---

## 6. Were the benchmark inputs generated, or typed?

I reported a 0/9 failure that turned out to be a hand-transcribed config file
with a typo in it. The engine was fine. Re-running against files generated from
upstream gave 9/9.

**If an input can be generated from its source, generate it.** Anything typed by
hand is a finding waiting to happen, and it will be a finding about you.

---

## 7. Were failures counted, or dropped?

A run that times out is a result. A parse error is a result. A skipped record is
a result.

If the batch loop counts survivors, your denominator is wrong and your average is
flattering. Append failures to a file, count them, and put them in the report.

---

## 8. Are you reporting the metric that matters, or the friendly one?

At one threshold, pairwise precision was 0.293 and B-cubed precision 0.807. The
same result. Pairwise counts every pair inside a cluster, so one bad cluster of
39 contributes 741 pairs and dominates; B-cubed asks per record, so a bad cluster
only damages the records in it.

Neither is wrong. But pairwise is sensitive to exactly the failure that costs the
most, so swapping it for the friendlier number hides the thing you most need to
see.

**Report both, and say which one drove the decision.**

---

## 9. Is the objective the right one?

F1 weighs a false merge and a missed match equally. In a register they are not
equal at all: declaring two people to be one person attaches somebody's records
to a stranger — hard to detect, hard to undo, an incident rather than an
inconvenience.

F1 peaked at threshold 12. Moving to 20 cost 371 matches and removed 98 false
merges.

**That is a decision for whoever bears the cost, made explicitly and written
down — not something a maximisation should decide silently.**

---

## 10. Does the write-up describe hand work as shipped code?

If you ran it once by hand in a notebook, say so. "The system verifies X" and "I
checked X manually on this run" are different claims, and only one of them
survives someone else running it.

---

## 11. Is n stated, per layer?

Path fidelity on 5 questions and critic agreement on 13 are two numbers about
two different sets. They never merge into one figure.

State n next to every number, at the layer it was measured.

---

## The list

```
[ ] no metric defaults to passing on missing input
[ ] n_scored + n_excluded == question set size
[ ] determinism_check.py passes
[ ] metrics tested on known-perfect and known-worst cases
[ ] reported numbers are not from the tuning set (or said to be)
[ ] configuration verified at the process
[ ] benchmark inputs generated from source, not typed
[ ] failures counted and in the report
[ ] both metric families reported where they disagree
[ ] objective matches the real cost asymmetry
[ ] manual steps described as manual
[ ] n stated per layer
[ ] "not assessed" block present
```

---

## The habit underneath all of it

Write the prediction before the run.

I was wrong about the write cost of super-nodes, wrong twice about community
detection repairing over-merge, and right about a path-count estimate. Only
because each was written down first could any of them be reported honestly
rather than quietly revised afterwards.

A prediction written after the result is not a prediction.
