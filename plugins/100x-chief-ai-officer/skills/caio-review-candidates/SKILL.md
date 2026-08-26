---
name: caio-review-candidates
description: Read the sensitive-data candidates the scan produced and judge each one real or a false alarm. Use when asked to confirm, verify, triage or review exposure findings, when someone asks "are these real", when turning candidates into confirmed findings, or when they want the Exposure Report to move from candidates to something publishable.
---

# Turning candidates into findings

## The distinction this whole skill exists to protect

A pattern matched some text. That is a **candidate**. Nobody has looked at it.

A person or an independent pass has read it in place and judged it. That is a
**finding**.

In earlier audits about **44%** of candidates survived being read, and some
kinds survived **none at all**. A candidate count published as a result
overstates reality by more than double. This has happened. It is the single most
expensive mistake this pipeline is built to prevent.

## Running the reading passes

```bash
caio judge  --data-dir data --judge worksheet    # writes a file for you to fill in
caio judge  --data-dir data --judge worksheet    # run again to read it back
caio verify --data-dir data --judge worksheet    # the independent second read
```

`--judge anthropic` has a model do the reading instead; `--judge demo` is an
offline stand-in for trying the tools out, and any report built with it says on
its own face that nothing was really read.

Read in bounded batches with `--limit`. Passages are queued strongest-first, so
a partial pass covers the ones most likely to matter, and the coverage figure is
recorded and published — which is why every count stays a floor.

## Where the candidates are

```
<data-dir>/_reports/<run-id>/candidates.jsonl     what the scan matched
<data-dir>/_reports/<run-id>/verdicts.jsonl       what the first read concluded
<data-dir>/_reports/<run-id>/verifications.jsonl  what the second read concluded
```

Each row records where the match was, which family matched, that family's
historical precision, and a **fingerprint of the matched value — never the value
itself**. To judge a candidate you open the conversation it points at.

## How to judge one

For each candidate, answer three questions in this order:

1. **Is the matched thing what the pattern thought it was?** A great many
   matches are placeholders (`REDACTED`, `your-key-here`, `xxx`), test data,
   documentation examples, or a number that happens to have the right shape.
2. **Does it matter in this context?** A credential in a public code sample is
   not a credential in a support ticket. A person's name in a public press
   release is not the same name in an HR case.
3. **Is there an owner and an action inside the organisation's control?** If
   nobody can change anything about it, it is not a finding. Kill it.

Write the verdict into the worksheet's `>>> VERDICT` line. Your reason goes into
the permanent record, so it must quote no values, no names, no email addresses
and no filenames — describe the shape and the context instead. The tool scrubs
these anyway, but it should not have to.

The worksheet is the only file in this project that contains raw conversation
text. It is git-ignored. Delete it once the verdicts have been read back.

## Two rules about how you read

**Never test a credential.** Not once, not "just to see if it is live". Judge
liveness from context — where it appeared, when, what it is attached to. Testing
a credential is an unauthorised authentication attempt against somebody's
system, and it is also how a dead key becomes an incident.

**Verification is a separate pass.** The pass that confirms a finding must not
see the reasoning of the pass that classified it. A second read that has already
been told the answer is not a check; it is agreement. `caio verify` enforces
this — it re-reads the passage from the raw store and is never handed the first
verdict.

Four outcomes, and only one of them is a finding:

| Outcome | Meaning |
|---|---|
| confirmed | both reads said real. Published. |
| disputed | the two reads disagreed. Published as neither — settle it by reading it yourself. |
| unverified | called real once, not yet re-read. Still a candidate. |
| cleared | the first read called it a false alarm. |

## What to report when you are part-way through

The honest shape is always: "we read the strongest-looking matches first;
of what we read, X% held up; every count is a floor."

Never present a partial read as a total. Never round a floor up into a headline.
If someone asks for a single number and there isn't one, give the range and say
what would narrow it.

## Zero is a legal answer

If the candidates you read were all false alarms, the finding is that there were
none. Write that. A report that always finds something is a report that finds
something whether or not it is there.
