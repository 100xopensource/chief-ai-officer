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
caio demo --out data-demo
caio all  --data-dir data-demo --out-dir _reports
```

Check `caio welcome --json` first for how to invoke it on this machine: from a
checkout, `pip install -e .` once and the above works as written; installed only
as a plugin, every command becomes
`python3 "$CLAUDE_PLUGIN_ROOT/pipeline/cli.py" …` — written with the variable,
never the path it expands to, since that directory changes between sessions.

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

Read that as "one *or* two", never "both". See below — a Compliance key serves
everything.

**One key is often all there is, and it is usually enough.** Every pull resolves
`CAIO_API_KEY` first, so a single key set there serves all of them — a Compliance
Access Key covers the analytics and directory pulls as well as the conversation
pull. Two keys are supported, not required.

Do not tell somebody holding one key that they are blocked. That reading of the
table above is the most common reason a person stops before they have started.

```bash
export CAIO_API_KEY="...the key they were given..."

caio pull everything --data-dir data     # cost, usage, seats
caio pull content    --data-dir data     # conversation text, consent-gated

caio all --data-dir data --out-dir _reports
```

If they do have two separate keys and want each pull to use its own, set them
per-pull instead and leave `CAIO_API_KEY` unset:

```bash
export ANTHROPIC_ADMIN_KEY="sk-ant-admin-..."              # analytics
export ANTHROPIC_COMPLIANCE_ACCESS_KEY="sk-ant-api01-..."  # directory and content
```

The pull is its own command on purpose, and `caio all` never does it: pulling
touches a network and needs credentials, and someone who asked for a report
should not get a network call they did not ask for.

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
| `command not found: caio` | The package is not installed. `pip install -e .` from the repository root; from a checkout, `PYTHONPATH=plugins/100x-chief-ai-officer python3 -m pipeline.cli`; **installed only as a plugin, with no `pip`**, `python3 "$CLAUDE_PLUGIN_ROOT/pipeline/cli.py"`. Run `caio welcome --json` first and use whichever form it reports — telling somebody to type `caio` when they do not have it is the most common way a first run fails. |
| the plugin's skills are missing, or the plugin list is empty | It was installed part-way through a session and is not picked up until a new one starts. Tell them to start a new conversation. It is not a failed install and reinstalling will not help. |
| `no lake at data` | Nothing has been pulled yet. Run `caio demo`, or `caio pull everything`. |
| `BLOCKED  Exposure Report` | Conversation content is not in this lake. `caio check` prints the command that fixes it, and what the consent gate will ask before it runs. |
| `does not pass its gates` | The report was written but must not be shared. The failing gate names the reason. Fix the report; there is deliberately no override. |
| `no API key` | No key in this shell. Set `CAIO_API_KEY` to whichever key they have — it is resolved first by every pull, so one key serves all of them. |
| a report arrives as a blank page | It was pasted into an email or opened in a preview pane. The page draws itself when a browser opens it. Save the file and open it in a browser; send reports as attachments. |
| a figure reads `—` or "cannot be measured" | That is deliberate and must not be reported as zero. The data does not carry what the figure needs; the report names the reason. `caio check` lists anything present-but-the-wrong-shape. |
| nothing at all happened after installing the plugin | The greeting only speaks once per machine. `caio welcome` prints it, or `CAIO_WELCOME=always` restores it for every session. |

## What to say when someone asks "is this safe"

- It reads; it never writes to your Claude account.
- It never tests a credential it finds. If it sees something that looks like a
  key, it records that it saw one — it does not try it.
- Reports are the shareable edition by default: counts, categories and dates.
  No names, no email addresses, no filenames, no conversation content.
- Any group of fewer than five people is described as "fewer than five", never
  as a number, because in a small company a precise count is close to a name.
