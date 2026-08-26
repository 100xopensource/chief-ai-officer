# Security

## Reporting a vulnerability

Do not open a public issue.

Email **security@100xteam.ai** with what you found, how to reproduce it, and
what you think the impact is. You will get an acknowledgement within three
working days.

If the finding involves data from a real organisation, describe the shape of the
problem rather than including the data. We do not want a copy.

## What this tool touches

Being clear about this, because it reads sensitive material by design.

**It reads. It never writes to your Claude account.** The API calls it makes are
reads of analytics, directory and compliance endpoints.

**Conversation content requires consent first.** A per-lake record naming an
accountable person must exist before any content is pulled, including on a
scheduled run.

**It never tests a credential.** If the scan finds something that looks like a
key, it records that it saw one. It does not try it. Testing a credential is an
unauthorised authentication attempt against somebody's system, and it turns a
dead key into an incident with a timestamp.

**It stores fingerprints, not values.** The scan writes a hash of each matched
span. A scan that stored what it matched would be a second copy of the material
it is auditing, in a folder with weaker handling than the original.

**Nothing leaves the machine.** The lake is local. Reports are files. Nothing is
sent to 100x, and nothing is sent to Anthropic beyond the read calls that fetch
your own data.

## Credentials

Keys are read from the environment, never from a file in the repository and
never from a command-line argument that would land in shell history:

```bash
export CAIO_API_KEY="sk-ant-..."
```

`.gitignore` excludes `.env`, `*.key` and the whole lake by construction, and
`tools/scrubber/scrub.py` fails CI if a credential-shaped string reaches a
tracked file.

## If you are about to commit something

```bash
python3 tools/scrubber/scrub.py --explain
```

That prints every rule and what it protects against. It fails on email
addresses, corporate domains, credentials, national identity numbers, private
keys, and rendered reports marked as the named edition.

Rendered reports built from real data are excluded from version control by
`.gitignore`. Only the synthetic examples in `docs/mock-reports/` are tracked.

## Threat model, briefly

The realistic risk here is not somebody attacking this tool. It is this tool
producing a file that gets forwarded.

A report is easy to email and hard to un-email. That is why the shareable
edition is the default, why a named edition has to be asked for and is stamped
as one in its own manifest, why any group under five people is described in
words, and why delivery refuses to package a report that fails a privacy check
and offers no override flag.
