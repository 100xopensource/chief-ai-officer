<a href="https://100xpartners.ai">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/100xpartners-logo-dark.png">
    <img alt="100x Partners" src="docs/assets/100xpartners-logo.png" width="100px">
  </picture>
</a>

<h3></h3>

# 100x Chief AI Officer

> Built by 100x Partners — [100xpartners.ai](https://100xpartners.ai)

[![CI](https://github.com/100xopensource/chief-ai-officer/actions/workflows/ci.yml/badge.svg)](https://github.com/100xopensource/chief-ai-officer/actions/workflows/ci.yml)
[![Scrub](https://github.com/100xopensource/chief-ai-officer/actions/workflows/scrub.yml/badge.svg)](https://github.com/100xopensource/chief-ai-officer/actions/workflows/scrub.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Your firm pays for Claude every month. This tells you what that money bought,
where it was wasted, and whether anything sensitive went into it.**

It produces three reports. Each one is a single web page you open by
double-clicking it, or attach to an email. No dashboard to log into, no server
to run, no vendor to sign up with. Everything runs on your own computer, and
**nothing is uploaded anywhere.**

![Three example reports — a Waste Ledger for Finance, a Value X-Ray for whoever owns AI, and an Exposure Report for Compliance — built from an invented company's data](docs/assets/chief-ai-officer-hero.png)

## What it produces

| Output | Purpose |
|---|---|
| ![Example Waste Ledger: $2,325 spent last week, 71 of 112 people using Claude, $32.74 per person, and a chart of weekly spend](docs/assets/report-waste-ledger.png) | **Waste Ledger** — for **Finance and Procurement.** What Claude cost, which of that bought nothing, and what to say at renewal. |
| ![Example Value X-Ray: 71 people used Claude last week, 40% of conversations produced a finished document, and a chart of documents produced each week](docs/assets/report-value-xray.png) | **Value X-Ray** — for **whoever owns AI.** What the money actually bought, and what is stopping it buying more. |
| ![Example Exposure Report: at least 38 people with a confirmed sensitive item, 612 confirmed items, 77% arriving inside a tool's response](docs/assets/report-exposure.png) | **Exposure Report** — for **Compliance and Risk.** Whether client data, credentials or MNPI reached Claude, and how it got there. |

Every name and number in those examples is invented. To click through a finished
one, download a file from [`docs/mock-reports/`](docs/mock-reports/) and open it
in your browser — GitHub shows you a page's source code rather than the page
itself, so downloading is the trick.

## Before you start

1. **A Mac or a Windows PC.** You'll type a few commands, but you don't need to
   know what they mean — every one of them is on this page to copy and paste.
2. **Python, version 3.10 or newer.** Two minutes to check and install, below.
3. **Nothing else.** The first run invents a fictional company to report on, so
   it needs no API key, no permission from anyone, and touches no real data.

Set aside about ten minutes. Real data comes later, in
[Part 2](#part-2--using-your-real-data), and needs a key from whoever
administers your firm's Claude account.

## Install

### Step 1 — Check you have Python

Open **Terminal** (Mac: press `Cmd+Space`, type *Terminal*) or **PowerShell**
(Windows: press the Start key, type *PowerShell*). Paste this and press Enter:

```bash
python3 --version
```

You want **3.10 or higher**. If you see "command not found" or a lower number,
install Python from [python.org/downloads](https://www.python.org/downloads/)
and try again.

> **On Windows,** if `python3` isn't recognised, type `python` instead — and use
> `python` everywhere else on this page too.

### Step 2 — Download this project

```bash
git clone https://github.com/100xopensource/chief-ai-officer.git
cd chief-ai-officer
```

> **No `git`?** Click the green **Code** button at the top of this page, choose
> **Download ZIP**, unzip it, and open a terminal inside the unzipped folder.

### Step 3 — Install it

```bash
pip install -e .
```

This downloads two small libraries and creates a command called `caio`. It takes
about a minute.

> **If `pip` isn't found,** use `python3 -m pip install -e .` instead.

That's the install finished. Nothing is running in the background, and nothing
has read any of your data.

## Try it on a fictional company

Two commands, and you have all three reports in front of you.

### Step 4 — Invent a company

```bash
caio demo --out data-demo
```

You'll watch it build 14 weeks of fictional history — 112 employees, about
$11,700 of pretend spend, 900 conversations. All of it fabricated by your own
machine.

### Step 5 — Build the reports

```bash
caio all --data-dir data-demo --out-dir _reports
```

It prints what it's doing, then finishes with something like:

```
  built  waste-ledger-2026-07-12.html      ·  2 finding(s)  ·  13 integrity checks passed
  built  value-xray-2026-07-12.html        ·  1 finding(s)  ·  13 integrity checks passed
  built  exposure-report-2026-07-12.html   ·  6 finding(s)  ·  13 integrity checks passed
```

"Integrity checks" and not "checks", deliberately. Those checks say the document
is well-formed and leaks nothing. They do not say the numbers are right — see
[Why you can trust the numbers](docs/TRUST.md) for what does and does not get
verified.

### Step 6 — Look at them

Open the `_reports` folder and double-click any `.html` file. It opens in your
browser like a normal web page.

**That's the whole tool.** Two commands, forever. Everything below is about
pointing it at your real data instead of the fictional company.

---

## Part 2 — Using your real data

### What you need first

One or two API keys from whoever administers your firm's Claude account. You
almost certainly cannot create these yourself — they come out of the Claude
admin console, and only an account owner or admin can make them.

| You want | You need | Which reports it unlocks |
|---|---|---|
| Cost and usage numbers | An **Admin API key** | Waste Ledger, Value X-Ray |
| The text of conversations | A **Compliance API key** | Exposure Report |

Start with just the Admin key and you get two of the three reports. The Exposure
Report is the one that needs to read what people actually wrote, which is a
bigger decision — see
[the Exposure Report](#part-3--the-exposure-report-and-reading-peoples-conversations).

- The **Admin key** returns dollars, token counts and per-person usage totals.
  It cannot read anybody's messages.
- The **Compliance key** *can* read messages. Treat it like production database
  credentials, because that is effectively what it is.

<details>
<summary><b>An email you can send your Claude administrator</b> — copy, paste, fill in the blanks</summary>

<br>

> Hi — I'm setting up internal cost and governance reporting on our Claude
> Enterprise account, using an open-source tool that runs entirely on my machine
> and doesn't send our data anywhere ([link](https://github.com/100xopensource/chief-ai-officer)).
>
> Could you create and send me:
>
> 1. An **Admin API key** — read-only cost and usage totals, no conversation content.
> 2. *(if we're also doing the compliance report)* A **Compliance API key**.
>
> Please send them over [1Password / our secrets manager] rather than email or
> chat, since they're live credentials.
>
> Thanks — happy to walk you through what the tool does first if useful.

</details>

### Step 1 — Put your keys in your terminal

**Mac / Linux:**

```bash
export ANTHROPIC_ADMIN_KEY="sk-ant-admin-...paste-yours-here..."
export ANTHROPIC_COMPLIANCE_ACCESS_KEY="sk-ant-api01-...paste-yours-here..."
```

**Windows PowerShell:**

```powershell
$env:ANTHROPIC_ADMIN_KEY = "sk-ant-admin-...paste-yours-here..."
$env:ANTHROPIC_COMPLIANCE_ACCESS_KEY = "sk-ant-api01-...paste-yours-here..."
```

Only set the second one if you have a Compliance key and intend to run the
Exposure Report.

> **These last only until you close the terminal window.** That is deliberate —
> it keeps the keys out of files that get backed up or committed by accident. If
> you'd rather not retype them each time, put them in a `.env` file in this
> folder; `.env` is already in `.gitignore`, so it will never be committed.
>
> **Never paste a key into a spreadsheet, a chat message, or a script you email
> to someone.**

### Step 2 — Download your cost and usage data

```bash
caio pull everything --data-dir data
```

This creates a folder called `data` and fills it with your usage numbers and
your seat list. The first run pulls your history and takes a few minutes; later
runs only pull what's new and take seconds.

`everything` means cost, usage and the seat directory. It deliberately does
**not** include the text of conversations — that one is always asked for by name
(`caio pull content`), because it needs a recorded consent decision first.

### Step 3 — Build your reports

```bash
caio all --data-dir data --out-dir _reports
```

Same command as the fictional company, pointed at `data` instead of `data-demo`.
Your reports land in `_reports`.

**Run these two commands every Monday morning and you have a standing weekly
reporting pack:**

```bash
caio pull everything --data-dir data
caio all --data-dir data --out-dir _reports
```

If some data is missing, it tells you which report it can't build and exactly
what's missing, rather than guessing. To see that check on its own:

```bash
caio check --data-dir data
```

### Step 4 — Send a report to someone

```bash
caio all --data-dir data --deliver --to "Head of Compliance" --from "Your Name"
```

You get a folder that's ready to send, containing:

- the report file,
- the data it was built from,
- a **cover note written to be pasted into an email unedited**,
- a list of every check the report passed,
- checksums, so the person receiving it can prove the file wasn't altered.

If a report fails any of its safety checks, **nothing is packaged, and there is
no override flag.** A report you had to switch off a privacy check to send isn't
a report.

### Step 5 — Tell it the things only you know

Some facts about your organisation aren't in the data and never will be. Which
accounts are service accounts rather than people. Which bare identifier is
actually Salesforce. You work it out once, write it down once, and every report
from then on honours it.

Both files are optional, and both live in `data/_reports/config/`, next to the
data they describe — so copying this tool to another business unit doesn't carry
the old decisions with it.

<details>
<summary><b><code>seat_exclusions.json</code></b> — accounts that hold no active seat</summary>

<br>

They come out of the seat total, the idle-seat count, and the recoverable figure
that goes to Finance.

```json
{
  "excluded": [
    {"user_id": "user_01ABC...", "reason": "service account", "decided_on": "2026-08-17"},
    {"user_id": "user_01DEF...", "reason": "shared mailbox",  "decided_on": "2026-08-17"}
  ]
}
```

This one matters more than it looks. Without it, every service account in your
directory is priced as a seat somebody holds — which inflates the single number
the Waste Ledger exists to produce, every week, in the direction that looks best.

</details>

<details>
<summary><b><code>connector_aliases.json</code></b> — names for connections that report as a bare identifier</summary>

<br>

Some connections arrive with no name attached, and nothing in the data can say
what they are; the identifier is all the source publishes. Look each one up in
your admin console once:

```json
{"19b950ec-0c72-4e2e-9d3e-8a1f4c6b2e77": "Salesforce"}
```

As a bonus, this fixes double-counting: a connection that reports under a name
*and* under an identifier is counted twice until you say they're the same thing.

</details>

`caio check` prints which of these it found and how many entries each has, so a
decision being honoured is visible — and one being ignored is too.

---

## Part 3 — The Exposure Report, and reading people's conversations

The other two reports work on dollars and counts. This one has to read what your
colleagues actually typed into Claude, and what the tools they connected sent
back. In a financial firm that can include client records, deal terms, material
nonpublic information, and live credentials.

**The tool will not touch that data until you've said yes on the record.** When
you first try, it stops, shows you a full page explaining exactly what will be
stored and where, and asks for the name of the person accountable for the
decision. That answer is saved next to the data.

The point is simple: in six months, when somebody asks *"who decided we'd start
reading this?"*, there is an answer with a name and a date on it.

```bash
caio pull content --data-dir data
```

- **The conversation text is stored on your machine**, under `data/raw/`.
  Nothing is sent anywhere.
- **You can delete it at any time.** Delete the `data/raw/` folder. The reports
  and computed numbers survive.
- **You can withdraw consent**, and consent doesn't travel to another data
  folder, business unit or company.
- **`data/` and `data-*/` are already in `.gitignore`**, by shape as well as by
  name, so conversation content cannot be committed by accident.

Then nothing counts as a finding until it has been read twice — once by a judge,
once by an independent verifier who is never told what the first one concluded.
Only what survives both passes is published. You choose who does that reading: a
person with a worksheet and no network calls at all, or a Claude model.

**→ Full walkthrough, including both reading options:
[the Exposure Report](docs/EXPOSURE_REPORT.md).**

---

## Using it inside Claude Code (optional)

If your team uses Claude Code, you can install this as a plugin and ask for
things in plain English instead of typing commands.

**Step 1 — install it:**

```
/plugin marketplace add 100xopensource/chief-ai-officer
/plugin install 100x-chief-ai-officer@100x-chief-ai-officer
```

**Step 2 — start a new conversation.** This isn't a footnote. A plugin installed
part-way through a session isn't picked up until a new one begins: the skills
won't appear and the plugin list comes back empty, even though every file is
already on disk. It looks exactly like a failed install. It isn't one, and
reinstalling won't help — just start a new conversation.

It then introduces itself — what it is, the fact that it hasn't read anything
yet, and the two things you can do next. Ask for the welcome again any time, or
set `CAIO_WELCOME=never` to silence it.

Five skills become available:

| Ask for | Skill |
|---|---|
| "set this up" / "is my setup right?" | `caio-setup` |
| "build this week's reports" | `caio-weekly-reports` |
| "package the exposure report for Legal" | `caio-deliver-report` |
| "help me review these flagged passages" | `caio-review-candidates` |
| "where did this number come from?" | `caio-explain-report` |

<details>
<summary><b>Do I need <code>pip install</code> as well?</b></summary>

<br>

No. If you installed the plugin, the code is already on disk and every command
works as:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/pipeline/cli.py" check --data-dir data
```

Write it with `$CLAUDE_PLUGIN_ROOT`, not with the path it currently expands to.
That directory is scoped to the session and **changes between sessions**, so a
path you paste into your notes today is wrong tomorrow. The variable is right
every time. If you'd rather just type `caio`, run `pip install -e .` from a
checkout once and the prefix goes away.

In practice you won't type any of this — you ask in plain English and the skills
run the right command for you. It's here for when you want to check what was run.

</details>

---

## When something goes wrong

| What you see | What it means | Fix |
|---|---|---|
| `command not found: python3` | Python isn't installed | Install from [python.org](https://www.python.org/downloads/). On Windows try `python` instead. |
| `command not found: caio` | Step 3 didn't finish, or you're in the wrong folder | `cd` back into the project folder and re-run `pip install -e .`. Installed as a Claude Code plugin and never ran `pip`? You don't have `caio` — use `python3 "$CLAUDE_PLUGIN_ROOT/pipeline/cli.py"` instead. |
| `command not found: pip` | `pip` isn't on your PATH | Use `python3 -m pip install -e .` |
| The plugin's skills don't appear, or the plugin list is empty | It was installed part-way through a session | Start a new conversation. Nothing is broken and reinstalling won't help. |
| An emailed report opens as a blank white page | The page draws itself when a browser opens it; a preview pane doesn't | Save the attachment and open it in a browser. Send reports as attachments, never pasted into an email body. |
| `CAIO_API_KEY, ANTHROPIC_ADMIN_KEY, or ANTHROPIC_API_KEY not set` | No key in this terminal window | Re-run the `export` from Part 2, Step 1. Closing the terminal clears it. |
| `401` or `403` from the API | The key is wrong, expired, or is the wrong *type* of key | Analytics needs an **Admin** key; conversation content needs a **Compliance** key. They aren't interchangeable. |
| `no API key. Set CAIO_API_KEY to a Compliance Access Key` | You have an Admin key but not a Compliance one | Either request a Compliance key, or skip the Exposure Report and run the other two. |
| A report is listed as blocked, not built | It failed one of the publication gates | The message names the gate. This is the tool working correctly — fix the cause, don't look for an override. |
| "too new to compare" all over a report | Your data doesn't go back far enough yet | Normal on the first week or two. Keep pulling; comparisons appear once there's history. |
| `caio check` says a report can't be built | Some required data is missing | The output names exactly which data. Usually it's the compliance pull you haven't run. |

Still stuck? Open an issue at
[github.com/100xopensource/chief-ai-officer/issues](https://github.com/100xopensource/chief-ai-officer/issues).
Please don't paste real report content or API keys into an issue.

## Common questions

**Does any of our data leave the building?**
Only to Anthropic's own API, and only when you ask for it. There is no
telemetry, no analytics, no phone-home, and no 100x server involved at any
point. Exactly two places make outbound calls, and you can check both yourself:
`pipeline/fetch/` downloads your own account's data, and
`pipeline/judges/anthropic.py` runs *only* if you choose the model judge in the
Exposure Report. A person with a worksheet does the same job with no network
calls at all.

**Does installing this read our conversations?**
No. Installing reads nothing and downloads nothing. Every download is a command
you type yourself, and the conversation pull additionally requires a recorded
consent decision with a person's name on it.

**Can I run just the cost reports and skip the conversation reading entirely?**
Yes, and plenty of firms should start that way. Run only
`caio pull everything` and you get the Waste Ledger and Value X-Ray. The
Exposure Report will simply be reported as unbuildable, with the reason and the
command that would fix it.

**Can employees be identified in the reports?**
Not by default. Reports are built in "shareable" edition, where the gates block
email addresses and any group under five people. There is a `--edition named`
option that permits email addresses for internal-only use; every other gate
still applies.

**How often should I run it?**
Weekly. The reports are built around a week-long window and compare it to the
previous four weeks averaged.

**Is this an Anthropic product?**
No. It's open source from [100x Partners](https://100xpartners.ai), Apache-2.0
licensed, and reads Anthropic's public admin and compliance APIs like any other
client would.

## Repository guide

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
