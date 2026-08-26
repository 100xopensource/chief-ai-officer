#!/usr/bin/env python3
"""Consent gate for pulling and storing conversation content.

The compliance pull is different in kind from the analytics pull. Analytics
returns dollars and token counts. Compliance returns what people wrote, what
tools they called, and what those tools returned — in a healthcare, financial,
or legal organization that can include patient records, deal terms, and employee
case files. The pipeline needs it (the Exposure Report is built entirely from
it, and 77% of confirmed sensitive findings arrive through tool output rather
than typed prompts), but nobody should acquire a corpus like that by running a
command whose consequences weren't spelled out.

So content pulling is on by default — the reports do not work without it — and
gated by a consent record the operator creates once, deliberately, after reading
what it means. The record lives in the lake, not in the repo, and is per-lake:
copying the tool to a new organization does not carry the previous
organization's consent with it.

This is not a legal instrument. It is a forcing function that puts the decision
in front of a human, and leaves an auditable note of who made it and when.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONSENT_FILE = "_content_consent.json"
CONSENT_VERSION = 1

NOTICE = """
────────────────────────────────────────────────────────────────────────────
  CONTENT PULL — READ THIS BEFORE CONTINUING
────────────────────────────────────────────────────────────────────────────

You are about to pull conversation content from your organization's Claude
Enterprise deployment into a local data lake.

WHAT GETS STORED, on this machine, under {raw_path}:

  · Every message people typed into Claude.
  · Every input sent to every connected tool (search terms, record ids,
    queries against your internal systems).
  · Every payload those tools returned — which is where sensitive data
    actually lands. In the reference deployment, roughly 77% of confirmed
    sensitive findings arrived this way, and only about 4% were typed by a
    person.
  · Error payloads, which are a common place for credentials to surface.
  · Attachment and generated-file names (file *bodies* are not retrievable).

In a regulated organization this corpus may contain patient or customer
records, compensation and employee-relations detail, material nonpublic
information, and live credentials. It is as sensitive as the systems your
people connected Claude to.

WHY IT IS STORED RAW AND UNPARSED:

Every derived table is rebuilt from this store locally, with zero API calls.
Parsing bugs get fixed by re-parsing, not by re-pulling. An earlier version
of this pipeline parsed on the fly and discarded the rest; the loss was
unrecoverable and cost a full re-pull. Raw-first is why that cannot recur.

YOUR OBLIGATIONS, which this tool cannot discharge for you:

  · Get the governance sign-off your organization requires before pulling
    conversation content broadly. The cost and adoption reports do not need
    this pull; only the Exposure Report does.
  · Keep this machine's disk encrypted and access-controlled.
  · Decide a retention period. `caio purge-raw` deletes the store whenever
    you want it gone; derived tables survive.

Nothing is uploaded anywhere. This tool has no server, no telemetry, and no
network destination other than the Claude Enterprise APIs you point it at.
────────────────────────────────────────────────────────────────────────────
"""

PROMPT = 'Type "I have authorization" to record consent, or anything else to abort: '
EXPECTED = "i have authorization"


def consent_path(root: Path) -> Path:
    return Path(root) / CONSENT_FILE


def read_consent(root: Path) -> dict[str, Any] | None:
    path = consent_path(root)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return record if record.get("consent_version") == CONSENT_VERSION else None


def record_consent(root: Path, *, granted_by: str, method: str) -> dict[str, Any]:
    record = {
        "consent_version": CONSENT_VERSION,
        "granted_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "granted_by": granted_by,
        "method": method,
        "scope": "compliance conversation content, including tool inputs, tool "
                 "outputs, error payloads, and attachment metadata",
        "storage": str(Path(root).resolve() / "raw" / "compliance"),
        "note": "Recorded by pipeline.fetch.consent. Delete this file to revoke; "
                "run `caio purge-raw` to delete the stored content itself.",
    }
    path = consent_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def ensure_consent(root: Path, *, assume_yes: bool = False, quiet: bool = False) -> dict[str, Any]:
    """Return the consent record, obtaining it interactively if absent.

    Raises ConsentDeclined if consent is neither on file nor granted. Callers
    should let that propagate — a content pull without consent is not a
    degraded run, it is a run that must not happen.
    """
    existing = read_consent(root)
    if existing:
        if not quiet:
            print(
                f"[consent] content pull authorized {existing['granted_at']} "
                f"by {existing['granted_by']}",
                file=sys.stderr,
            )
        return existing

    if assume_yes:
        # Non-interactive path for scheduled runs. Still writes a record, and
        # still names who stands behind it, so the audit trail survives
        # automation.
        who = os.environ.get("CAIO_CONSENT_GRANTED_BY", "").strip()
        if not who:
            raise ConsentDeclined(
                "--yes was passed but CAIO_CONSENT_GRANTED_BY is unset. A recorded "
                "consent must name a person; set it to the accountable owner's "
                "name or email and re-run."
            )
        return record_consent(root, granted_by=who, method="non-interactive (--yes)")

    if not sys.stdin.isatty():
        raise ConsentDeclined(
            "Content pull needs consent and this session has no terminal to ask on.\n"
            "Run it once interactively, or set CAIO_CONSENT_GRANTED_BY and pass --yes."
        )

    print(NOTICE.format(raw_path=Path(root).resolve() / "raw" / "compliance"))
    try:
        answer = input(PROMPT).strip().lower()
    except (EOFError, KeyboardInterrupt):
        raise ConsentDeclined("Consent prompt cancelled; nothing was pulled.") from None

    if answer != EXPECTED:
        raise ConsentDeclined("Consent not given; nothing was pulled.")

    who = os.environ.get("CAIO_CONSENT_GRANTED_BY", "").strip() or _whoami()
    record = record_consent(root, granted_by=who, method="interactive")
    print(f"[consent] recorded in {consent_path(root)}", file=sys.stderr)
    return record


def _whoami() -> str:
    for var in ("USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(var)
        if value:
            return value
    return "unknown"


class ConsentDeclined(RuntimeError):
    """Raised when a content pull is attempted without recorded consent."""


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Inspect or grant content-pull consent.")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--show", action="store_true", help="print the current record and exit")
    ap.add_argument("--revoke", action="store_true", help="delete the consent record")
    args = ap.parse_args()

    lake_root = Path(args.data_dir)

    if args.revoke:
        consent_path(lake_root).unlink(missing_ok=True)
        print("Consent record deleted. Stored content is untouched — use `caio purge-raw` for that.")
        raise SystemExit(0)

    if args.show:
        current = read_consent(lake_root)
        print(json.dumps(current, indent=2) if current else "No consent on file.")
        raise SystemExit(0)

    try:
        ensure_consent(lake_root)
    except ConsentDeclined as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
