"""The judge a compliance reviewer actually is.

Writes one file listing every passage that needs reading, waits for a person to
fill in a verdict against each, then reads it back. That is the whole backend.

Why a file rather than a prompt loop
------------------------------------
Reviewing a hundred passages is not an interactive session. It happens across a
morning, in an office, with interruptions, and often by somebody who wants to
sort by category and do all the credential ones first. A file can be saved,
handed to a colleague, diffed, and attached to an audit record. A terminal
prompt cannot.

What the reviewer sees, and does not see
----------------------------------------
The worksheet shows the passage in full, because a judgement cannot be made
without reading it. It carries a loud handling notice for exactly that reason:
the worksheet is the one artifact in this pipeline that contains raw
conversation content, so it is written outside the lake, git-ignored, and meant
to be deleted once the verdicts are read back.

The reason field the reviewer writes goes into the permanent record, so the
worksheet says plainly: no values, no names, no filenames.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.judges.base import (
    SEVERITIES,
    VERDICTS,
    VERDICT_UNSURE,
    Candidate,
    Verdict,
)

NAME = "human-worksheet"

HEADER = """\
# Review worksheet — {count} passage(s) to judge

## HANDLING

This file contains raw conversation content from your organisation. It is the
only file this pipeline produces that does. Do not email it, do not commit it,
and delete it once the verdicts have been read back in.

## What to do

For each passage below, fill in the three fields in its `>>> VERDICT` line.

    verdict:   real | false_alarm | unsure
    severity:  high | internal | low        (only when verdict is real)
    reason:    one or two sentences

Answer three questions, in this order:

1. Is the matched thing what the pattern thought it was? Most matches are
   placeholders, test fixtures, documentation examples, or a number that merely
   has the right shape.
2. Does it matter in this context? A credential in a public code sample is not
   a credential in a support ticket.
3. Is there an owner and an action inside the organisation's control? If nobody
   can change anything about it, it is not a finding.

## Two rules

- **Never test a credential.** Not once, not "just to see if it is live". Judge
  liveness from context. Testing one is an unauthorised authentication attempt
  against somebody's system, and it turns a dead key into an incident.
- **Your reason must quote no values, no names, no email addresses and no
  filenames.** It goes into the permanent record and will be read by people who
  must not see them. Describe the shape and the context instead.

`false_alarm` is the most common answer and a good one — historically about 44%
of passages survive being read. `unsure` is better than a guess; an unsure
verdict is never published as a finding.

When you are done, read it back with:

    caio judge --data-dir <lake> --judge worksheet --worksheet {path}

---
"""

ENTRY = """
## {n}. {family_title}

- passage id: `{block_id}`
- where: {surface}
- date: {day}
{extra}
```
{text}
```

>>> VERDICT verdict= severity= reason=

---
"""

VERDICT_LINE = re.compile(
    r"^>>>\s*VERDICT\s+verdict=\s*(?P<verdict>\w*)\s*"
    r"severity=\s*(?P<severity>\w*)\s*"
    r"reason=\s*(?P<reason>.*)$",
    re.MULTILINE,
)
BLOCK_ID_LINE = re.compile(r"^- passage id: `(?P<block_id>[^`]+)`$", re.MULTILINE)

# The reviewer's reason goes into the permanent record, so it is held to the
# same rule as a published report.
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
FILENAME = re.compile(r"\b[\w -]+\.(?:docx|xlsx|pptx|pdf|csv|txt|png|jpg|zip)\b", re.I)
LONG_DIGITS = re.compile(r"(?<![\d,.])\d{5,}(?!\d|[,.]\d)")


class WorksheetNotReady(RuntimeError):
    """The worksheet has been written and is waiting for a person."""


class WorksheetJudge:
    """Writes a worksheet on the first run; reads verdicts back on the second."""

    name = NAME

    def __init__(self, worksheet: str | Path = "review-worksheet.md", **_ignored) -> None:
        self.path = Path(worksheet)
        self._pending: list[Candidate] = []
        self._answers: dict[str, Verdict] = {}
        if self.path.exists():
            self._answers = self._read_back(self.path)

    def judge_one(self, candidate: Candidate) -> Verdict:
        answer = self._answers.get(candidate.block_id)
        if answer is not None:
            return answer
        # Not answered yet. Collect it for the worksheet and return nothing
        # publishable — an unread passage is never a finding.
        self._pending.append(candidate)
        return Verdict(
            block_id=candidate.block_id,
            verdict=VERDICT_UNSURE,
            reason="Not yet read. Waiting for a reviewer.",
            judged_by=self.name,
            extra={"awaiting_review": True},
        )

    def close(self) -> None:
        """Write the worksheet for anything still unanswered."""
        if not self._pending:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        parts = [HEADER.format(count=len(self._pending), path=self.path)]
        for index, candidate in enumerate(self._pending, start=1):
            extra = []
            if candidate.tool_name:
                extra.append(f"- tool: {candidate.tool_name}")
            if candidate.integration:
                extra.append(f"- data connection: {candidate.integration}")
            if candidate.truncated:
                extra.append("- **cut off at the source** — text past the cut is missing")
            parts.append(ENTRY.format(
                n=index,
                family_title=candidate.family_title,
                block_id=candidate.block_id,
                surface=candidate.surface,
                day=candidate.day,
                extra=("\n".join(extra) + "\n") if extra else "",
                text=candidate.text[:6000],
            ))
        self.path.write_text("".join(parts), encoding="utf-8")

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # ------------------------------------------------------------------

    @staticmethod
    def _read_back(path: Path) -> dict[str, Verdict]:
        text = path.read_text(encoding="utf-8")
        ids = [m.group("block_id") for m in BLOCK_ID_LINE.finditer(text)]
        lines = list(VERDICT_LINE.finditer(text))

        answers: dict[str, Verdict] = {}
        for block_id, match in zip(ids, lines):
            raw_verdict = (match.group("verdict") or "").strip().lower()
            if raw_verdict not in VERDICTS:
                continue  # left blank, or mistyped: treat as not yet answered
            severity = (match.group("severity") or "").strip().lower()
            reason = (match.group("reason") or "").strip()
            answers[block_id] = Verdict(
                block_id=block_id,
                verdict=raw_verdict,
                reason=_scrub(reason) or "No reason given.",
                judged_by=NAME,
                severity=severity if severity in SEVERITIES else None,
                extra={"read_by_a_person": True},
            )
        return answers


def _scrub(reason: str) -> str:
    """Strip anything the reviewer should not have put in a stored reason.

    Redacting rather than rejecting: a reviewer who has read fifty passages and
    typed a filename into one of them should not lose the other forty-nine, and
    a rejected worksheet tends to come back with the reasons deleted instead.
    """
    reason = EMAIL.sub("[email removed]", reason)
    reason = FILENAME.sub("[filename removed]", reason)
    reason = LONG_DIGITS.sub("[value removed]", reason)
    return reason.strip()
