# First week on a client engagement

The technical runbooks assume you already have access, a task, and a sense of
what success looks like. In week one you have none of those, and the errors
available are larger than any query mistake.

Written for a consultant joining a project, not for a full-time hire.

---

## Day 1 — find out what you have been put in the middle of

### Four questions, asked of different people

Ask your lead, then ask someone on the client side, and compare. Where the two
answers differ is where the project's real risk is.

1. **What is the customer paying for?** Not the task — the outcome they signed
   for. Everything you do is judged against this, whether or not anyone says so.

2. **Who decides it is done?** A name, not a role. If nobody can name the
   person, the deliverable has no acceptance criterion and you will find that
   out at the worst moment.

3. **What has already been tried?** There is always something. Knowing it stops
   you from proposing it again in week two, which is the fastest way to lose a
   room.

4. **What may I not touch?** Systems, data, people, times of day. Ask
   explicitly; nobody volunteers it.

### Access, in writing

Write down exactly what you were given, and what you were not:

- Which systems, which environments — production or a copy
- Read or write
- Whose credentials, and whether they are yours or shared
- Whether client data may leave their environment at all

That last one is not paperwork. It decides whether you may pull a file to your
own machine, run it through a local model, or paste a sample into a chat window.
**Ask before you need to, not in the moment you want to.**

If any answer is "just use the admin account for now", say plainly that you
would rather have a scoped account, and why: a shared admin account makes it
impossible to prove what you did or did not do. That is protection for you, and
it is the kind of thing that gets noticed in a good way.

---

## Day 2 — measure before you opine

Run `new-graph.md`, or its equivalent for whatever store the data is in. Produce
a profile, not an opinion.

**Report what is incomplete before you analyse anything.** A property that is
60% populated breaks every rule built on it, silently. Said on day two it is a
finding; said in week four it is an excuse.

Keep two lists from the very first day, and never merge them:

**Findings** — what the data shows.
**Questions for the owner** — what the data cannot decide.

Putting a question in the findings list is the one mistake that costs more than
missing something, because it is said out loud in front of the customer and has
to be walked back.

---

## Day 3 — write down your own baseline

Before you change anything, record:

- The number the project is supposed to move, and its value today
- How long the slow thing currently takes
- What currently fails, and how often

You will be asked in month three what improved. Without this, the honest answer
is "I do not know", and the dishonest answer is easy and tempting.

---

## Every day — the two-minute log

At the end of the day, in a file nobody else reads:

```
what I did
what I found
what I could not do, and what blocked it
what I promised, and to whom
```

That last line is the one that saves you. "You said Thursday" happens to
everyone, and the only defence is a record — not to argue, but to know whether
they are right.

The same file feeds the time reporting that the job description asks for, which
is otherwise reconstructed from memory on Friday afternoon and is wrong.

---

## When you are asked how long something will take

You will be asked in week one, before you know anything.

**Give a range, never a point.** "Two to five days, and I will narrow it after
the first day." A point estimate given at the moment of least information
becomes a commitment, and nobody remembers the conditions attached.

**Say what the range depends on.** "Two days if the data is in one system, five
if I have to reconcile two." Now the customer owns half the uncertainty, which
is correct, because they do.

**Record the estimate and the actual.** After three months you have your own
calibration data instead of a feeling. This is the same procedure as calibrating
a traversal multiplier: the number is a property of your environment, not of
you, and it cannot be copied from anyone else.

---

## When you do not know

Say so, and then say what you would do to find out. Those are two different
sentences and the second one is the one that matters:

> "I do not know. I would check X and Y, and I would know by tomorrow morning."

A consultant who says "I do not know" and stops is expensive. One who says it
and names the next step is the reason people hire consultants.

Do not guess to fill a silence in a meeting. A guess said out loud becomes a
commitment in somebody's notes within the hour.

---

## When you find something bad

Three rules, in order:

**Tell your own lead before you tell the customer.** Not to hide it — so that
the customer hears one version, and so that whoever owns the relationship is not
surprised in front of their own management.

**Separate the finding from the blame.** "The IČO field is populated on 60% of
rows, so any rule keyed on it silently excludes two in five companies" is a
finding. "Whoever built this did not put a constraint on it" is the same fact,
made unusable.

**Say what it costs and what it would take to fix.** A finding without either is
a complaint.

---

## What to write down for yourself

Keep a running file, private, for the first three months:

- Every acronym and internal name you hear, with what it means
- Who knows what — the person you ask about billing, about the database, about
  why a decision was made
- Every process you had to ask about twice
- Every time your estimate was wrong, and by how much

The first list makes you fluent in a month. The second makes you effective. The
fourth makes you accurate, and accuracy about your own speed is rarer and more
valuable than speed.

---

## The checklist

```
[ ] what the customer is paying for — in their words
[ ] who accepts the deliverable — a name
[ ] what has already been tried
[ ] what I may not touch
[ ] access recorded: systems, environments, read/write, whose credentials
[ ] whether client data may leave their environment — asked explicitly
[ ] profile produced before any opinion offered
[ ] incomplete data reported before analysis, not after
[ ] findings and questions kept in separate lists from day one
[ ] baseline recorded before any change
[ ] daily log running
[ ] every estimate given as a range, with what it depends on
[ ] estimates and actuals both recorded
```
