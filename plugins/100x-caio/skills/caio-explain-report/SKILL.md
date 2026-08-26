---
name: caio-explain-report
description: Explain what a 100x CAIO report says and where a number came from, in plain language. Use when someone asks what a figure means, challenges a number, asks "how did you get that", "why is this a floor", "what does candidate mean", asks to walk an executive through a report, or wants a report summarised for someone who will not open it.
---

# Explaining a report

## Who you are talking to

Someone with three minutes who did not build this. They want to know what to do
and whether to believe it. Every piece of internal vocabulary you use is a
sentence they skip.

Say **matched** not *lead*. Say **the week of 6 July** not *week 28*. Say
**a data connection** not *a connector integration*. Say **the export we are
allowed to read** not *the compliance feed*.

## Where any number comes from

Every report renders itself from a data record embedded inside the file, and
that record is written by a stage you can run yourself. So "where did this come
from" always has a concrete answer:

```bash
python3 -m pipeline.stages.x2_metrics_waste --data-dir data     # the cost numbers
python3 -m pipeline.stages.x2_metrics_value --data-dir data     # the usage numbers
python3 -m pipeline.stages.x2_metrics_exposure --data-dir data  # the exposure numbers
python3 -m pipeline.stages.x5_lock --data-dir data --all        # findings and movement
```

If a number is being challenged, run the stage and read the value out. Do not
reason about what it probably is.

## The four things people always ask

**"Is this the real number?"**
For exposure, no — and the report says so. It is a count of things that matched
a pattern. Nobody has read them. Somewhere between the low and high figure would
be expected to survive being read. For cost, yes: cost is complete for every
product because it all bills through one place.

**"Why does it say 'at least'?"**
Because the report can only see part of the picture and refuses to guess about
the rest. It reads chat conversations; other products are not in that export.
Some tool results are cut off at the source and nothing past the cut can be
examined. So every count is a floor: at least this, never less.

**"Why does it say 'fewer than five people'?"**
Because in a company this size, an exact count of three, filtered by a team and
a week, is close enough to a name. Under five is always words.

**"This number changed from last week — did you get it wrong?"**
Probably not. Recent weeks keep settling for about a month as late data lands,
and the report says so. Check whether the finding is marked as getting worse or
getting better; that comparison is against the same measure in the ledger, not
against a re-typed figure.

## Summarising for someone who will not open it

Give them, in this order:

1. The one sentence from the bottom line.
2. The single action ranked first, and who owns it.
3. One sentence on what the report cannot see.

Stop there. If they want more they will open it — that is what the file is for.

## What not to do when explaining

- Do not restate a candidate count as a finding count to make the answer
  simpler. The simpler answer is wrong.
- Do not drop the caveat because it reads as hedging. It is not hedging; it is
  the reason the number is true.
- Do not add a number the report does not contain. If someone wants exposure
  priced in money, the honest answer is that it needs a loaded hourly rate the
  pipeline does not have.
- Do not name individuals from a shareable edition. If someone needs names,
  that is a named edition and a decision, not a favour.
