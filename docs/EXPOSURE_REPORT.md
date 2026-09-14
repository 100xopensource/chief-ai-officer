# The Exposure Report, and reading people's conversations

[← back to the README](../README.md)

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

## Downloading the conversation text

```bash
caio pull content --data-dir data
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

To see what you consented to:

```bash
python3 -m pipeline.fetch.consent --data-dir data --show
```

## Nothing counts as a finding until it's been read twice

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

## Option A — a person does the reading

No network calls, nothing extra to install. This is two passes, because a human
is in the middle of it:

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

## Option B — a Claude model does the reading

One command, but it needs a one-off install and it sends the flagged passages to
a model:

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
