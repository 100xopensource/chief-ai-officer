#!/usr/bin/env python3
"""Detector families. Every output is a lead. None of them is a finding.

The distinction is the whole discipline of this project, so it is worth being
blunt about it: a regular expression that matches the shape of a credential has
found the shape of a credential. Whether it found a credential is a question
only a reader can answer. Historically about 44% of what these families flag
survives that reading, and some families have scored 0% — so publishing a scan
count as a result would routinely overstate reality by a factor of two, and
occasionally by infinity.

Everything here therefore emits `(address, pattern_id, family)` and stops. The
classification stage reads a bounded sample and judges; the verification stage
tries to break the judgement. Only what survives both gets published, as a
floor.

Detectors are versioned with the run
------------------------------------
Every scan saves the exact detector definitions it used into
`_reports/detectors/<date>/`. This is not bureaucracy: a previous pass lost its
patterns and two published numbers became permanently unreproducible. If a
number cannot be recomputed, it cannot be defended, and a number that cannot be
defended should not have shipped.

Tuning notes that cost something to learn
-----------------------------------------
· **Entropy checks only inside an assignment context.** Free-floating
  high-entropy string detection over tens of millions of characters of tool
  output returns essentially everything. It must be anchored to `key=value`,
  `Authorization:`, or a known prefix.

· **Luhn validity does not mean payment card.** Order numbers, SKUs, and
  account identifiers in retail and finance data pass the Luhn check constantly.
  On one real corpus this family's precision was zero. It is retained, because
  a real card would matter, but it is quarantined behind a context requirement
  and its output should be assumed wrong until read.

· **Check when a field was born before trusting any trend.** A dimension that
  first appears mid-window produces spectacular fake growth. `first_seen()`
  exists for exactly this and should be consulted before any comparison across
  a dimension.

· **Default filenames are not evidence of anything.** Auto-generated names
  (`Pasted…`, `Untitled…`, `image.png`) once produced a confident finding about
  dozens of people sharing a document. They were sharing a default.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator

DETECTOR_VERSION = "1.0.0"


@dataclass(frozen=True)
class Pattern:
    id: str
    regex: re.Pattern
    note: str = ""
    # Precision this pattern has historically shown when read. Purely
    # informational, and deliberately visible: a reader triaging leads should
    # know which piles are mostly noise before spending their attention.
    expect_precision: str = "unknown"


@dataclass
class Family:
    id: str
    title: str
    why: str
    patterns: list[Pattern] = field(default_factory=list)
    surfaces: tuple[str, ...] = ("typed_prompt", "tool_input", "tool_output", "attachment")

    def scan(self, text: str) -> Iterator[tuple[str, str]]:
        """Yield (pattern_id, matched_span) for each hit. Spans are never published."""
        for pattern in self.patterns:
            for match in pattern.regex.finditer(text):
                yield pattern.id, match.group(0)


# --------------------------------------------------------------------------
# D1 — credentials and secrets
# --------------------------------------------------------------------------

D1 = Family(
    id="D1",
    title="Credentials and secrets",
    why="A key, token, or password sitting in conversation history is recoverable by "
        "anyone who can read that history, and stays valid until somebody rotates it.",
    patterns=[
        Pattern("D1_KEY_PREFIX",
                re.compile(r"\b(?:AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
                           r"|AIza[0-9A-Za-z_-]{20,}|sk-ant-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9]{32,})\b"),
                "Vendor key prefixes. The highest-precision family here: these shapes "
                "are rarely accidental.",
                "high"),
        Pattern("D1_JWT",
                re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
                "A JSON Web Token. Often short-lived, which changes the remedy but not "
                "the fact that it was captured.",
                "high"),
        Pattern("D1_PRIVATE_KEY",
                re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----"),
                "A private key block. Unambiguous.",
                "high"),
        Pattern("D1_ASSIGNED",
                re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token"
                           r"|auth[_-]?token|client[_-]?secret)\b\s*[=:]\s*[\"']?([^\s\"'&,;]{6,})"),
                "A secret assigned to a named variable. Fires heavily on placeholders "
                "(REDACTED, xxx, your-key-here) — read before believing.",
                "low"),
        Pattern("D1_BEARER",
                re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._-]{16,}"),
                "An Authorization header captured verbatim.",
                "medium"),
        Pattern("D1_CONNECTION_STRING",
                re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:@/]+:[^\s:@/]+@[^\s/]+"),
                "Credentials embedded in a connection string or URL.",
                "medium"),
        Pattern("D1_SIGNED_URL",
                re.compile(r"https?://[^\s\"'<>]+[?&](?:token|sig|signature|access_token|X-Amz-Signature)"
                           r"=[A-Za-z0-9._%-]{16,}"),
                "A signed access link. The pattern behind the largest finding this "
                "project has produced: connectors returning live result URLs into "
                "tool output. Describe these accurately — they are time-limited "
                "access links, not passwords, and the fix differs.",
                "high"),
    ],
)


# --------------------------------------------------------------------------
# D2 — structured personal information
# --------------------------------------------------------------------------

D2 = Family(
    id="D2",
    title="Structured personal information",
    why="Identifiers that single out a specific person carry obligations regardless of "
        "how they arrived.",
    patterns=[
        Pattern("D2_SSN",
                re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
                "US Social Security Number, with the reserved ranges excluded.",
                "medium"),
        Pattern("D2_CARD_LUHN",
                re.compile(r"\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|3[47]\d{13}|6(?:011|5\d{2})\d{12})\b"),
                "A payment-card shape that also passes a Luhn check. Precision on real "
                "business data has been ZERO: order numbers and SKUs pass Luhn "
                "routinely. Requires a payment context before it means anything.",
                "zero-without-context"),
        Pattern("D2_ROUTING",
                re.compile(r"(?i)\b(?:routing|aba)\s*(?:number|no\.?|#)?\s*[:=]?\s*\d{9}\b"),
                "A bank routing number in an explicit context.",
                "medium"),
        Pattern("D2_PASSPORT",
                re.compile(r"(?i)\bpassport\s*(?:number|no\.?|#)?\s*[:=]?\s*[A-Z0-9]{6,9}\b"),
                "A passport number in an explicit context.",
                "medium"),
        Pattern("D2_DOB",
                re.compile(r"(?i)\b(?:date of birth|dob|born on)\b\s*[:=]?\s*"
                           r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}"),
                "A date of birth in an explicit context. On its own this is weak; it "
                "matters when it sits beside a name.",
                "low"),
    ],
)


# --------------------------------------------------------------------------
# D3 — compensation
# --------------------------------------------------------------------------

D3 = Family(
    id="D3",
    title="Compensation",
    why="Pay attached to an identifiable person is among the most sensitive material an "
        "employer holds, and it moves around in spreadsheets constantly.",
    patterns=[
        Pattern("D3_COMP_AMOUNT",
                re.compile(r"(?i)\b(?:salary|base pay|base salary|bonus|equity|RSU|vesting|"
                           r"severance|offer)\b[^.\n]{0,60}?[$€£]\s?\d[\d,]{3,}"),
                "A compensation term within a short distance of an amount. Proximity "
                "matters: the two must be in the same clause, not the same document.",
                "medium"),
        Pattern("D3_COMP_TABLE",
                re.compile(r"(?i)\b(?:employee_id|emp_id|full_name|last_name)\b[^\n]{0,80}?"
                           r"\b(?:salary|base_pay|annual_comp|total_comp)\b"),
                "A record shape carrying both an identity column and a pay column — a "
                "payroll extract rather than a discussion.",
                "high"),
    ],
)


# --------------------------------------------------------------------------
# D4 — material nonpublic information
# --------------------------------------------------------------------------

D4 = Family(
    id="D4",
    title="Material nonpublic information",
    why="Unreleased financial and transaction information carries trading restrictions, "
        "and the people handling it are usually not thinking about where it is stored.",
    patterns=[
        Pattern("D4_TRANSACTION",
                re.compile(r"(?i)\b(?:letter of intent|term sheet|due diligence|data ?room|"
                           r"earn[- ]?out|purchase agreement|definitive agreement)\b"),
                "Transaction vocabulary. Very noisy: about two in five survive reading, "
                "because these terms appear constantly in templates, training material, "
                "and hypotheticals.",
                "low"),
        Pattern("D4_SECURITIES",
                re.compile(r"(?i)\b(?:10-K|10-Q|8-K|earnings guidance|blackout period|"
                           r"material nonpublic|insider trading)\b"),
                "Securities-reporting vocabulary.",
                "medium"),
        Pattern("D4_DEBT",
                re.compile(r"(?i)\b(?:covenant|indenture|default notice|forbearance|"
                           r"waiver letter|leverage ratio)\b"),
                "Debt and covenant vocabulary.",
                "low"),
        Pattern("D4_BOARD",
                re.compile(r"(?i)\bboard\s+(?:deck|minutes|resolution|pack|materials)\b"),
                "Board material.",
                "medium"),
    ],
)


# --------------------------------------------------------------------------
# D5 — employee relations
# --------------------------------------------------------------------------

D5 = Family(
    id="D5",
    title="Employee relations",
    why="Individual case material — investigations, performance action, medical "
        "accommodation — is confidential to the person it concerns.",
    patterns=[
        Pattern("D5_CASE",
                re.compile(r"(?i)\b(?:performance improvement plan|grievance|disciplinary|"
                           r"workplace investigation|termination letter|constructive dismissal)\b"),
                "Case vocabulary. Means little without an identifiable individual "
                "attached, which is the reading stage's job to establish.",
                "medium"),
        Pattern("D5_REDUCTION",
                re.compile(r"(?i)\b(?:reduction in force|layoff|redundanc|WARN notice|"
                           r"severance package)\b"),
                "Workforce reduction vocabulary — often forward-looking and therefore "
                "nonpublic as well as personal.",
                "medium"),
        Pattern("D5_MEDICAL",
                re.compile(r"(?i)\b(?:accommodation request|medical leave|fitness for duty|"
                           r"disability|occupational health)\b"),
                "Health-related employment material. Report this in plain words. Do not "
                "use regulatory terms — whether a given regime applies is a legal "
                "determination, not a scanning result.",
                "medium"),
    ],
)


# --------------------------------------------------------------------------
# D6 — customer and patient records
# --------------------------------------------------------------------------

D6 = Family(
    id="D6",
    title="Customer and patient records",
    why="This is where the largest volumes land. A person asks an ordinary question and "
        "a connected system answers with rows of identifiable records.",
    surfaces=("tool_output",),
    patterns=[
        Pattern("D6_RECORD_SHAPE",
                re.compile(r"(?i)\b(?:first_name|last_name|full_name|patient_id|customer_id|"
                           r"member_id|mrn)\b[^\n]{0,120}?\b(?:dob|date_of_birth|phone|email|"
                           r"address|diagnosis|medication)\b"),
                "A record layout carrying an identity column beside a personal-detail "
                "column. Scoped to tool output because that is where extracts arrive; "
                "the same words in a prompt are usually somebody describing a schema.",
                "high"),
        Pattern("D6_CLINICAL",
                re.compile(r"(?i)\b(?:diagnosis|dosage|prescription|medical record number|"
                           r"treatment plan)\b"),
                "Clinical vocabulary. Describe findings as identifiable medical "
                "information relating to named individuals; never assert a regulatory "
                "category.",
                "low"),
        Pattern("D6_BULK_CONTACT",
                re.compile(r"(?:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}[\s,;|]+){10,}"),
                "Ten or more contact addresses in sequence — a list extract rather than "
                "a mention.",
                "high"),
    ],
)


# --------------------------------------------------------------------------
# D7 — attachment names
# --------------------------------------------------------------------------

DEFAULT_NAME = re.compile(
    r"(?i)^(?:pasted|untitled|image|screenshot|document|download|export|new |tmp|temp)"
)

D7 = Family(
    id="D7",
    title="Attachment names",
    why="File bodies are not retrievable, but names are, and they are close to always "
        "the human original. A file named like an offer letter is a signal.",
    surfaces=("attachment",),
    patterns=[
        Pattern("D7_SENSITIVE_NAME",
                re.compile(r"(?i)\b(?:offer[_ -]?letter|salary|comp(?:ensation)?[_ -]?(?:review|plan)|"
                           r"payroll|termination|severance|board[_ -]?(?:deck|pack)|"
                           r"due[_ -]?diligence|patient|census|roster|passport|tax)\b"),
                "A filename suggesting sensitive contents. Must be filtered through "
                "the default-name guard before counting: auto-generated names once "
                "produced a confident finding about dozens of people sharing a "
                "document they had not shared.",
                "medium"),
    ],
)


def is_default_name(name: str | None) -> bool:
    """Whether a filename is an auto-generated default rather than a chosen one."""
    return bool(name) and bool(DEFAULT_NAME.match(name.strip()))


# --------------------------------------------------------------------------
# D8 — governance signals, computed rather than matched
# --------------------------------------------------------------------------

D8 = Family(
    id="D8",
    title="Unmanaged connections",
    why="A connection nobody can name is a connection nobody is reviewing. This one is "
        "computed from connector metadata rather than matched against text.",
    surfaces=(),
    patterns=[],
)

UUID_LIKE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def is_unnamed_connector(name: str | None) -> bool:
    """A connector that presents as an identifier rather than a product name."""
    if not name:
        return True
    return bool(UUID_LIKE.match(name.strip()))


D9 = Family(
    id="D9",
    title="Export integrity",
    why="Conversations that claim messages but export none, and conversations with a "
        "title and nothing else, are a question about whether the export can be "
        "relied on — which matters before anyone relies on one.",
    surfaces=(),
    patterns=[],
)


ALL_FAMILIES = [D1, D2, D3, D4, D5, D6, D7, D8, D9]
TEXT_FAMILIES = [f for f in ALL_FAMILIES if f.patterns]

FAMILIES_BY_ID = {f.id: f for f in ALL_FAMILIES}


# --------------------------------------------------------------------------
# supporting analysis
# --------------------------------------------------------------------------

def shannon_entropy(value: str) -> float:
    """Bits per character. Used only inside an assignment context — never free-floating."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


PLACEHOLDER = re.compile(
    r"(?i)^(?:x{3,}|\*{3,}|\.{3,}|redacted|removed|hidden|changeme|your[_-]?\w+|"
    r"<[^>]+>|\{\{?[^}]+\}?\}|example|sample|test|dummy|placeholder|none|null|todo)$"
)


def looks_like_placeholder(value: str) -> bool:
    """Whether a captured secret is obviously not a real one.

    A cheap pre-filter, not a verdict. It removes the most obvious noise before
    a person spends attention on the pile; anything it does not remove still
    needs reading.
    """
    stripped = value.strip().strip("\"'")
    if PLACEHOLDER.match(stripped):
        return True
    if len(stripped) < 8:
        return True
    # Real secrets are not one repeated character, and are not a single word.
    if len(set(stripped)) <= 2:
        return True
    if shannon_entropy(stripped) < 2.0:
        return True
    return False


def fingerprint(value: str) -> str:
    """A one-way, non-reversible handle for a matched value.

    Lets the pipeline deduplicate the same secret across many conversations, and
    lets a report say "this one value appeared 400 times", without the value
    itself ever being written down. The raw value never leaves the scan.
    """
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:16]


def first_seen(rows: Iterable[dict[str, Any]], dimension: str, day_key: str = "day") -> dict[str, str]:
    """The earliest day each value of a dimension appears.

    Consult this before comparing anything across that dimension. A value born
    mid-window shows enormous growth purely because it did not exist earlier,
    and that artefact has previously been published as a finding before being
    caught.
    """
    earliest: dict[str, str] = {}
    for row in rows:
        value = row.get(dimension)
        day = row.get(day_key)
        if not value or not day:
            continue
        key = str(value)
        if key not in earliest or str(day) < earliest[key]:
            earliest[key] = str(day)
    return earliest


# --------------------------------------------------------------------------
# persistence — a scan that cannot be reproduced cannot be defended
# --------------------------------------------------------------------------

def save_definitions(root: Path, run_date: date | None = None) -> Path:
    """Write the exact detector definitions used by this run.

    Called at scan time, not at publication time. The gap between those two
    moments is where a previous pass lost its patterns permanently.
    """
    stamp = (run_date or date.today()).isoformat()
    out = Path(root) / "_reports" / "detectors" / stamp
    out.mkdir(parents=True, exist_ok=True)

    definitions = {
        "detector_version": DETECTOR_VERSION,
        "saved_for_run": stamp,
        "families": [
            {
                "id": family.id,
                "title": family.title,
                "why": family.why,
                "surfaces": list(family.surfaces),
                "patterns": [
                    {
                        "id": p.id,
                        "regex": p.regex.pattern,
                        "flags": p.regex.flags,
                        "note": p.note,
                        "expect_precision": p.expect_precision,
                    }
                    for p in family.patterns
                ],
            }
            for family in ALL_FAMILIES
        ],
        "note": "These are the exact definitions used for the run named above. Every "
                "published count must be reproducible from this file. Do not edit it "
                "after the fact — start a new dated folder instead.",
    }

    path = out / "definitions.json"
    path.write_text(json.dumps(definitions, indent=2) + "\n", encoding="utf-8")

    readme = out / "README.md"
    readme.write_text(
        f"# Detector definitions — {stamp}\n\n"
        f"Version {DETECTOR_VERSION}. "
        f"{sum(len(f.patterns) for f in ALL_FAMILIES)} patterns across "
        f"{len(ALL_FAMILIES)} families.\n\n"
        "Saved at scan time so every number produced by this run can be recomputed "
        "later. A number that cannot be recomputed cannot be defended, and should not "
        "have been published.\n\n"
        "Every count these patterns produce is a **lead**. Leads become findings only "
        "after a person reads the material and confirms it, and only what survives an "
        "independent second pass is published — as a floor.\n\n"
        + "\n".join(
            f"- **{f.id} {f.title}** — {len(f.patterns)} pattern(s). {f.why}"
            for f in ALL_FAMILIES
        )
        + "\n",
        encoding="utf-8",
    )
    return path
