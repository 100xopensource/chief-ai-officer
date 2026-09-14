<a href="https://100xteam.ai/">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/100xteam-logo-dark.png">
    <img alt="100x" src="docs/assets/100xteam-logo.png" width="100px">
  </picture>
</a>

<h3></h3>

# 100x Chief AI Officer

> Built by 100x — [100xteam.ai](https://100xteam.ai)

[![CI](https://github.com/100xopensource/chief-ai-officer/actions/workflows/ci.yml/badge.svg)](https://github.com/100xopensource/chief-ai-officer/actions/workflows/ci.yml)
[![Scrub](https://github.com/100xopensource/chief-ai-officer/actions/workflows/scrub.yml/badge.svg)](https://github.com/100xopensource/chief-ai-officer/actions/workflows/scrub.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**AI spend reporting for the person who has to answer for it — in Claude Cowork
or Claude Code.**

Your firm pays for Claude every month. This turns your own account's usage
records into three reports: what that money bought, where it was wasted, and
whether anything sensitive went into it. You ask in plain English; the plugin
runs everything on your own machine and writes the reports to a folder you
choose.

![Three example reports — a Waste Ledger for Finance, a Value X-Ray for whoever owns AI, and an Exposure Report for Compliance — built from an invented company's data](docs/assets/chief-ai-officer-hero.png)

## What it produces

| Output | Purpose |
|---|---|
| ![Example Waste Ledger: $2,325 spent last week, 71 of 112 people using Claude, $32.74 per person, and a chart of weekly spend](docs/assets/report-waste-ledger.png) | **Waste Ledger** — for **Finance and Procurement.** What Claude cost, which of that bought nothing, and what to say at renewal. |
| ![Example Value X-Ray: 71 people used Claude last week, 40% of conversations produced a finished document, and a chart of documents produced each week](docs/assets/report-value-xray.png) | **Value X-Ray** — for **whoever owns AI.** What the money actually bought, and what is stopping it buying more. |
| ![Example Exposure Report: at least 38 people with a confirmed sensitive item, 612 confirmed items, 77% arriving inside a tool's response](docs/assets/report-exposure.png) | **Exposure Report** — for **Compliance and Risk.** Whether client data, credentials or MNPI reached Claude, and how it got there. |

Each report is a single web page. Double-click it to open it, or attach it to an
email. No dashboard to log into, no server to run, no vendor to sign up with.

Every name and number in those examples is invented. To click through a finished
one, download a file from [`docs/mock-reports/`](docs/mock-reports/) and open it
in your browser — GitHub shows you a page's source code rather than the page
itself, so downloading is the trick.

## Before you start

1. **Claude Cowork, or Claude Code.** Either one. The plugin is the same.
2. **A browser**, to open the reports.
3. **Nothing to install.** Python is already on your machine, and the first run
   needs no API key, no permission from anyone, and touches no real data.

Your own firm's numbers come later, and need one API key from whoever
administers your Claude account. See [Run it on your own data](#run-it-on-your-own-data).

## Install

### In Claude Cowork

1. Open Claude Cowork.
2. Select **Customize → Plugins**.
3. Select **Add**, then **Add marketplace**.
4. Paste `https://github.com/100xopensource/chief-ai-officer`.
5. Pick **100x Chief AI Officer** from the list and install it.
6. **Start a new conversation.** See below — this step is not optional.

### In Claude Code

```
/plugin marketplace add 100xopensource/chief-ai-officer
/plugin install 100x-chief-ai-officer@100x-chief-ai-officer
```

Then **start a new conversation.**

> **Why starting a new conversation matters.** A plugin installed part-way
> through a session isn't picked up until a new one begins: the skills won't
> appear and the plugin list comes back empty, even though every file is already
> on disk. It looks exactly like a failed install. It isn't one, and reinstalling
> won't help.

When the new conversation opens, the plugin introduces itself — what it is, the
fact that it hasn't read anything yet, and what you can do next.

## See it on a fictional company first

Do this before pointing it at anything real. Start a new chat and say:

> **"Show me an example Chief AI Officer report."**

Claude invents a fictional 112-person company — 14 weeks of history, about
$11,700 of pretend spend, 900 conversations, all fabricated on your own machine
— and builds all three reports from it. Then it tells you where they are. Open
any of them in your browser.

No API key, no permission from anyone, and no real data involved.

## Run it on your own data

### 1. Get a key

You need **one API key** from whoever administers your firm's Claude account.
You almost certainly cannot create it yourself — it comes out of the Claude
admin console, and only an account owner or admin can make one.

| The key they give you | What you get |
|---|---|
| A **Compliance API key** | All three reports |
| An **Admin API key** | The Waste Ledger and the Value X-Ray |

**One key is usually all there is, and it is usually enough.** A Compliance key
covers the cost and usage pulls as well as the conversation pull. If you are
given an Admin key only, you still get two of the three reports, and the third
one reports itself as unbuildable with the reason — nothing silently breaks.

<details>
<summary><b>An email you can send your Claude administrator</b> — copy, paste, fill in the blanks</summary>

<br>

> Hi — I'm setting up internal cost and governance reporting on our Claude
> Enterprise account, using an open-source tool that runs entirely on my machine
> and doesn't send our data anywhere ([link](https://github.com/100xopensource/chief-ai-officer)).
>
> Could you create and send me an API key for it? Either works:
>
> - a **Compliance API key**, which covers all three reports, or
> - an **Admin API key**, which covers the two cost reports — read-only totals,
>   no conversation content.
>
> Please send it over [1Password / our secrets manager] rather than email or
> chat, since it's a live credential.
>
> Thanks — happy to walk you through what the tool does first if useful.

</details>

> **Treat a Compliance key like production database credentials,** because that
> is effectively what it is. It can read what people wrote. An Admin key cannot —
> it returns dollars, token counts and per-person usage totals only.

### 2. Say so

Start a new chat and say:

> **"Set up Chief AI Officer with my API key."**

Claude asks where to put the key, checks what it can reach, and tells you which
reports your account can produce before pulling anything. Downloading is always
a separate, named step — asking for a report never triggers a network call you
didn't ask for.

### 3. Ask for the reports

> **"Build this week's Chief AI Officer reports."**

The first pull fetches your history and takes a few minutes; later runs only
fetch what's new and take seconds. **Ask for this every Monday morning and you
have a standing weekly reporting pack.**

### 4. Send one to someone

> **"Package the Exposure Report for our Head of Compliance."**

You get a folder that's ready to send, containing the report, the data it was
built from, a cover note written to be pasted into an email unedited, a list of
every check the report passed, and checksums so the recipient can prove the file
wasn't altered.

If a report fails any of its safety checks, **nothing is packaged, and there is
no override flag.** A report you had to switch off a privacy check to send isn't
a report.

## What you can ask for

You never have to remember a command. Ask in plain English and the right skill
runs it for you.

| Say something like | What happens |
|---|---|
| "Set this up" / "Is my setup right?" | Checks what's installed, what data you have, and which reports it can build |
| "Show me an example report" | Invents a fictional company and builds all three |
| "Build this week's reports" | Pulls what's new and writes the three reports |
| "Package the exposure report for Legal" | A folder ready to send, with a cover note and checksums |
| "Help me review these flagged passages" | Walks you through judging what the scan found |
| "Where did this number come from?" | Explains a figure in plain language, down to the source |

## Two things only you can tell it

Some facts about your organisation aren't in the data and never will be. Which
accounts are service accounts rather than people. Which bare identifier is
actually Salesforce. Say so once — *"these three user IDs are service accounts,
not people"* — and every report from then on honours it.

This matters more than it looks. Without it, every service account in your
directory is priced as a seat somebody holds, which inflates the single number
the Waste Ledger exists to produce, every week, in the direction that looks best.

Both decisions are stored next to the data they describe, so copying this tool to
another business unit doesn't carry the old decisions with it. The file formats
are in [the command reference](docs/REFERENCE.md#decisions-only-you-can-make) if
you'd rather write them by hand.

## Before it reads anyone's conversations

The Waste Ledger and the Value X-Ray work on dollars and counts. The Exposure
Report has to read what your colleagues actually typed into Claude, and what the
tools they connected sent back. In a financial firm that can include client
records, deal terms, material nonpublic information, and live credentials.

**It will not touch that data until you've said yes on the record.** The first
time you ask, it stops, explains exactly what will be stored and where, and asks
for the name of the person accountable for the decision. That answer is saved
next to the data.

The point is simple: in six months, when somebody asks *"who decided we'd start
reading this?"*, there is an answer with a name and a date on it.

Then nothing counts as a finding until it has been read twice — once by a judge,
once by an independent verifier who is never told what the first one concluded.
Only what survives both passes is published. You choose who does that reading: a
person working through a worksheet, with no network calls at all, or a Claude
model.

**→ Full walkthrough: [the Exposure Report](docs/EXPOSURE_REPORT.md).**

## Prefer the command line?

Everything above is also a CLI, and the plugin is running that CLI for you. If
you'd rather drive it yourself:

```bash
git clone https://github.com/100xopensource/chief-ai-officer.git
cd chief-ai-officer
pip install -e .

caio demo --out data-demo                              # invent a company
caio all  --data-dir data-demo --out-dir _reports      # build all three reports
```

Then open any file in `_reports`.

Reports built from an existing folder of data need **nothing installed at all** —
the demo, the gap check, the metrics, the scan and the report gates are standard
library only. `pip install -r requirements.txt` adds `requests`, which is needed
the first time you pull your own account's data.

**→ Every command, the six stages, and running without installing:
[the command reference](docs/REFERENCE.md).**

## When something goes wrong

| What you see | What it means | Fix |
|---|---|---|
| The plugin's skills don't appear, or the plugin list is empty | It was installed part-way through a session | Start a new conversation. Nothing is broken and reinstalling won't help. |
| Nothing happened at all after installing | The greeting only speaks once per machine | Ask "what is Chief AI Officer?" — it will introduce itself. |
| `ModuleNotFoundError: No module named 'requests'` on your first real pull | Downloading needs one library; building reports doesn't | `pip install requests`, or `pip install -r requirements.txt` from a checkout. |
| `401` or `403` from the API | The key is wrong, expired, or revoked | Check it was pasted whole. An Admin key cannot read conversation content — for the Exposure Report you need a Compliance key. |
| A report is listed as blocked, not built | It failed one of the publication gates | The message names the gate. This is the tool working correctly — fix the cause, don't look for an override. |
| "too new to compare" all over a report | Your data doesn't go back far enough yet | Normal in the first week or two. Keep pulling; comparisons appear once there's history. |
| A figure reads `—` or "cannot be measured" | The data doesn't carry what that figure needs | Deliberate, and it must not be read as zero. The report names the reason. |
| An emailed report opens as a blank white page | The page draws itself when a browser opens it; a preview pane doesn't | Save the attachment and open it in a browser. Send reports as attachments, never pasted into an email body. |
| `command not found: caio` | You're using the CLI without having installed it | `pip install -e .` from a checkout — or just ask Claude instead, which doesn't need it. |

Still stuck? Open an issue at
[github.com/100xopensource/chief-ai-officer/issues](https://github.com/100xopensource/chief-ai-officer/issues).
Please don't paste real report content or API keys into an issue.

## Common questions

**Does any of our data leave the building?**
Only to Anthropic's own API, and only when you ask for it. There is no
telemetry, no analytics, no phone-home, and no 100x server involved at any
point. Exactly two places make outbound calls, and you can check both yourself:
`pipeline/fetch/` downloads your own account's data, and
`pipeline/judges/anthropic.py` runs *only* if you pick the model judge for the
Exposure Report. A person with a worksheet does the same job with no network
calls at all.

**Does installing this read our conversations?**
No. Installing reads nothing and downloads nothing. Every download is a step you
ask for, and the conversation pull additionally requires a recorded consent
decision with a person's name on it.

**Can I run just the cost reports and skip the conversation reading entirely?**
Yes, and plenty of firms should start that way. Pull cost and usage only, and you
get the Waste Ledger and Value X-Ray. The Exposure Report is reported as
unbuildable, with the reason and the step that would fix it.

**Can employees be identified in the reports?**
Not by default. Reports are built in "shareable" edition, where the gates block
email addresses and any group under five people. There is a named edition for
internal-only use that permits email addresses; every other gate still applies.

**How often should I run it?**
Weekly. The reports are built around a week-long window and compare it to the
previous four weeks averaged.

**Is this an Anthropic product?**
No. It's open source from [100x](https://100xteam.ai), Apache-2.0 licensed, and
reads Anthropic's public admin and compliance APIs like any other client would.

## Repository guide

- [Getting started](docs/GETTING_STARTED.md) — the full walkthrough, from
  installing to your first real report.
- [Why you can trust the numbers](docs/TRUST.md) — what the publication gates
  check, what they deliberately don't, and the two wrong reports that changed
  the wording.
- [The Exposure Report](docs/EXPOSURE_REPORT.md) — consent, the two reading
  passes, and choosing between a person and a model.
- [Command reference](docs/REFERENCE.md) — every command, the six stages, what's
  in this repository, and running without installing.
- [Architecture](docs/architecture.md) — four diagrams: what ships, what a person
  does with it, how data moves, and the rule separating a match from a finding.
- [Privacy](PRIVACY.md) · [Security policy](SECURITY.md) ·
  [Contributing](CONTRIBUTING.md) · [Handoff notes](HANDOFF.md)

## Licensing

- This repository is licensed under [Apache-2.0](LICENSE). See [NOTICE](NOTICE)
  for the attribution notice that must be kept in any copy.
- `requests` and `pandas` are runtime dependencies installed from PyPI. They are
  not redistributed here. `anthropic` is optional and only needed for the model
  judge.
- Apache-2.0 grants no trademark rights. You may fork and redistribute the code;
  you may not use the 100x name or marks to describe your fork or imply that
  100x produced it.
- Security issues: please follow [SECURITY.md](SECURITY.md) rather than opening a
  public issue.
