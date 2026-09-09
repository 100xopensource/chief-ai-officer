# 100x Chief AI Officer

**Your firm pays for Claude every month. This tells you what that money bought,
where it was wasted, and whether anything sensitive went into it.**

It produces three reports. Each one is a single web-page file you can open by
double-clicking it, or attach to an email. No dashboard to log into, no server
to run, no vendor to sign up with.

Everything runs on your own computer. **Nothing is uploaded anywhere.**

---

## The three reports

| Report | Give it to | It answers |
|---|---|---|
| **Waste Ledger** | Finance / Procurement | What did Claude cost us, which of that bought nothing, and what should we say at renewal? |
| **Value X-Ray** | Whoever owns AI | What did the money actually buy, and what is stopping it buying more? |
| **Exposure Report** | Compliance / Risk | Did anything sensitive — client data, credentials, MNPI — end up in Claude, and how did it get there? |

Want to see one before you install anything? Three finished examples are in
[`docs/mock-reports/`](docs/mock-reports/). Download a file and open it in your
browser. (GitHub shows you the page's source code instead of the page itself, so
downloading is the trick.) Every name and number in them is made up.

---

## Part 1 — Try it in five minutes, using fake data

Do this first. It needs **no API keys, no permission from anyone, and touches no
real data.** It invents a fictional 112-person company and writes all three
reports about it, so you can see exactly what you'd be getting.

### Step 1 — Check you have Python

Open Terminal (Mac) or Command Prompt / PowerShell (Windows) and type:

```bash
python3 --version
```

You want **3.10 or higher**. If it says "command not found" or a lower number,
install Python from [python.org/downloads](https://www.python.org/downloads/)
and try again. On Windows you may need to type `python` instead of `python3` —
if so, use `python` everywhere below too.

### Step 2 — Download this project

```bash
git clone https://github.com/100xopensource/chief-ai-officer.git
cd chief-ai-officer
```

No `git`? Click the green **Code** button at the top of this page → **Download
ZIP**, unzip it, then open a terminal inside the unzipped folder.

### Step 3 — Install it

```bash
pip install -e .
```

This downloads two small libraries and creates a command called `caio`. It takes
about a minute.

> If `pip` isn't found, use `python3 -m pip install -e .` instead.

### Step 4 — Invent a company

```bash
caio demo --out data-demo
```

You'll see it build 14 weeks of fictional history — 112 employees, about $11,700
of pretend spend, 900 conversations. All of it fabricated by your own machine.

### Step 5 — Build the reports

```bash
caio all --data-dir data-demo --out-dir _reports
```

It prints what it's doing, then finishes with something like:

```
  built  waste-ledger-2026-07-12.html      ·  2 finding(s)  ·  12 gates passed
  built  value-xray-2026-07-12.html        ·  2 finding(s)  ·  12 gates passed
  built  exposure-report-2026-07-12.html   ·  6 finding(s)  ·  12 gates passed
```

### Step 6 — Look at them

Open the `_reports` folder and double-click any `.html` file. It opens in your
browser like a normal web page.

**That's the whole tool.** Two commands, forever. Everything after this point is
about pointing it at your real data instead of the fake company.

---

## Part 2 — What you need before using real data

You need one or two API keys from whoever administers your company's Claude
account. You almost certainly cannot create these yourself — they come out of
the Claude admin console, and only an account owner or admin can make them.

| You want | You need | Which reports it unlocks |
|---|---|---|
| Cost and usage numbers | An **Admin API key** | Waste Ledger, Value X-Ray |
| The text of conversations | A **Compliance API key** | Exposure Report |

You can start with just the Admin key and get two of the three reports. The
Exposure Report is the one that needs to read what people actually wrote, which
is a bigger decision — see [Part 4](#part-4--the-exposure-report-and-reading-peoples-conversations).

### Email to send your Claude administrator

Copy, paste, fill in the blanks:

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

### What these keys can and cannot do

- The **Admin key** returns dollars, token counts and per-person usage totals.
  It cannot read anybody's messages.
- The **Compliance key** *can* read messages. Treat it like production database
  credentials, because that is effectively what it is.

---

## Part 3 — Using your real data

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
python3 -m pipeline.fetch.analytics --data-dir data
```

This creates a folder called `data` and fills it with your usage numbers. First
run pulls your history and takes a few minutes; later runs only pull what's new
and take seconds.

### Step 3 — Build your reports

```bash
caio all --data-dir data --out-dir _reports
```

Same command as the demo, pointed at `data` instead of `data-demo`. Your reports
land in `_reports`.

**Run these two commands every Monday morning and you have a standing weekly
reporting pack.**

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

---

## Part 4 — The Exposure Report, and reading people's conversations

The other two reports work on dollars and counts. This one has to read what your
colleagues actually typed into Claude, and what the tools they connected sent
back. In a financial firm that can include client records, deal terms, material
nonpublic information, and live credentials.

The tool will not touch that data until you've said yes on the record. When you
first try, it stops, shows you a full page explaining exactly what will be stored
and where, and asks for the name of the person accountable for the decision. That
answer is saved next to the data.

The point is simple: in six months, when somebody asks *"who decided we'd start
reading this?"*, there is an answer with a name and a date on it.

```bash
python3 -m pipeline.fetch.compliance --data-dir data
```

Things worth knowing before you run it:

- **The conversation text is stored on your machine**, under `data/raw/`. Nothing
  is sent anywhere.
- **You can delete it at any time.** Delete the `data/raw/` folder. The reports
  and computed numbers survive; you just can't re-analyse without re-downloading.
- **You can withdraw consent:** `python3 -m pipeline.fetch.consent --data-dir data --revoke`
- **Consent doesn't travel.** It's recorded per data folder, so copying this tool
  to another company or another business unit does not carry the old permission
  with it.
- **`data/` and `data-*/` are already in `.gitignore`**, by shape as well as by
  name, so conversation content cannot be committed by accident.

### Then: nothing counts as a finding until it's been read twice

This is the most important thing about this tool, so it's worth understanding.

When the scan spots something that looks like a leaked password, that is a **lead
to go check** — not a result. Historically only about **44%** of those leads turn
out to be real once someone reads them. Some categories turn out real *none* of
the time.

So there are two more steps before anything is printed as a finding:

1. **Judge** — someone (or something) reads each flagged passage and decides
   whether it's genuinely sensitive.
2. **Verify** — it gets read a *second* time, independently, and the second
   reader is never told what the first one concluded. A checker who's been shown
   the answer agrees with it, and that's a signature, not a check.

Only what survives both passes is published. Anything the two passes disagree
about is published as neither — a disagreement is a fact about the material, not
a tie to break quietly.

**Option A — a person does the reading.** No network calls, nothing extra to
install. This is two passes, because a human is in the middle of it:

```bash
# 1. Writes review-worksheet.md, with each flagged passage and a blank VERDICT line
caio judge --data-dir data --judge worksheet

# 2. You (or a compliance colleague) open review-worksheet.md and fill in each
#    VERDICT line. Then run exactly the same command again to read it back:
caio judge --data-dir data --judge worksheet

# 3. Then build the reports
caio all --data-dir data --out-dir _reports
```

Verdicts you leave blank stay unread, and unread means not published. Nothing is
counted just because you skipped it.

> ⚠️ `review-worksheet.md` is the one file that contains raw conversation text in
> plain sight. It's already in `.gitignore`. Delete it when you're done, and don't
> email it around.

**Option B — a Claude model does the reading.** One command, but it needs a
one-off install and it sends the flagged passages to a model:

```bash
pip install anthropic
export ANTHROPIC_API_KEY="sk-ant-..."

caio all --data-dir data --out-dir _reports --read --judge anthropic
```

> The model judge is the **only** part of this project that needs a third
> library, and the only part that sends any of your content to a model. If your
> firm would rather that never happen, use Option A. Everything else — the demo,
> both cost reports, the scan, the tests — runs without it.

**The report says on its own face who did the reading.** The built-in offline
stand-in used by the demo is not a real reading, and any report built with it
says so, so it can never be mistaken for the real thing.

---

## Why you can trust the numbers

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

### The twelve gates

No report is allowed out until it passes twelve checks, run against the finished
file rather than against a promise about what's in it. A report is blocked if it
contains an email address, a filename, a loose run of digits that could be a
leaked value, a group of three described as "3 people", internal jargon, a
regulatory claim the tool can't support, arithmetic that doesn't add up, a
caveat that got quietly dropped during a rewrite, or a record missing something
the page needs to display.

**Every one of those gates exists because that exact mistake once reached a real
reader.**

To run the gates against a report by hand:

```bash
python3 -m pipeline.render.validate _reports/exposure-report-2026-07-12.html \
        --json-sidecar _reports/exposure-report-2026-07-12.json
```

That prints `12 passed`. Leave off the `--json-sidecar` and you'll see `11
passed` instead — the twelfth gate is the one that checks the report against its
own data file, so it needs both files to run. Both are written side by side in
`_reports/`.

---

## Using it inside Claude Code (optional)

If your team uses Claude Code, you can install this as a plugin and just ask for
things in plain English instead of typing commands.

```
/plugin marketplace add 100xopensource/chief-ai-officer
/plugin install 100x-chief-ai-officer@100x-chief-ai-officer
```

It introduces itself on your next session — what it is, the fact that it hasn't
read anything yet, and the two things you can do next. Type `caio welcome` to see
that again any time, or set `CAIO_WELCOME=never` to silence it.

Five skills become available:

| Ask for | Skill |
|---|---|
| "set this up" / "is my setup right?" | `caio-setup` |
| "build this week's reports" | `caio-weekly-reports` |
| "package the exposure report for Legal" | `caio-deliver-report` |
| "help me review these flagged passages" | `caio-review-candidates` |
| "where did this number come from?" | `caio-explain-report` |

---

## When something goes wrong

| What you see | What it means | Fix |
|---|---|---|
| `command not found: python3` | Python isn't installed | Install from [python.org](https://www.python.org/downloads/). On Windows try `python` instead. |
| `command not found: caio` | Step 3 didn't finish, or you're in the wrong folder | `cd` back into the project folder and re-run `pip install -e .` |
| `command not found: pip` | `pip` isn't on your PATH | Use `python3 -m pip install -e .` |
| `CAIO_API_KEY, ANTHROPIC_ADMIN_KEY, or ANTHROPIC_API_KEY not set` | No key in this terminal window | Re-run the `export` from Part 3, Step 1. Closing the terminal clears it. |
| `401` or `403` from the API | The key is wrong, expired, or is the wrong *type* of key | Analytics needs an **Admin** key; conversation content needs a **Compliance** key. They aren't interchangeable. |
| `no API key. Set CAIO_API_KEY to a Compliance Access Key` | You have an Admin key but not a Compliance one | Either request a Compliance key, or skip the Exposure Report and run the other two. |
| A report is listed as blocked, not built | It failed one of the twelve gates | The message names the gate. This is the tool working correctly — fix the cause, don't look for an override. |
| "too new to compare" all over a report | Your data doesn't go back far enough yet | Normal on the first week or two. Keep pulling; comparisons appear once there's history. |
| `caio check` says a report can't be built | Some required data is missing | The output names exactly which data. Usually it's the compliance pull you haven't run. |

Still stuck? Open an issue at
[github.com/100xopensource/chief-ai-officer/issues](https://github.com/100xopensource/chief-ai-officer/issues).
Please don't paste real report content or API keys into an issue.

---

## Common questions

**Does any of our data leave the building?**
Only to Anthropic's own API, and only when you ask for it. There is no
telemetry, no analytics, no phone-home, and no 100x server involved at any
point. Exactly two things make outbound calls, and you can check both yourself:

- `pipeline/fetch/` — downloads your own account's data. These are the only two
  files in the project that import `requests`.
- `pipeline/judges/anthropic.py` — used *only* if you choose `--judge anthropic`,
  which sends flagged passages to a Claude model to be read. If you don't use
  that option, this file never runs. A person with a worksheet
  (`--judge worksheet`) does the same job with no network calls at all.

**Does installing this read our conversations?**
No. Installing reads nothing and downloads nothing. Every download is a command
you type yourself, and the conversation pull additionally requires a recorded
consent decision with a person's name on it.

**Can I run just the cost reports and skip the conversation reading entirely?**
Yes, and plenty of firms should start that way. Run only
`python3 -m pipeline.fetch.analytics` and you get the Waste Ledger and Value
X-Ray. The Exposure Report will simply be reported as unbuildable, with the
reason.

**Can employees be identified in the reports?**
Not by default. Reports are built in "shareable" edition, where the gates block
email addresses and any group under five people. There is a `--edition named`
option that permits email addresses for internal-only use; every other gate
still applies.

**How often should I run it?**
Weekly. The reports are built around a week-long window and compare it to the
previous four weeks averaged.

**Is this an Anthropic product?**
No. It's open source from [100x](https://100xteam.ai), Apache-2.0 licensed, and
reads Anthropic's public admin and compliance APIs like any other client would.

---

## Reference

### Every command

| Command | What it does |
|---|---|
| `caio welcome` | What this is and what to do next, in plain English |
| `caio demo` | Invent a fictional company to try it on |
| `caio check` | What your data can answer, and what's missing |
| `caio scan` | Sweep conversation content for sensitive-data patterns |
| `caio judge` | Read the flagged passages and decide if they're real |
| `caio verify` | Independently re-read whatever the first pass called real |
| `caio report` | Build one report, or all three |
| `caio deliver` | Package a built report for sending |
| `caio all` | Check, scan and build everything — **the Monday-morning command** |

Add `--help` to any of them for the full option list.

Downloading data stays on separate, longer commands on purpose. Pulling touches
a network and needs credentials, and someone who asked for a report should not
get a network call they didn't ask for:

```bash
python3 -m pipeline.fetch.analytics   --data-dir data   # cost and usage
python3 -m pipeline.fetch.compliance  --data-dir data   # conversation content
python3 -m pipeline.fetch.consent     --data-dir data --show
```

### Prefer not to install?

Every command works without `pip install -e .` if you tell Python where the code
lives. `caio X` becomes `PYTHONPATH=plugins/100x-chief-ai-officer python3 -m pipeline.cli X`:

```bash
pip install -r requirements.txt
export PYTHONPATH=plugins/100x-chief-ai-officer

python3 -m pipeline.cli demo --out data-demo
python3 -m pipeline.cli all  --data-dir data-demo --out-dir _reports
```

### What's in this repository

```
plugins/100x-chief-ai-officer/
  pipeline/
    fetch/        the only modules that touch a network
    lake/         how downloaded data is stored and read back
    detectors/    nine families of sensitive-data pattern
    stages/       gap check, scan, metrics, findings ledger
    render/       compose, build, validate, deliver
    demo/         the fictional company
  skills/         five skills, for using this from Claude
  hooks/          the greeting shown the first time after installing
  references/     the data contract, the pipeline, the privacy rules
tools/scrubber/     the publication gate — run before anything ships
docs/mock-reports/  three example reports, fictional company
tests/              81 tests, run end to end with no credentials
```

### The six stages

One command runs them in order; each also runs alone.

| Stage | What it does |
|---|---|
| `x0` gap check | What can be answered, what's missing, which dates |
| `x1` pull | Download into local storage; content lands raw before parsing |
| `x2` metrics | Arithmetic over the data; no model reads anything here |
| `x3` classify | Something reads the flagged material and judges it |
| `x4` verify | An independent pass tries to break x3's conclusions |
| `x5` lock | The findings ledger, then render and validate |

### Going deeper

- [`docs/architecture.md`](docs/architecture.md) — four diagrams: what ships, what
  a person does with it, how data moves, and the rule separating a match from a
  finding.
- [`references/pipeline_spine.md`](plugins/100x-chief-ai-officer/references/pipeline_spine.md)
  — what each stage does and where to add things.
- [`references/data_contract.md`](plugins/100x-chief-ai-officer/references/data_contract.md)
  — every table and column of downloaded data.
- [`references/privacy_rules.md`](plugins/100x-chief-ai-officer/references/privacy_rules.md)
  — the privacy rules the code enforces, and why each exists.
- [`HANDOFF.md`](HANDOFF.md) — what's done and what's next.

### Requirements

Python 3.10 or newer, and `pip`. Two dependencies: `requests` for downloading,
`pandas` for the analysis. Storing data, the gap check, the metrics and the
report gates are all standard-library only — so the parts that have to run in a
locked-down environment have nothing to install.

One optional extra: `pip install anthropic`, needed only if you want a model to
do the reading in `caio judge --judge anthropic`.

### Status

All six stages are implemented and tested end to end against the fictional
company, with no credentials needed. 81 tests.

The reading passes ship with three judges: an offline stand-in for the demo and
for tests, a worksheet a person fills in, and a Claude model. The stand-in is
not a real reading, and every report built with it says so on its face.

### Licence

Apache-2.0. See [`LICENSE`](LICENSE), [`NOTICE`](NOTICE),
[`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md) and
[`PRIVACY.md`](PRIVACY.md).

Security issues: please follow [`SECURITY.md`](SECURITY.md) rather than opening a
public issue.
