---
name: caio-setup
description: Set up 100x Chief AI Officer for the first time, or check an existing setup. Use when someone has just installed the plugin, asks "what does this do", "how do I start", "where do I get the reports", asks about credentials or API keys for it, or wants to see what the reports look like before connecting any real data. Also use when a report run fails and the cause might be setup rather than data.
---

# Getting started with 100x Chief AI Officer

## What this plugin does, in plain words

Your company pays for Claude. This plugin reads the usage records your company
already has and writes three reports about them:

| Report | Written for | The question it answers |
|---|---|---|
| **Waste Ledger** | Finance | What did it cost, where did money go that bought nothing, and what is that worth at renewal? |
| **Value X-Ray** | Head of AI | What did the spending actually buy, and what is getting in the way of it buying more? |
| **Exposure Report** | Compliance | What sensitive material reached the AI, and how did it get there? |

Each report is a single HTML file. Open it in a browser. Nothing to install,
nothing to log into, and it can be emailed as an attachment.

**Everything runs on the machine you run it on.** The data is read from your
company's own systems into a folder on disk, and nothing is sent anywhere.

## Two things to offer someone who has just installed this

Offer both. Most people want the second one first.

The session-start hook has usually already said this much and offered these two
choices, so do not deliver the introduction a second time — pick up from
whichever one they chose. `caio welcome` prints it again if they want it, and
`caio welcome --json` says what is actually on this machine: whether the command
is installed, whether a checkout is here, and whether any data or reports
already exist. Check that before telling somebody to run something.

### 1. Look at an example report

No credentials, no company data, nothing to set up. This invents a fictional
company and produces all three reports for it:

```bash
pip install -e .          # once, from the repository root

caio demo --out data-demo
caio all  --data-dir data-demo --out-dir _reports
```

Then open any file in `_reports/`. Everything in them is invented — the company,
the people, the numbers. It exists so you can see the shape of the thing before
deciding whether to point it at anything real.

Three example reports are also committed at `docs/mock-reports/` if you would
rather just open a file.

### 2. Connect your own data

This needs two things from whoever administers your company's Claude account:

- **An Admin API key**, for the cost and usage numbers.
- **A Compliance API key**, for conversation content. Only needed for the
  Exposure Report. Without it the other two reports still work.

Set whichever you have and pull. The two keys go in **separate** variables —
each pull resolves its own, so one shared `CAIO_API_KEY` would hand one of them
a key of the wrong type:

```bash
export ANTHROPIC_ADMIN_KEY="sk-ant-admin-..."              # for analytics
export ANTHROPIC_COMPLIANCE_ACCESS_KEY="sk-ant-api01-..."  # for content

python3 -m pipeline.fetch.analytics  --data-dir data     # cost and usage
python3 -m pipeline.fetch.compliance --data-dir data     # conversation content

caio all --data-dir data --out-dir _reports
```

The pull is separate from the reporting on purpose: pulling touches a network
and needs credentials, and someone who asked for a report should not get a
network call they did not ask for.

## Before pulling conversation content, read this out loud

The Exposure Report reads what people actually wrote and what tools handed
back. That is people's work, and in places it is people's private information.

The pipeline will not pull it until a consent record exists naming an
accountable person. It will prompt for one the first time. This is not
paperwork — it is so that in six months there is an answer to "who decided we
would read this, and when".

## Checking a setup that already exists

```bash
caio check --data-dir data
```

This says what each report can be produced from, what is missing, and which
dates the run resolves to. It never pulls anything and never fails destructively.

## When something goes wrong

| What you see | What it means |
|---|---|
| `command not found: caio` | The package is not installed. `pip install -e .` from the repository root, or put `PYTHONPATH=plugins/100x-chief-ai-officer` in front of `python3 -m pipeline.cli`. |
| `no lake at data` | Nothing has been pulled yet. Run `caio demo`, or pull. |
| `BLOCKED  Exposure Report` | Conversation content is not in this lake. Pull it, or produce the other two. |
| `does not pass its gates` | The report was written but must not be shared. The failing gate names the reason. Fix the report; there is deliberately no override. |
| `no API key` | No key in this shell. Analytics wants `ANTHROPIC_ADMIN_KEY`; the content pull wants `ANTHROPIC_COMPLIANCE_ACCESS_KEY`. An Admin key will not serve the content pull, or the other way round. |
| nothing at all happened after installing the plugin | The greeting only speaks once per machine. `caio welcome` prints it, or `CAIO_WELCOME=always` restores it for every session. |

## What to say when someone asks "is this safe"

- It reads; it never writes to your Claude account.
- It never tests a credential it finds. If it sees something that looks like a
  key, it records that it saw one — it does not try it.
- Reports are the shareable edition by default: counts, categories and dates.
  No names, no email addresses, no filenames, no conversation content.
- Any group of fewer than five people is described as "fewer than five", never
  as a number, because in a small company a precise count is close to a name.
