# Privacy rules

These are enforced in code, not remembered. Each one exists because it failed.

## Counts and ranks in records; names only at render, only on request

The pipeline stores identifiers because it has to join on them. Reports carry
counts and categories. A named edition exists, requires `--edition named`, is
stamped as named in its manifest and cover note, and is produced only when
somebody has asked for it.

Default is shareable. Shareable means: no names, no email addresses, no
filenames, no conversation content.

## Any group under five is "fewer than five"

Never a digit. In a company of a hundred people, "3 people in the commercial
team last week" is close enough to a name that anyone in the company can finish
the sentence. `_window.describe_people` is the only way a person-count reaches
a reader, and the `small_group` gate fails a report that gets around it.

A count of conversations, connections or requests is not a person and is not
floored.

## The scan stores fingerprints, never values

`detectors.fingerprint` hashes the matched span. `candidates.jsonl` records
where a match was and what matched, never what the matched text said. A scan
that stored what it matched would be a second copy of the material it is
auditing, sitting in a folder with weaker handling than the original.

## Never test a credential

Not once. Not to check whether it is live. Liveness is judged from context —
where it appeared, when, what it is attached to.

Testing a credential is an unauthorised authentication attempt against somebody
else's system. It is also how a key that was already dead becomes an incident
with a timestamp on it.

## A pattern match is a candidate, never a finding

Roughly 44% of candidates survive being read. Some families survive none. A
candidate count published as a result overstates reality by more than double.
Nothing is published unread; the exposure report says so on its own face.

## Regulatory status cannot be asserted

This pipeline can observe that material looks like medical information relating
to a named person. It cannot establish that a regulation applies to it — that
depends on what the organisation is, which the pipeline does not know.

So the words for specific regulatory regimes are refused by the `privacy` gate.
Say "identifiable medical information relating to named individuals" and let the
organisation's own counsel decide what that means for them.

## Consent before conversation content

A per-lake record must exist before content is pulled, naming an accountable
person, even for a scheduled run. Not paperwork: so that in six months there is
an answer to "who decided we would read this".

## Nothing leaves the machine

The lake is read locally. Reports are files on disk. Nothing is uploaded to
100x, to Anthropic, or anywhere else. `.gitignore` keeps the lake and any real
rendered report out of version control by construction — lakes are excluded by
the files they are made of (`_state.json`, `part.jsonl`, `candidates.jsonl` and
the rest) wherever they are written, not only by folder name, because a lake
lives wherever `--data-dir` pointed. `tools/scrubber` fails the build if a real
organisation's people, domains or credentials appear in anything tracked, and
the scrub workflow fails it if a lake is tracked at all.

## Zero findings is a legal result

A report that always finds five things is a report that finds five things
whether or not they are there. If nothing crossed a threshold, the report says
nothing crossed a threshold.
