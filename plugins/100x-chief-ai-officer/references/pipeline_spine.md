# The pipeline spine

Six stages, one lake, one direction. Each runs on its own; `pipeline.cli` runs
them in order because ordering is the tool's job, not the user's.

```
x0  gap check    what can be answered, what is missing, which dates
x1  pull         fetch into the lake; content lands raw first
x2  metrics      deterministic computation; no model reads anything
x3  classify     a model reads flagged content and labels it
x4  verify       an independent pass tries to break x3's conclusions
x5  lock         a findings ledger, then render and validate
```

## x0 — gap check

`stages/x0_gapcheck.py`

Answers three questions: which dates does this run cover, which reports can be
produced from the data present, and what is stale or absent.

The window is the most recently **completed** Monday to Sunday, resolved from
the newest day of data rather than from today's date. A part-week compared
against full weeks always reads as a collapse, and has been published as one.

Standard library only. It has to run when the lake is empty and pandas is not
installed — the two moments somebody is most likely to run it.

## x1 — pull

`fetch/analytics.py`, `fetch/directory.py`, `fetch/compliance.py`

The only modules that touch a network. Conversation content requires a consent
record naming an accountable person before anything is pulled; the record is
per-lake and is checked even on a scheduled run.

Rate-limited well under the organisation-shared ceiling, with mid-run
checkpointing, because a pull that dies at 80% and has to start again is a pull
nobody runs twice.

## x2 — metrics

`stages/x2_metrics_waste.py`, `x2_metrics_value.py`, `x2_metrics_exposure.py`,
and `stages/x2_scan.py` for the content sweep.

Arithmetic over the lake. No model is consulted, so the same lake gives the same
answer and an argument about a number is settled by reading the code.

The scan writes a **fingerprint** of each matched value, never the value. A scan
that stored what it matched would be a second copy of the thing it is auditing.

## x3 — classify

`stages/x3_classify.py`

Reads the passage each candidate points at and judges it real, a false alarm,
or unsure. The passage — not the pattern match — is the unit: one passage often
trips several patterns, and a reader reads it once.

Reads strongest-first and records the fraction covered, which is what makes
every downstream count a floor. Fetches text from the raw store at judgement
time and never persists it; what is stored is a verdict, a severity and a
reason scrubbed of values, names and filenames.

Three judges, chosen with `--judge`:

| Judge | What it is |
|---|---|
| `demo` | offline and deterministic, for CI, tests and the synthetic lake. Not a reading, stamped as such in every verdict, and named on the report. |
| `worksheet` | writes a review file, a person fills it in, run again to read it back. The only artifact in this project that carries raw conversation text — git-ignored, and the file says so at the top. |
| `anthropic` | a model reads each passage. One request per passage, structured output, needs a key. |

**Never test a credential.** Liveness is judged from context. Testing one is an
unauthorised authentication attempt against somebody's system, and it is also
how a dead key becomes an incident. Every judge's brief says so.

## x4 — verify

`stages/x4_verify.py`

Re-reads whatever x3 called real, from the raw store, with no access to x3's
verdict, reason or severity. A check that has been told the answer is not a
check; it is agreement.

| Outcome | Meaning |
|---|---|
| `confirmed` | both passes said real. The only thing published as a finding. |
| `disputed` | x3 said real, x4 did not. Published as neither — a disagreement is a fact, not a tie to break silently. |
| `unverified` | x3 said real, x4 has not looked. Stays a candidate. |
| `cleared` | x3 said false alarm. Not re-read. |

Verifies everything x3 claimed by default. Sampling here would mean publishing
findings nobody checked; if the budget is tight, the lever is reading fewer
passages in x3.

## x5 — lock and render

`stages/x5_lock.py`, then `render/compose.py`, `render/build.py`,
`render/validate.py`, `render/deliver.py`.

The ledger gives each finding a key derived from what it is about — never from
its position in a list — and compares against previous runs to say whether it is
new, still here, getting worse, getting better, or cleared.

Rendering is a data swap and nothing else. `build.py` refuses to write unless
the markup outside the data block is byte-identical to the template and the
block round-trips. `validate.py` runs the publication gates on the rendered
artifact, not on a claim about it. `deliver.py` runs them again at the last
moment anything can be stopped, and refuses to package a report that fails.

## Where to add something

| You want to | Change |
|---|---|
| measure something new | the matching `x2_metrics_*.py` |
| turn a measurement into a finding | `findings_for_*` in `x5_lock.py` |
| change what a report says | `render/compose.py` |
| change how a report looks | the template in `render/templates/` |
| stop a class of mistake shipping | a gate in `render/validate.py` |
| detect a new kind of sensitive data | a family in `detectors/families.py` |

A new gate should be added with the sentence that explains what it caught. Every
gate in `validate.py` carries one, because a gate whose reason is forgotten is a
gate somebody eventually deletes.
