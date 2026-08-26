# Contributing

Thanks for looking at this. A few things about how this project is built will
save you time.

## Run it first

```bash
pip install -e ".[dev]"     # the dev extra is what brings in pytest

caio demo --out data-demo
caio all  --data-dir data-demo --out-dir _reports
```

If that works you have a complete development environment. No credentials are
needed for anything except the pull, and the pull is not needed for anything
except real data.

## Before you open a pull request

```bash
python3 -m pytest                    # the suite
python3 tools/check_manifests.py     # plugin and skill manifests
python3 tools/scrubber/scrub.py      # the publication gate
```

All three run in CI. The scrubber is the one that matters most: it fails the
build if a real organisation's people, domains or credentials appear anywhere
in a tracked file.

## The rules the code enforces, and why they are not negotiable

Each of these is in the code because it once failed in front of a reader. If a
change would relax one, that is a conversation to have in an issue first, not a
diff.

- **A pattern match is a candidate, never a finding.** Nothing is published
  unread.
- **Publish floors, not estimates**, and say so in the reader's words.
- **Check when something was born before trending across it.**
- **Verification runs independently of classification** — no access to the first
  pass's reasoning.
- **Counts and ranks in records; names only at render, only on request.** Any
  group under five is "fewer than five".
- **Zero findings is a legal result.** Never pad.
- **Every finding needs an owner and an action they control.**
- **Never test a credential.** Liveness is judged from context.

[`references/privacy_rules.md`](plugins/100x-caio/references/privacy_rules.md)
has the full set with what each one cost.

## Where things go

| You want to | Change |
|---|---|
| measure something new | the matching `stages/x2_metrics_*.py` |
| turn a measurement into a finding | `findings_for_*` in `stages/x5_lock.py` |
| change what a report says | `render/compose.py` |
| change how a report looks | the template in `render/templates/` |
| stop a class of mistake shipping | a gate in `render/validate.py` |
| detect a new kind of sensitive data | a family in `detectors/families.py` |

## About the templates

A report is one HTML file that renders itself from the data record embedded
inside it. The markup, the styles and the script are fixed; producing next
week's edition replaces the data and nothing else.

`build.py` refuses to write if so much as a space outside the data record
differs from the template. That is deliberate: when the only mutable surface is
data, the design cannot rot, and every difference between two editions is a
data difference you can read.

So: **do not hand-edit a rendered report.** Change the template or change the
data.

## About gates

If you add a gate to `validate.py`, write down what it caught. Every gate there
carries that sentence, because a gate whose reason has been forgotten is a gate
somebody eventually deletes for being annoying.

Gates run on the finished artifact, never on a stage's report of its own
output. Self-attestation is exactly what has failed before.

## Style

Match the file you are editing. Comments explain *why*, especially where the
code looks stranger than it needs to — that strangeness is usually a bug someone
already paid for.

## Tests

New behaviour needs a test. The ones worth writing are the ones that would be
an incident if they failed — a person identified, a candidate published as a
finding, a report that renders blank. `tests/test_privacy_rules.py` is the model.

## Reporting something sensitive

Do not open a public issue. See [`SECURITY.md`](SECURITY.md).
