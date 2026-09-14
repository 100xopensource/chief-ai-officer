#!/usr/bin/env python3
"""Validation gates. A report that fails one of these does not ship.

Every gate here exists because something got past a human reviewer. They are
cheap to run and they run on the rendered artifact, not on the ledger that
claims what the artifact contains — a worker's own account of its output is
self-attestation, and self-attestation is exactly what has failed before.

The gates, and what each one caught
-----------------------------------
privacy      Names, emails, filenames, or quoted content reaching a shareable
             report. The reason this project keeps records rank-only and adds
             names at render time, on request, and never by default.

small_group  A person-group of one to four rendered as a digit. "3 people" in a
             112-seat company is close to naming them. Under five is always
             textualised as "fewer than five".

jargon       Internal codes reaching a reader: week numbers, stage ids, finding
             ids, pipeline vocabulary. The audience is a director with three
             minutes; every code they have to decode is a sentence they skip.

acronyms     Unglossed acronyms. A separate gate from jargon because a new code
             family appeared once (E-numbers) that the banned-word list did not
             cover, and nobody noticed until it shipped.

caveats      A report that quietly drops the caveats travelling with its
             numbers: floors rather than estimates, the restatement window, the
             payload truncation, the scope wall. These get cut in rewrites
             because they read as hedging. They are not hedging; they are what
             makes the numbers true.

arithmetic   Category counts that do not sum to their stated total. A published
             report once had five items floating in prose outside its own line
             items, and a badge claiming a severity its contents did not all
             share.

completeness Every key the page's own script reads must be present in the
             data. Added after a report passed every other gate here and still
             rendered as a blank page, because the script threw on the first
             key that was missing.

structure    The embedded render block must parse, and must match its sidecar
             JSON byte for byte. Iteration is a JSON swap; if the swap and the
             file disagree, one of them is lying about what shipped.

zero_variance A whole column of zeros, or a stated total of zero that its own
             parts agree with, is refused. This is the newest gate and the one
             that would have caught the worst failure this project has had: a
             stat card reading "0% of connector calls failed" against a source
             that publishes no failure data, and a "most-used capabilities"
             ranking sorted by a key that was zero for every row. Both passed
             every other gate here, and the reconciliation block proved 0 == 0.

What these gates do not check
-----------------------------
Whether the numbers are right. Every gate here is a check on form: the document
is well-formed, it leaks nothing, its parts sum to its totals, its caveats
survived the rewrite. None of them verifies that a figure was computed from a
field that exists, or that the field means what the code thinks it means.

That distinction cost this project a pair of confidently wrong reports, because
"12 gates passed" was read — including by the person who wrote it — as "the
numbers are right". So the summary line says what it means: integrity checks
passed, numbers not independently verified. The shape check that does test
whether a source field exists lives at ingest, in `pipeline.lake.schema`, and
runs from `caio check`.

Usage
-----
    python3 -m pipeline.render.validate report.html
    python3 -m pipeline.render.validate report.html --json-sidecar block.json
    python3 -m pipeline.render.validate report.html --edition named
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RENDER_BLOCK_RE = re.compile(
    r'<script[^>]*id=["\']render-block["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")

# Acronyms a business reader already knows, or that the report glosses on first
# use. Anything else must be spelled out.
ALLOWED_ACRONYMS = {
    "AI", "API", "CEO", "CFO", "CIO", "CISO", "CTO", "HR", "IT", "PDF", "SSO",
    "US", "USD", "PII", "SSN", "MNPI", "FAQ", "URL", "CSV", "HTML", "SQL",
    "CAIO", "MCP", "SOP", "KPI", "OK", "ID", "PM", "AM", "Q1", "Q2", "Q3", "Q4",
}

# Pipeline vocabulary. Correct in a ledger, wrong in a reader's hands.
JARGON_PATTERNS = [
    (r"\bW\d{1,2}\b", "week codes like W28 — use calendar dates"),
    (r"\bX\d\b", "stage ids like X3 — readers do not know the pipeline"),
    (r"\b(?:EL|WL|VL)-\d+\b", "internal finding ids — describe the finding"),
    (r"\bD\d\b(?!\s*[a-z])", "detector ids like D1"),
    (r"\bNET\b|\bGROSS\b", "netting vocabulary — say what is included"),
    (r"\best[- ]tok\b", "est-tok — say 'estimated tokens' or drop it"),
    (r"\bprovisional\b", "provisional — say the numbers can still change, plainly"),
    (r"\brung\b", "rung — pipeline vocabulary"),
    (r"\bblock(?:s)?\b(?!\s*(?:quote|chain))", "blocks — pipeline vocabulary, not reader-facing"),
    (r"\bhand[- ]fed\b", "hand-fed — say 'pasted in by hand'"),
    (r"\bhusk(?:s)?\b", "husks — say 'chats that export with no messages'"),
    (r"\blead(?:s)?\b(?!\s+(?:to|the|us|them))",
     "leads — this pipeline's word for an unread match. Say 'candidates' or "
     "'matches'; a reader hears 'sales leads'"),
    (r"\bdefB\b|\bdef[- ]?B\b", "definition shorthand"),
]

# Phrases that carry a caveat. At least one from each required group must
# survive into the reader layer.
CAVEAT_GROUPS = {
    "floor": [r"\bat least\b", r"\bfloor\b", r"\bminimum\b", r"\bno fewer than\b"],
    "bounds": [r"\bupper bound", r"\bbound(?:s)?\b", r"not (?:a )?promised",
               r"\brather than a (?:settled|promised)"],
    "restatement": [r"can (?:still )?(?:shift|change|be revised)", r"\brevise",
                    r"finali[sz]", r"not final", r"close to final"],
    "verification": [r"\bread\b.*\bconfirm", r"we read", r"double[- ]check",
                     r"\bverif", r"recomputed", r"independent"],
    "truncation": [r"about 10,000 characters", r"\btruncat", r"cut (?:off )?at the source",
                   r"past the cut"],
    "scope_wall": [r"cannot see", r"can(?:'|\u2019)t see", r"not visible", r"invisible",
                   r"we can see the (?:actual )?work behind"],
}

# What each report must not drop. A cost report states bounds; an exposure
# report states floors, what was cut at the source, and what it cannot see at
# all. Requiring the wrong set trains people to add words that are not true.
CAVEAT_REQUIREMENTS = {
    "waste": ["bounds", "restatement", "verification"],
    "value": ["restatement", "verification", "scope_wall"],
    "exposure": ["floor", "verification", "truncation", "scope_wall"],
}
DEFAULT_CAVEATS = ["restatement", "verification"]

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
FILENAME_RE = re.compile(r"\b[\w -]+\.(?:docx|xlsx|pptx|pdf|csv|txt|png|jpg|zip)\b", re.I)
# A bare run of five or more digits: the shape a matched value has when it
# escapes into prose. The trailing guard rejects only a digit, or a comma or
# point that is itself followed by a digit — so 1,227 and 12.5 are numbers and
# pass, while a value ending a sentence does not. The earlier version excluded
# any trailing point, which let through the single most likely way a leak
# actually appears: at the end of a sentence.
LONG_DIGITS_RE = re.compile(r"(?<![\d,.])\d{5,}(?!\d|[,.]\d)")
SMALL_GROUP_RE = re.compile(
    r"\b([1-4])\s+(?:people|users|users?|persons?|employees|individuals|accounts|staff)\b",
    re.I,
)


@dataclass
class Result:
    passed: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)

    def ok(self, gate: str) -> None:
        self.passed.append(gate)

    def fail(self, gate: str, detail: str) -> None:
        self.failures.append((gate, detail))

    def warn(self, gate: str, detail: str) -> None:
        self.warnings.append((gate, detail))


COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _block_match(html: str) -> re.Match[str] | None:
    """Locate the render block, ignoring any mention of it in a comment.

    These templates document their own reuse instructions in a header comment
    that names the tag, so a naive search finds the prose before the data.
    Comments are stripped first (with their length preserved, so offsets into
    the original file stay valid), then the first block whose contents actually
    parse as JSON wins.
    """
    stripped = COMMENT_RE.sub(lambda m: " " * len(m.group(0)), html)
    for match in RENDER_BLOCK_RE.finditer(stripped):
        try:
            json.loads(match.group(1).strip())
        except ValueError:
            continue
        return match
    return None


def extract_block(html: str) -> tuple[dict[str, Any] | None, str | None]:
    match = _block_match(html)
    if not match:
        if RENDER_BLOCK_RE.search(COMMENT_RE.sub("", html)):
            return None, "the render block is present but its contents are not valid JSON"
        return None, 'no <script id="render-block"> found'
    return json.loads(match.group(1).strip()), None


def reader_text(block: dict[str, Any]) -> str:
    """Every string a reader can see, flattened.

    Walks the render block rather than the HTML because the block is what a
    weekly rerun swaps; a gate that only read the markup would pass a report
    whose data had gone wrong. The markup is checked separately.
    """
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                # Keys that are addresses, not prose.
                if key in {"id", "ledger_ids", "appendix", "finding", "RENDER_BLOCK"}:
                    continue
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(block)
    return "\n".join(parts)


def prose_text(block: dict[str, Any]) -> str:
    """Only the sentences — the strings long enough to be prose rather than a label.

    "Gloss on first use" is a rule about writing, so it applies to writing. A
    masthead reading WASTE LEDGER, a status pill reading NEW, and a table cell
    reading USD are not unglossed acronyms, and flagging them trains people to
    ignore the gate. Forty characters is the line between a label and a sentence.
    """
    return "\n".join(part for part in reader_text(block).split("\n") if len(part) >= 40)


def markup_text(html: str) -> str:
    """Reader-visible strings living in the markup rather than the block.

    A JSON-only gate once passed a report whose page title and static section
    headings still carried the previous edition's wording. Anything a reader
    can see is in scope, wherever it lives.
    """
    body = COMMENT_RE.sub("", html)
    body = RENDER_BLOCK_RE.sub("", body)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.DOTALL)
    body = re.sub(r"<script.*?</script>", "", body, flags=re.DOTALL)
    title = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    text = TAG_RE.sub(" ", body)
    if title:
        text = f"{title.group(1)}\n{text}"
    return re.sub(r"\s+", " ", text)


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------

def gate_privacy(text: str, result: Result, edition: str) -> None:
    emails = EMAIL_RE.findall(text)
    if emails and edition != "named":
        result.fail("privacy", f"{len(emails)} email address(es) in a shareable report")
    elif emails:
        result.warn("privacy", f"{len(emails)} email(s) — permitted only in the named edition")
    else:
        result.ok("privacy/emails")

    filenames = FILENAME_RE.findall(text)
    if filenames:
        result.fail("privacy", f"filename(s) from the corpus: {len(filenames)} found")
    else:
        result.ok("privacy/filenames")

    digits = LONG_DIGITS_RE.findall(text)
    if digits:
        result.fail(
            "privacy",
            f"{len(digits)} run(s) of 5+ digits — a matched value may have leaked "
            "(thousands separators are fine; bare digit runs are not)",
        )
    else:
        result.ok("privacy/digits")

    for banned in ("PHI", "HIPAA"):
        if re.search(rf"\b{banned}\b", text):
            result.fail(
                "privacy",
                f"'{banned}' asserts a regulatory status this pipeline cannot establish. "
                "Say 'identifiable medical information relating to named individuals'.",
            )
    result.ok("privacy/regulatory-wording")


def gate_small_group(text: str, result: Result) -> None:
    hits = SMALL_GROUP_RE.findall(text)
    if hits:
        result.fail(
            "small_group",
            f"person-group(s) rendered as a digit 1-4: {sorted(set(hits))}. "
            "Under five is always 'fewer than five' — in a small org a count of "
            "three is close to a name.",
        )
    else:
        result.ok("small_group")


def gate_jargon(text: str, result: Result) -> None:
    found = []
    for pattern, explanation in JARGON_PATTERNS:
        if re.search(pattern, text, re.I):
            found.append(explanation)
    if found:
        result.fail("jargon", "; ".join(found))
    else:
        result.ok("jargon")


def gate_acronyms(text: str, result: Result) -> None:
    unknown = set()
    for token in re.findall(r"\b[A-Z]{2,5}\b", text):
        if token in ALLOWED_ACRONYMS:
            continue
        # Glossed at first use: "Something Long (SL)" or "SL (something long)".
        if re.search(rf"\(\s*{token}\s*\)", text) or re.search(rf"\b{token}\b\s*\(", text):
            continue
        unknown.add(token)
    if unknown:
        result.fail(
            "acronyms",
            f"unglossed: {sorted(unknown)}. Spell out at first use or add to the "
            "allowlist if a business reader genuinely knows it.",
        )
    else:
        result.ok("acronyms")


def gate_caveats(text: str, result: Result, report_kind: str | None) -> None:
    required = CAVEAT_REQUIREMENTS.get(report_kind or "", DEFAULT_CAVEATS)
    missing = [
        name for name in required
        if not any(re.search(p, text, re.I) for p in CAVEAT_GROUPS[name])
    ]
    if missing:
        result.fail(
            "caveats",
            f"missing: {missing}. These get cut in rewrites because they read as "
            "hedging. They are what makes the numbers honest.",
        )
    else:
        result.ok("caveats")


def gate_arithmetic(block: dict[str, Any], result: Result) -> None:
    """Check any stated total against the parts it claims to sum.

    A report is allowed to present a headline that differs from its own line
    items — that happens legitimately, when a category is counted one way for
    the reader and another for the ledger. What it is not allowed to do is
    differ silently. So a mismatch is only a failure when the report does not
    also explain it.
    """
    checks = block.get("reconciliation") or {}
    if not checks:
        result.warn(
            "arithmetic",
            "no reconciliation block. Every headline should ship with the "
            "arithmetic that produced it.",
        )
        return

    problems = []
    for label, spec in checks.items():
        if not isinstance(spec, dict):
            continue
        total = spec.get("total")
        parts = spec.get("parts") or []
        if total is None or not parts:
            continue
        summed = sum(p.get("value", 0) for p in parts if isinstance(p, dict))
        if abs(summed - total) > 1e-6 and not spec.get("explained"):
            problems.append(f"{label}: parts sum to {summed}, total says {total}, no explanation given")
    if problems:
        result.fail("arithmetic", "; ".join(problems))
    else:
        result.ok("arithmetic")


NUMERIC_CELL_RE = re.compile(r"^[-+]?[\d,]+(?:\.\d+)?%?$")


def _is_zero_cell(cell: Any) -> bool:
    """True for a cell that is numerically zero, however it was formatted."""
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        return float(cell) == 0.0
    if not isinstance(cell, str):
        return False
    text = cell.strip().lstrip("$")
    if not NUMERIC_CELL_RE.match(text):
        return False
    try:
        return float(text.rstrip("%").replace(",", "")) == 0.0
    except ValueError:
        return False


def _is_numeric_cell(cell: Any) -> bool:
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        return True
    if not isinstance(cell, str):
        return False
    return bool(NUMERIC_CELL_RE.match(cell.strip().lstrip("$")))


def gate_zero_variance(block: dict[str, Any], result: Result,
                       min_rows: int = 3) -> None:
    """Refuse a numeric column that is zero all the way down.

    A metric reading a field the data does not have returns zero rather than
    failing, and a zero is indistinguishable from a measurement downstream. That
    is how a stat card reading "0% of connector calls failed" shipped over a
    source that publishes no failure data at all — and why its reconciliation
    block tied out perfectly, proving that nothing summed to nothing.

    The rule: a whole column of zeros is not a finding that everything is zero.
    It is a column that was never measured, and the report must say which. A
    genuine all-zero week is rare, real, and says so in words — an unmeasured
    column has no words to offer, which is exactly what makes it dangerous.

    Columns shorter than `min_rows` are left alone: three rows of zero really
    can be three zeros.
    """
    problems: list[str] = []

    for name, table in _tables(block):
        rows = [r for r in table.get("rows") or [] if isinstance(r, list)]
        head = table.get("head") or []
        if len(rows) < min_rows:
            continue
        width = min((len(r) for r in rows), default=0)
        for column in range(width):
            cells = [row[column] for row in rows]
            if not all(_is_numeric_cell(c) for c in cells):
                continue
            if all(_is_zero_cell(c) for c in cells):
                label = head[column] if column < len(head) else f"column {column + 1}"
                problems.append(
                    f"{name}: every row of '{label}' is zero across {len(rows)} rows"
                )

    for label, spec in (block.get("reconciliation") or {}).items():
        if not isinstance(spec, dict):
            continue
        parts = [p for p in (spec.get("parts") or []) if isinstance(p, dict)]
        if len(parts) < min_rows:
            continue
        if _is_zero_cell(spec.get("total")) and all(_is_zero_cell(p.get("value"))
                                                    for p in parts):
            problems.append(
                f"{label}: a total of zero reconciled against {len(parts)} parts that "
                "are all zero — this proves nothing and reads as a measurement"
            )

    if problems:
        result.fail(
            "zero_variance",
            "; ".join(problems) + ". A column that is zero everywhere was not "
            "measured as zero, it was not measured. Report it as not measurable, "
            "naming the reason, rather than as a number — a zero here reads to a "
            "reader as good news.",
        )
    else:
        result.ok("zero_variance")


def _tables(block: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every head/rows table anywhere in the block, with the key it sits under."""
    found: list[tuple[str, dict[str, Any]]] = []

    def walk(node: Any, name: str) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("rows"), list) and "head" in node:
                found.append((name, node))
            for key, value in node.items():
                walk(value, key)
        elif isinstance(node, list):
            for item in node:
                walk(item, name)

    walk(block, "block")
    return found


def gate_completeness(html: str, block: dict[str, Any], result: Result) -> None:
    """Every key the page's own script reads must exist in the data.

    This gate exists because all the others passed a report that rendered as a
    blank page. The data was clean, the caveats were present, the arithmetic
    tied out — and the script threw on the first key that was not there, so a
    reader got a masthead and nothing else.

    The check is self-contained: the rendered file carries the script that
    consumes the data, so the requirement can be read out of the artifact
    rather than trusted from a schema kept somewhere else. Keys the script
    guards with `||` or `if(!D.x)` are optional and are not required here.
    """
    scripts = re.findall(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>",
                         html, re.DOTALL)
    code = "\n".join(scripts)
    if not code.strip():
        result.warn("completeness", "no render script found; nothing to check against")
        return

    referenced = set(re.findall(r"\bD\.([A-Za-z_][A-Za-z0-9_]*)", code))
    # A key the script defends against is allowed to be absent.
    optional = set(re.findall(r"\bD\.([A-Za-z_][A-Za-z0-9_]*)\s*\|\|", code))
    optional |= set(re.findall(r"if\s*\(\s*!\s*D\.([A-Za-z_][A-Za-z0-9_]*)", code))
    optional |= set(re.findall(r"\(\s*D\.([A-Za-z_][A-Za-z0-9_]*)\s*\|\|", code))

    missing = sorted(k for k in referenced - optional if k not in block)
    empty = sorted(k for k in referenced - optional
                   if k in block and block[k] in (None, [], {}))

    if missing:
        result.fail(
            "completeness",
            f"the page reads {missing} and the data does not have them. The report "
            "would render as a blank page — which every other gate here would pass.",
        )
    elif empty:
        result.warn("completeness", f"present but empty: {empty}")
        result.ok("completeness")
    else:
        result.ok("completeness")


def gate_structure(html: str, block: dict[str, Any], sidecar: Path | None, result: Result) -> None:
    if not block.get("RENDER_BLOCK"):
        result.fail("structure", "render block has no RENDER_BLOCK version field")
    else:
        result.ok("structure/version")

    if sidecar:
        if not sidecar.exists():
            result.fail("structure", f"sidecar {sidecar} not found")
            return
        match = _block_match(html)
        embedded = match.group(1).strip() if match else ""
        on_disk = sidecar.read_text(encoding="utf-8").strip()
        if embedded != on_disk:
            result.fail(
                "structure",
                "the embedded render block and its sidecar JSON differ. Iteration is "
                "a JSON swap; if they disagree, one of them is not what shipped.",
            )
        else:
            result.ok("structure/byte-match")


def summary_line(name: str, result: Result) -> str:
    """What the run says it did — in words that cannot be read as more than it is.

    This used to print "12 passed", and every reader of it, including the people
    who built it, took that to mean the numbers had been checked. They had not.
    Every gate here checks the shape of the document; not one of them can tell a
    figure computed from real data from a figure computed from a field that does
    not exist. Two reports went out confidently wrong with a full set of green
    checks, and the wording is why nobody looked twice.
    """
    tail = (f", {len(result.warnings)} warning(s)" if result.warnings else "")
    tail += (f", {len(result.failures)} failure(s)" if result.failures else "")
    return (f"{name}: {len(result.passed)} integrity checks passed{tail} · "
            "numbers not independently verified")


def validate(path: Path, sidecar: Path | None = None, edition: str = "shareable") -> Result:
    result = Result()
    html = path.read_text(encoding="utf-8")

    block, error = extract_block(html)
    if block is None:
        result.fail("structure", error or "unreadable render block")
        return result

    text = reader_text(block) + "\n" + markup_text(html)

    gate_privacy(text, result, edition)
    gate_small_group(text, result)
    gate_jargon(text, result)
    gate_acronyms(prose_text(block), result)
    gate_caveats(text, result, block.get("report_kind"))
    gate_arithmetic(block, result)
    gate_zero_variance(block, result)
    gate_completeness(html, block, result)
    gate_structure(html, block, sidecar, result)

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the publication gates over a rendered report.")
    ap.add_argument("report", help="the rendered .html")
    ap.add_argument("--json-sidecar", help="the render JSON the report should byte-match")
    ap.add_argument("--edition", choices=["shareable", "named"], default="shareable",
                    help="'named' permits emails; everything else still applies")
    ap.add_argument("--quiet", action="store_true", help="print failures only")
    args = ap.parse_args()

    path = Path(args.report)
    if not path.exists():
        print(f"validate: no such report: {path}", file=sys.stderr)
        return 2

    result = validate(path, Path(args.json_sidecar) if args.json_sidecar else None, args.edition)

    if not args.quiet:
        for gate in result.passed:
            print(f"  pass   {gate}")
    for gate, detail in result.warnings:
        print(f"  warn   {gate}: {detail}")
    for gate, detail in result.failures:
        print(f"  FAIL   {gate}: {detail}", file=sys.stderr)

    print("\n" + summary_line(path.name, result))
    if result.failures:
        print("This report does not ship until these are fixed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
