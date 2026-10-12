# Honest null

**Why the system said "I don't know", and whether it was telling the truth.**

A bare refusal is worthless to all three people it reaches. The user does not
know whether to rephrase. The data owner does not know whether to load more
sources. The engineer does not know whether to fix the index. So a null has to
leave the system as a **finding with a cause**, not as an apology.

This runbook also covers cross-language retrieval, which looks like a separate
subject and is not: a cross-language miss produces a null that claims the fact
does not exist, when it exists in the other language. That is the most common
way a system lies while sounding humble.

`<ENTITY>`, `<REL>`, `<LANG>` are placeholders throughout.

---

## 0. The principle this runbook exists for

> Confident claims get audited. Refusals do not.

Nobody opens a ticket because the assistant said "I don't know." They open one
when it says something wrong. So defects **accumulate inside the nulls**, where
no one is looking, and a system can degrade for months while its error rate on
answered questions stays flat.

A false honest null — "not in the sources" when it is in the sources — is worse
than a wrong answer for exactly this reason. It is a lie wearing the shape of
humility, and humility is not reviewed.

---

## 1. The eight states, and who catches each

A null is not one thing. Each row below has a different cause, a different fix,
and a different detector. The states in the output look nearly identical, which
is the whole problem.

| # | State | Is the fact in the source text? | In the graph? | Retrieved? | What the user should be told |
|---|---|---|---|---|---|
| 1 | `OUT_OF_SCOPE` | — | — | — | "Outside what I cover. I cover X." |
| 2 | `FALSE_PREMISE` | the thing does not exist | — | — | "There is no Article 141." |
| 3 | `ABSENT` | no | no | — | "Not in the loaded sources." |
| 4 | `UNSTRUCTURED` | yes, as raw text | no | as text only | "Present in the text, not modelled." |
| 5 | `UNLINKED` | yes, in pieces | both ends, no edge | partial | "I have A and B; nothing joins them." |
| 6 | `MISSED` | yes | yes | **no** | nothing — this is a bug, not a null |
| 7 | `LOW_CONFIDENCE` | yes | yes | yes, below threshold | "Best candidate X, score 0.41." |
| 8 | `LANGUAGE_MISMATCH` | yes, in `<LANG>` | yes, other surface form | no | nothing — this is a bug, not a null |

### Problem → fix → notes

| State | Fix belongs to | Notes |
|---|---|---|
| `OUT_OF_SCOPE` | scope declaration | Cheapest check, so it runs first. The reply must name what *is* covered, or the user cannot tell a scope limit from ignorance. |
| `FALSE_PREMISE` | adversarial critic | The only state where the correct answer is to **contradict the question**. A relevance threshold cannot do this: the question is lexically close to real articles, so retrieval happily returns neighbours and the model answers about the wrong thing. Needs a component that reasons about meaning. |
| `ABSENT` | data acquisition | Not an engineering defect. But it is only honestly reportable once `MISSED` has been measured and excluded — otherwise every retrieval bug reports itself as a coverage gap. |
| `UNSTRUCTURED` | extraction / modelling | The answer is in the corpus but never became nodes and edges. A critic reading the *text* sees the answer and says the system was wrong; a scorer reading the *graph* sees why. Only the graph-side scorer localises this. |
| `UNLINKED` | edges, entity resolution | Report it with coordinates: `UNLINKED: <ENTITY A> ↔ <ENTITY B>, no path at depth ≤ 3`. That is a finished bug report, not a refusal. |
| `MISSED` | retrieval — index, chunking, embeddings, query rewriting | Never surface as a null. Measurable **only** against a ground-truth set (§3). Without that set you have no idea what share of your nulls are this. |
| `LOW_CONFIDENCE` | threshold, calibration | The state most systems hide. Log the best candidate and its score every time. This log is what the abstention threshold is later calibrated on; without it the threshold is a guess that cannot be improved. |
| `LANGUAGE_MISMATCH` | cross-language entity resolution, reference normalisation | Masquerades as `ABSENT`. §5. |

**Correction recorded.** My first version of this taxonomy had three states —
absent, missed, unlinked — and no `FALSE_PREMISE`. It therefore had no state in
which the right answer is to tell the user their question is wrong, which is
the one case where a coverage-based system confidently answers about something
that does not exist. The four-state split that caught it came from VERITAS.

---

## 2. The detector sequence

A null must be *earned*. Run the checks in this order — cheapest first — and
emit the code of the first one that fires. Every check leaves a trace.

```
0.  in declared scope?                   no  -> OUT_OF_SCOPE
1.  entities resolve to nodes?
      no, in any language               -> go to 2
      no in query language,
         yes in another                 -> LANGUAGE_MISMATCH   (bug)
2.  does the premise hold?
      (adversarial critic: does the
       thing the question asserts
       actually exist in the corpus?)    no  -> FALSE_PREMISE
3.  entities resolve, path exists?       no  -> UNLINKED  (report both ends + depth)
4.  answer present in raw text
      but not in the graph?              yes -> UNSTRUCTURED
5.  candidates returned at all?          no  -> MISSED or ABSENT
                                                (§3 decides which — you cannot
                                                 tell them apart at runtime)
6.  candidates below threshold?          yes -> LOW_CONFIDENCE
                                                (emit best candidate + score)
```

Step 5 is the honest part of this runbook: **at answer time you cannot
distinguish `MISSED` from `ABSENT`.** Both produce zero candidates. Anyone who
claims their system reports coverage gaps accurately, without a ground-truth
set, is reporting their retrieval bugs as data problems.

The output format matters. Not:

> I don't know.

But:

> `UNLINKED` — I have *Regulation 2024/1689* and *provider obligation* as
> separate entities; no path of length ≤ 3 connects them.

---

## 3. What you cannot do without a ground-truth set

Build a set of questions whose answers are **guaranteed** to be in the corpus,
by generating them *from* the corpus rather than inventing them.

- A null on one of those questions is **always** a retrieval defect. That gives
  you a measured `MISSED` rate.
- Then, on real user questions:
  `nulls_total − measured_missed ≈ absent`

Without that set you see nulls and have no information about whether any of
them are honest. The rate can be anything from 0% to 100% and the system looks
identical from outside.

Generate the set per language (§6), or you measure one language and quote the
number for both.

---

## 4. The metric: risk–coverage, not accuracy

A null is an abstention, so accuracy is the wrong frame. Report three things:

- **coverage** — share of questions answered at all
- **selective accuracy** — accuracy *among the answered*
- **the curve between them** as the abstention threshold moves

A system answering 60% of questions at 95% accuracy can be far better than one
answering 100% at 70%. Which is better depends on the same quantity that decides
every threshold: **the cost of a wrong answer against the cost of a null.**

| Setting | Wrong answer costs | A null costs | Operating point |
|---|---|---|---|
| Compliance, legal, clinical | penalty, liability, a decision made on false grounds | one human lookup | abstain readily |
| Internal help desk | a wasted few minutes | the user gives up on the tool | answer readily |

That ratio is not the model's to choose. It belongs to whoever carries the cost
of being wrong. Ask for it in the same sentence you ask for the baseline.

### Two traps in the measurement

**Uncalibrated abstention.** The system abstains when uncertain — but if the
uncertainty signal is miscalibrated, it abstains on easy questions and answers
confidently on hard ones. Measure: *of the questions it declined, what share
would it have got right?* A high figure means the abstention **signal** is bad,
not the model. This is the figure that tells you whether increasing coverage is
free.

**A null counted as a non-event.** If nulls are excluded from the evaluation
denominator, every abstention improves the score. Then the cheapest way to a
good number is to abstain more, and the metric rewards a system that answers
nothing. Nulls go in the denominator; coverage is reported beside the accuracy.

---

## 5. Cross-language retrieval

Three architectures. Each fails differently.

| Approach | Cost | Where it breaks |
|---|---|---|
| Translate the query | one translation per query | Terms of art. A paraphrase of a legal term is not the term. Fix is a pinned glossary applied before and after MT, not a better translator. |
| Translate the corpus | once, expensive | Your evidence is now a translation, and the citation must point at the original. Requires chunk-level alignment between the two versions — a structure that can silently break, after which citations point at the wrong place and nobody notices. |
| Multilingual embeddings | no translation step | Language bias. Below. |

### Language bias — the thing to measure before trusting a shared vector space

Multilingual encoders tend to score documents **in the query's own language**
higher, independently of content. In a mixed corpus a Slovak query therefore
pulls Slovak documents, and if the authoritative text is English you
systematically retrieve the weaker source — while every relevance number looks
fine.

The size of the effect varies by model, so it is measured, not assumed:

> Take a parallel pair — identical content, two languages. Query in `<LANG>`.
> Which ranks higher? If the same-language one wins on semantically identical
> text, that is language bias, not relevance.

### Where this meets §1

User asks in Slovak. The fact exists only in English. The entity names did not
match across languages. Nothing retrieved. The system reports:

> "Not in the sources."

`ABSENT`. Falsely. It is `MISSED`, caused by language.

This is why check 1 in §2 has a language branch. If the entities resolve in
another language but not in the query language, the code is not `ABSENT` but:

> `LANGUAGE_MISMATCH: entities resolved in EN, query SK`

which names the defect precisely. Without that branch you ship a system that is
modest with speakers of one language and competent with speakers of another,
and the aggregate number hides it completely.

---

## 6. Slovak specifics, ordered by payoff over effort

**1. Numbers and references — do this first.** `čl. 6 ods. 2` against
`Article 6(2)`. In a legal corpus these are the highest-value retrieval keys and
the cheapest to normalise: a regex and a canonical form. A question that names
an article explicitly and still retrieves the wrong one is this defect, and it
is the one most worth catching early because the fix is an afternoon.

**2. Diacritics.** Users type without them. `umela inteligencia` must reach
`umelá inteligencia`. Keep a folded index **alongside** the accented one, never
instead of it — folding destroys distinctions you need elsewhere.

**3. Inflection.** Slovak declines heavily:
`nariadenie / nariadenia / nariadením` are three tokens to BM25 without a
lemmatiser. The silent failure is this: hybrid retrieval is the standard advice,
BM25 plus vectors — but when the lexical half is much weaker in Slovak, "hybrid"
quietly becomes "vectors only" for Slovak queries. The system now behaves
differently per language and nothing in the aggregate score says so.

**4. Entities across languages.** *Európska komisia* and *European Commission*
must be **the same node**. Otherwise the same fact sits in the graph twice,
unconnected — which is state 5, `UNLINKED`, arriving through a language. Language
is just another source of surface variation, so the ordinary entity-resolution
machinery applies: an alias table with a language tag per surface form.

---

## 7. The parallel evaluation set

The same questions in both languages, against the same ground truth. Then
measure two things, not one:

- **language gap** — accuracy in `<LANG A>` against `<LANG B>` on identical
  questions
- **retrieval parity** — whether the *same chunks* came back

The second is the one that gets skipped and it is the more informative. Answers
can agree while retrieval diverges, and then you have two systems that happened
to agree today.

Report both per language. A single blended figure for a bilingual system is an
average over two populations with different error rates, which is a number
nobody can act on.

---

## 8. A measured case — what a scalar abstention rate hid

From a bilingual GraphRAG pilot over one legal corpus. Three questions labelled
unanswerable, a hand-annotated gold set, four metrics. Small n on purpose: the
point is what the instrument did, not what the system scored.

### 8.1 The headline number and what was under it

The harness reported `honest_null_rate` as a single ratio. Across two runs it
read `0.0` and `0.3333333333333333`. Nothing in the system changed between
them; retrieval happened to return empty on one question.

| Question | false premise | retrieval empty | abstained | what it did |
|---|---|---|---|---|
| q009 — a court ruling that does not exist | yes | **true** | **true** | correctly declined |
| q008 — a requirement the Act never imposes | yes | false | false | answered from Art. 95 |
| q010 — a non-existent Article 141 | yes | false | false | answered from Art. 37 |

Abstention matched `retrieval_empty` on 3 of 3. So the reading is not *the
detector works a third of the time.* It is:

> **There may be no abstention detector at all — only `abstained = retrieval
> returned nothing`.**

That looked like a hypothesis needing an experiment — force retrieval to return
empty on a question that **is** answerable, and see whether the system abstains.
It did not need one. The scorer takes `abstained` from *either* the answer field
**or** an empty-retrieval signal, so an empty index is abstention **by
definition**. The 3-of-3 correlation was not evidence of a pattern; it was the
definition showing through.

Read the scoring code before designing an experiment against the scores. The
experiment would have cost an afternoon and told me what one function already
said.

The conflation is defensible operationally — the user sees no answer either way
— but it is fatal to the metric, because an abstention earned by a critic
catching a false premise scores identically to one caused by an empty index.
The system is credited with judgement it never exercised. The fix is not to
remove the `or`; it is to **record which branch fired**:

```
abstain_reason: "empty_retrieval" | "critic_flagged" | "explicit_refusal"
```

Then `true_abstain` splits into earned and accidental, and the headline 1/3
resolves into 0 of 2 on judgement and 1 of 1 on plumbing.

### 8.2 Resolution — the number should not have been published at all

Three labelled questions means `honest_null_rate` can only take the values
`0, 1/3, 2/3, 1`. Four states, 33 percentage points apart. The figure was
printed to sixteen decimal places on an instrument that cannot distinguish 0.30
from 0.36.

Before quoting a ratio, divide 1 by the denominator. That is the smallest
difference the measurement can see.

### 8.3 The one it got right was a different state

All three were labelled *false premise*. But the corpus was the Act's text, and
q009 asked about a **court ruling** — a category of document that was never in
the corpus at all. That is `OUT_OF_SCOPE` (state 1), not `FALSE_PREMISE`
(state 2), and the distinction explains the whole table:

- Out of scope → no lexical neighbours → retrieval empty → abstains for free
- False premise → plenty of lexical neighbours → retrieval full → answers
  confidently about the wrong article

Rescored by state: **1 of 1 on out-of-scope, 0 of 2 on false premise.** The
system handles the case that needs no judgement and fails the case that needs
all of it. "1/3" says none of that.

The gold schema carries a single boolean, `answerable`. Out-of-scope and
coverage-gap questions both map to `answerable: false`, and the annotation
protocol says so explicitly. One flag over two populations with different
difficulty, which is why the rate above mixes them.

The information is not missing — it is recorded one level down and thrown away
on the way up. Each claim already carries `must_be_grounded`, documented as
*"if the fact is true-but-not-in-graph, set false"*. That **is** the coverage-gap
distinction, at claim granularity. The question-level flag simply does not
inherit it.

So part of `null_type` costs no new annotation. Only part:

| unanswerable because | at claim level | question-level type | derivable? |
|---|---|---|---|
| true, in the source, not modelled | `must_be_grounded: false` | `UNSTRUCTURED` | **yes** |
| the thing does not exist | nothing to ground | `FALSE_PREMISE` | no |
| that category of document is not in the corpus | nothing to ground | `OUT_OF_SCOPE` | no |

The last two are indistinguishable from the claims alone — both have nothing to
ground — so they need one annotated field. Derive the first, annotate the rest,
and mark the undecidable ones `UNSPECIFIED` rather than guessing. A rate
computed over a mostly-untyped slice moves when the mix of question types moves,
with nothing in the system changing.

### 8.4 A control that holds, and a near-identical trace that is not it

The question asking about Article 50 **in the non-corpus language** retrieved
Articles 25, 26, 69 and 86 — neighbours, never the pinned entity. The **same
question in the corpus language** retrieved Article 50 correctly. One variable
changed, everything else held: that is a proper control, and it puts the failure
at `LANGUAGE_MISMATCH` (state 8), not `MISSED` (state 6). The node existed and
was reachable; only the query language decided whether it was found. The fix is
§6.1 — pin the article reference before retrieval runs.

**The near-miss is the more useful lesson.** A different question — about
general-purpose model transparency, whose gold path is Article 53 — also
visited `art:50`. Read as a trace, it looks like the same successful Article 50
resolution. It is the opposite: entering Article 50 there is the *failure*, and
its path fidelity is 0.00.

Two traces, the same node in `visited_nodes`, one correct and one wrong. Nothing
in either trace distinguishes them.

> A visited-node list is not evidence of anything until it is read **against the
> golden for that question**. Without the golden, a trace will support whichever
> conclusion you brought to it.

A first review of this pilot cited the wrong one of the two as the control, and
so offered a failed trace as proof that the corpus language resolves articles
correctly. The conclusion survived — a real control existed — but it survived by
luck, not by the evidence given for it.

### 8.5 Two metrics that return their best value for doing nothing

`provenance_coverage_grounded` read `1.0` in every run because the claim list
was empty: zero claims, so every claim grounded. In the scorer:

```python
if not claims:
    return {"linked": 1.0, "grounded": 1.0, "n_claims": 0, "ungrounded_claims": []}
```

That is not an uninformative result, it is a broken one. `0/0` must be undefined
and excluded from the mean, never `1.0`. Note that `n_claims: 0` is returned
right beside it — the information needed to exclude the row is already there and
the guard still returns the best possible score.

An empty-input guard is where this defect lives in general. It is written to
avoid a crash, it is never revisited, and it quietly hands out full marks.

Together with the abstention ratio — improvable by declining more often — two
of four metrics scored a system highest for producing nothing. That is a
pattern, not two bugs:

> Every metric must state what it returns on an empty denominator, and `1.0` is
> never the answer.

### 8.5b The scorer was right; the aggregation threw it away

Worth separating, because the fix is much smaller than it first appeared.

`score_honest_null` does **not** return a ratio. It returns one of four
categories per question — `true_abstain`, `false_answer`, `true_answer`,
`false_abstain` — which is a 2×2 of *abstained / answered* against
*unanswerable / answerable*. That is a confusion matrix, and selective accuracy
and coverage both fall straight out of it.

The scalar `honest_null_rate` was produced by the harness collapsing that matrix
into one cell count. So the recommendation is not "move to risk–coverage", which
implies rebuilding. It is **stop collapsing**: report the 2×2 the scorer already
emits.

Before proposing a new instrument, check whether the existing one is being read
through a narrower aperture than it emits.

### 8.6 One annotator

The gold set carried `annotator: "M"` throughout. One person's reading of "the
correct path" is a hypothesis about correctness, not a measurement of it, and
there is no way to tell which gold paths another expert would have drawn
differently.

For internal instrumentation that is acceptable and should be stated. For
anything published, double-annotate a sample — fifty items is enough to put a
number on agreement — because it is the first thing a reviewer asks and the
only answer without it is "I don't know."

Said to this project's author, this was advice she did not need: the repository
already ships agreement tooling, the test suite asserts bounds on the kappa, and
the README lists the study itself as planned rather than done. The gap was real
and already declared.

Which is its own rule. **Read the repository before advising on the project.**
A finding drawn from one run's output describes that run; the project may have
moved past it, and arriving with a correction that was shipped two versions ago
costs the credibility of the corrections that are still live.

---

## 9. The same states outside retrieval

The title of this runbook names one domain. The content is about any system that
can fail to produce an answer, and the states transfer without modification.

### 9.1 A network, device inventory, or topology

| State | The same thing in a network |
|---|---|
| `OUT_OF_SCOPE` | the question is about a device class the inventory does not hold |
| `FALSE_PREMISE` | the device ID does not exist — and the tool returns something anyway |
| `ABSENT` | that link genuinely is not in the topology data |
| `UNSTRUCTURED` | it is in the as-built documentation, never in the topology database |
| `UNLINKED` | both segments are present, nothing joins them, so no path is found |
| `MISSED` | the link is in the data; the path computation did not consider it |
| `LOW_CONFIDENCE` | a candidate route exists below threshold and is never shown |
| `LANGUAGE_MISMATCH` | one device under two names in two systems |

The last row is **more** common here than across human languages. Inventory,
monitoring and billing each name the same element differently, nobody reconciles
them, and the result is a fact that exists twice and connects to neither half.
It is `UNLINKED` arriving through naming rather than through language, and it is
fixed the same way: one canonical identity, aliases attached to it.

### 9.2 §0, in a network

> **No alarm and no data look identical on a dashboard.**

A dead probe draws a flat line. A healthy link draws a flat line. And nobody
raises a ticket about the *absence* of an alarm — which is §0 word for word,
with monitoring in place of refusal.

So a monitoring view has to distinguish three states, not two:

```
  alarming        something is wrong
  clear           measured, and fine
  no data         not measured — and this is NOT the same as clear
```

Most dashboards have two. The third is where the outage hides.

### 9.3 §8.5, in a network

An availability report showing **100% for a device that sent nothing all month**
is `0/0 = 1.0`, in production, on a page someone bills against.

It is the same guard written for the same reason — avoid dividing by zero, get
a number out, never revisit it — and it is common enough that it is worth
checking on the first report you are handed, before you check anything else.

### 9.4 Where this does not transfer

This is about systems permitted to decline. It says nothing useful about
regression, ranking, or generation without an abstain option. Pushed there, it
stops being a method and becomes an attitude.

---

## 10. Checklist

- [ ] Every null leaves the system with a state code from §1
- [ ] The detector sequence runs cheapest-first and the trace is kept
- [ ] `LOW_CONFIDENCE` nulls log the best candidate and its score
- [ ] A ground-truth set exists, generated from the corpus, **per language**
- [ ] The `MISSED` rate is measured; `ABSENT` is only ever reported as a residual
- [ ] Coverage and selective accuracy are reported together, never accuracy alone
- [ ] Nulls are in the denominator
- [ ] Wasted-abstention rate measured at least once
- [ ] The cost ratio behind the abstention threshold came from the data owner, in writing
- [ ] Language bias tested on a parallel pair
- [ ] Article/section references normalised to a canonical form
- [ ] Folded index alongside the accented one
- [ ] Lexical retrieval lemmatised, or hybrid reported as vectors-only for Slovak
- [ ] Cross-language aliases resolve to one node
- [ ] Language gap and retrieval parity reported separately per language
- [ ] Every ratio checked against 1/n — the smallest difference it can resolve
- [ ] Every metric states what it returns on an empty denominator
- [ ] Abstention tested against forced-empty retrieval on an answerable question
- [ ] Gold set double-annotated on a sample, or single-annotator stated as a limit
- [ ] "No data" is a third state on every dashboard, never folded into "clear"

---

## Related

- [`before-you-publish-a-number.md`](before-you-publish-a-number.md) — the
  coverage figure in §4 is exactly the kind of number that needs a baseline
  beside it
- [VERITAS](https://github.com/TinaFusek/veritas) — scores the reasoning path
  rather than the answer text; the false-premise / coverage-gap split in §1 is
  its distinction
