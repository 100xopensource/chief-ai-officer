---
name: caio-deliver-report
description: Package a built 100x CAIO report for sending to someone, with a cover note, a manifest and checksums. Use when asked to send, share, email, hand off, or deliver a report, to prepare it for a board pack or an exec meeting, or to produce a named edition for a compliance reviewer.
---

# Delivering a report

## The command

```bash
python3 -m pipeline.render.deliver _reports/exposure-report-2026-07-12.html \
        --out-dir deliveries --to "Head of Compliance" --from "CAIO office"
```

Or as part of the run:

```bash
caio all --data-dir data --deliver --to "Exec team"
```

## What you get

```
deliveries/2026-07-12/
  exposure-report-2026-07-12.html    the report, byte for byte unchanged
  exposure-report-2026-07-12.json    the data it renders itself from
  MANIFEST-exposure.json             what shipped, and every gate it passed
  COVER-NOTE-exposure.md             written to be pasted into an email as-is
  CHECKSUMS.txt                      so the recipient can prove it is intact
```

The cover note already says what the report is, the one thing worth reading
first, and what the report cannot see. Send it unedited unless you have a
reason. The last part matters most: a reader who discovers a blind spot on
their own stops trusting everything else on the page.

## It will refuse, and that is the feature

If a gate fails, nothing is written and there is no override flag. A report you
had to bypass a privacy check to send is not a report, it is an incident.

When it refuses, read the gate:

| Gate | What to do |
|---|---|
| `privacy` | Something identifying is in the report. Do not hand-edit the HTML; fix the data and rebuild. |
| `small_group` | A group of one to four is rendered as a digit. It must read "fewer than five". |
| `jargon` | Internal vocabulary reached the reader. Say the plain thing. |
| `caveats` | A caveat that travels with a number was dropped. Put it back; it is what makes the number true. |
| `completeness` | The data is missing a key the page reads. It would render as a blank page. |

## Shareable versus named

**Shareable** is the default and is what almost everyone should get: counts,
categories and dates. No names, no email addresses, no filenames, no
conversation content.

**Named** (`--edition named`) permits identifying detail. Every other gate still
applies. Use it only for a reviewer who needs to act on specific cases, only
when someone has asked for it, and never as a convenience.

A named edition is stamped as one in its manifest and its cover note, so whoever
finds the file in six months can tell what they are holding. Never send a named
edition to a distribution list, and never attach one to a calendar invite.

## Before you press send

- Is this the edition the recipient should have? Default to shareable.
- Does the cover note's "if you only do one thing" match what you would say out
  loud? If not, the report's ordering is wrong — fix the report, not the note.
- If a finding is marked as recurring for several weeks, say that in the
  covering message. "Third week running" is the sentence that gets acted on.
