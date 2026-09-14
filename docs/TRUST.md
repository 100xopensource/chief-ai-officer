# Why you can trust the numbers

[← back to the README](../README.md)

Most usage dashboards will happily show you a big confident number. The value
here is in what this tool *refuses* to do.

**Every count is a floor, not a total.** It can read chat conversations. It
cannot see every other Claude product, and some tool output is truncated before
it ever reaches the tool. So it says *"at least this many, and the true number
cannot be lower"* — never a total it can't stand behind.

**Any group smaller than five people is reported as "fewer than five."** In a
firm of a hundred, *"3 people in the commercial team last week"* is close enough
to naming them that any reader can finish the sentence.

**Nothing brand new gets a growth rate.** A tool connection that first appeared
last month shows infinite growth this month. That artefact has been published as
a real finding before, at real cost. Everything carries the date it first
appeared, and anything too new to compare against is labelled as such.

**Zero findings is a valid result.** If nothing crossed a threshold, the report
says so plainly. Nothing is padded to fill a page.

**Every finding names an owner and one thing they can actually do.** A finding
about something nobody can change isn't a finding.

## The publication gates

No report is allowed out until it passes every gate, run against the finished
file rather than against a promise about what's in it. A report is blocked if it
contains an email address, a filename, a loose run of digits that could be a
leaked value, a group of three described as "3 people", internal jargon, a
regulatory claim the tool can't support, arithmetic that doesn't add up, a
caveat that got quietly dropped during a rewrite, a column that is zero in every
single row, or a record missing something the page needs to display.

**Every one of those gates exists because that exact mistake once reached a real
reader.**

## What the gates do *not* check — and why the wording changed

They check the document's **form**. Not one of them checks whether a number was
computed from a field that exists.

That distinction cost this project two confidently wrong reports. The metric code
was written against the demo lake, whose shape didn't match what the live API
returns, so several metrics read fields that were simply absent. A missing field
doesn't crash — it sums to zero. And a zero passes every gate here: the document
renders, nothing leaks, and the reconciliation block ties out perfectly, because
zero equals zero. One of those zeros rendered as a stat card reading **"0% of
connector calls failed"** over a data source that publishes no failure data at
all.

The run said `12 gates passed`, and everybody — including the people who built it
— read that as *the numbers are right*. So it now says:

```
13 integrity checks passed · numbers not independently verified
```

Two things were added underneath that wording:

- **A shape check at read time.** Each stage declares the fields it consumes, and
  they're checked against what's actually in your lake. A missing field is now a
  stated skip with a named reason — never a silent zero. `caio check` reports it.
- **A zero-variance gate.** A column that is zero in every row, or a total of zero
  reconciled against parts that are all zero, is refused. A column of zeros was
  never measured as zero; it wasn't measured.

The rule underneath both: **an absence of measurement is not a measurement of
zero**, and a reader is entitled to have the two told apart. A blank where a
failure rate used to be reads as good news.

## Running the gates yourself

To run them against a report by hand:

```bash
python3 -m pipeline.render.validate _reports/exposure-report-2026-07-12.html \
        --json-sidecar _reports/exposure-report-2026-07-12.json
```

That prints `13 integrity checks passed`. Leave off the `--json-sidecar` and
you'll see one fewer — the last gate checks the report against its own data file,
so it needs both files to run. Both are written side by side in `_reports/`.
