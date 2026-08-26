---
name: caio-weekly-reports
description: Produce the weekly Waste Ledger, Value X-Ray and Exposure Report from a 100x Chief AI Officer lake. Use when someone asks to run the weekly reports, refresh them, build one report in particular, asks "what did Claude cost last week", "who is using it", "what sensitive data went through it", or asks for the numbers behind a report that already exists.
---

# Producing the weekly reports

## The whole thing, one command

```bash
caio all --data-dir data --out-dir _reports
```

That checks what the lake can answer, sweeps conversation content, computes each
report's numbers, compares them against previous runs, renders three HTML files,
and puts each through the publication gates. A report that fails a gate is not
shared; the run says which gate and why.

Add `--deliver` to also package each one with a cover note and checksums.

Add `--read` to also run the two reading passes, so the Exposure Report carries
confirmed findings rather than candidates:

```bash
caio all --data-dir data --read --judge worksheet
```

Without `--read`, the exposure side reports what matched a pattern and says
plainly that nothing has been read. That is honest but weaker; the reading is
what turns a match into a finding.

## One report at a time

```bash
caio report exposure --data-dir data --out-dir _reports
```

## Before you report anything, answer the reader's first question

Every one of these reports is read by somebody with three minutes. Lead with
the sentence they need, not with the pipeline:

- **Waste Ledger** → what it cost, how that moved, and the largest thing that
  bought nothing.
- **Value X-Ray** → how many people genuinely use it, and the biggest thing
  getting in their way.
- **Exposure Report** → what was matched, how it arrived, and how much of the
  picture is missing.

## The rules that govern every number you repeat

These are not style preferences. Each was paid for by a wrong number reaching a
reader.

1. **A pattern match is a candidate, never a finding.** Roughly 44% of
   candidates survive being read; some kinds survive none. Never state a
   candidate count as a result. If nothing has been read, the honest answer is
   the range plus "nothing has been read yet". If the reading passes have run,
   the answer is the confirmed count — and confirmed means two independent
   reads agreed, not one.

   Check who did the reading before you quote a confirmed number. A run judged
   by the offline stand-in has not been read by anyone, and the report says so.

2. **Publish floors, not estimates.** Say so in the reader's words: "at least
   this many, and the true number cannot be smaller."

3. **Check when something was born before trending across it.** A data
   connection that first appeared last month shows infinite growth this month.
   The pipeline marks anything too new to compare; respect the mark.

4. **Counts and ranks, names only on request.** Any group under five people is
   "fewer than five".

5. **Zero findings is a result.** If a report has none, say it has none. Do not
   go looking for something to fill the page.

6. **Every finding needs an owner and an action they control.** A finding about
   something nobody can change is not a finding.

## Reading the run's output

```
built  exposure-report-2026-07-12.html  ·  6 finding(s)  ·  12 gates passed
skip   Value X-Ray: cannot be produced, analytics_users is not in this lake
FAIL   waste: privacy: 1 email address(es) in a shareable report
```

- `built` — it exists and it passed. Safe to share.
- `skip` — data is missing. Say what is missing; do not produce a thinner
  report and present it as the real one.
- `FAIL` — the file was written but must not leave. Fix the cause.

## Which week does "last week" mean

The most recently completed Monday to Sunday, resolved from the newest day of
data in the lake rather than from today's date. So a lake pulled a fortnight ago
reports the last complete week it can actually see, and says which dates those
are, rather than silently reporting an empty week.

Never compare the week in progress against completed weeks. A part-week always
reads as a collapse, and has been published as one.

## Individual stages

Useful when something looks wrong and you want to see where it came from.

```bash
caio check --data-dir data                                         # what can be answered
python3 -m pipeline.stages.x2_scan --data-dir data                 # sweep content
python3 -m pipeline.stages.x2_metrics_waste --data-dir data        # the raw numbers
python3 -m pipeline.stages.x3_classify --data-dir data             # read and judge
python3 -m pipeline.stages.x4_verify --data-dir data               # second read
python3 -m pipeline.stages.x5_lock --data-dir data --all           # findings + movement
python3 -m pipeline.render.compose --data-dir data --report waste  # the render data
```

Use `--dry-run` on a report build to avoid advancing the findings ledger. A
report composed twice for a look should not count as two weeks.
