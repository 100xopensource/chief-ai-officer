# Getting started

[← back to the README](../README.md)

## What you need

- **Claude Cowork, or Claude Code.** Either one; the plugin is the same.
- **A browser**, to open the reports.
- **One API key**, when you are ready to use your own firm's data. Not needed to
  look at an example first.

Nothing to install. Python is already on your machine, and everything except
downloading your own data runs on the standard library.

## Install

### In Claude Cowork

1. Open Claude Cowork.
2. Select **Customize → Plugins**.
3. Select **Add**, then **Add marketplace**.
4. Paste `https://github.com/100xopensource/chief-ai-officer`.
5. Pick **100x Chief AI Officer** from the list and install it.
6. Start a new conversation.

### In Claude Code

```
/plugin marketplace add 100xopensource/chief-ai-officer
/plugin install 100x-chief-ai-officer@100x-chief-ai-officer
```

Then start a new conversation.

**Step 6 is not a formality.** A plugin installed part-way through a session is
not picked up until a new one begins: the skills do not appear and the plugin
list comes back empty, even though every file is already on disk. It looks
exactly like a failed install. It is not one, and reinstalling will not help.

## Look at an example first

Start a new chat and say **"Show me an example Chief AI Officer report."**

Claude invents a fictional 112-person company — 14 weeks of history, about
$11,700 of pretend spend, 900 conversations — and builds all three reports from
it. Everything in them is invented: the company, the people, the numbers. It
exists so you can see the shape of the thing before deciding whether to point it
at anything real.

No API key, no permission from anyone, no real data.

Three finished examples are also committed at [`mock-reports/`](mock-reports/) if
you would rather just open a file. Download one — GitHub shows you a page's
source code rather than the page itself.

## Get a key

You need **one API key** from whoever administers your firm's Claude account.
Only an account owner or admin can create one.

| The key you are given | What it unlocks |
|---|---|
| A **Compliance API key** | All three reports |
| An **Admin API key** | The Waste Ledger and the Value X-Ray |

One key is usually all there is, and it is usually enough — a Compliance key
covers the cost and usage pulls as well as the conversation pull. An Admin key
alone still gets you two of the three reports; the third reports itself as
unbuildable, with the reason.

An Admin key returns dollars, token counts and per-person usage totals, and
cannot read anybody's messages. A Compliance key can. Treat a Compliance key like
production database credentials, because that is effectively what it is: never in
a spreadsheet, a chat message, or a script you email to someone.

## Set it up

Say **"Set up Chief AI Officer with my API key."**

Claude asks where to put the key, checks what it can reach, and reports which
reports your account can produce — before pulling anything. Keys live in your
shell environment or a `.env` file in the working folder; `.env` is already in
`.gitignore`, so it cannot be committed by accident.

Downloading is always its own named step. Asking for a report never triggers a
network call you did not ask for.

## Your first real run

Say **"Build this week's Chief AI Officer reports."**

Claude pulls your cost and usage data, then writes the three reports and tells
you where they are. The first pull fetches your history and takes a few minutes;
later runs only fetch what is new and take seconds.

Success means:

- three report files open normally in a browser;
- each one says how many findings it carries and that its integrity checks
  passed;
- anything that could not be built says so, and names what is missing;
- nothing was uploaded anywhere.

Ask for this every Monday morning and you have a standing weekly reporting pack.
The reports are built around a week-long window, compared against the previous
four weeks averaged.

## Tell it the things only you know

Two facts about your organisation are not in the data and never will be:

- **which accounts are service accounts** rather than people, and
- **what a connection that reports as a bare identifier actually is.**

Say so once — *"these three user IDs are service accounts, not people"*, or
*"that identifier is Salesforce"* — and every report from then on honours it.

The first matters more than it looks. Without it, every service account in your
directory is priced as a seat somebody holds, which inflates the single number
the Waste Ledger exists to produce, every week, in the direction that looks best.

Both decisions are stored next to the data they describe, so copying this tool to
another business unit does not carry the old decisions with it. File formats are
in [the command reference](REFERENCE.md#decisions-only-you-can-make).

## Before the Exposure Report reads anything

The Exposure Report is the one that reads what people actually wrote. It will not
touch that data until a consent record exists naming an accountable person, and
it will ask for one the first time.

Nothing it finds counts as a finding until it has been read twice — once by a
judge, once by an independent verifier who is never told what the first
concluded. You choose who reads: a person working through a worksheet, with no
network calls at all, or a Claude model.

Full walkthrough: [the Exposure Report](EXPOSURE_REPORT.md).

## Send a report to someone

Say **"Package the Exposure Report for our Head of Compliance."**

You get a folder ready to send: the report, the data it was built from, a cover
note written to be pasted into an email unedited, the list of checks the report
passed, and checksums so the recipient can prove the file was not altered.

Send reports as attachments. A report pasted into an email body arrives as a
blank page — the page draws itself when a browser opens it, and a preview pane
does not.

If a report fails any of its safety checks, nothing is packaged, and there is no
override flag.

## What to do when it will not run

The README carries [the full
table](../README.md#when-something-goes-wrong). The three that catch most
people:

1. **The skills do not appear.** The plugin was installed part-way through a
   session. Start a new conversation.
2. **`No module named 'requests'` on the first real pull.** Downloading needs
   one library; building reports does not. `pip install requests`.
3. **A figure reads `—` or "cannot be measured".** Deliberate, and it must not be
   read as zero. The data does not carry what that figure needs, and the report
   names the reason.
