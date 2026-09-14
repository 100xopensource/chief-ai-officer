#!/usr/bin/env python3
"""Package a validated report as something you can actually hand to someone.

A report that exists at `/tmp/exposure-report-2026-07-12.html` is not a
deliverable. A deliverable is a file with a name that says what it is, a
checksum that says what it was when it left, a record of which gates it passed,
and a covering note the sender can paste into an email without rewriting it.

What this produces
------------------
    deliveries/2026-07-12/
      exposure-report-2026-07-12.html      the report, unchanged
      exposure-report-2026-07-12.json      the data it renders itself from
      MANIFEST-exposure.json               what shipped, and what it passed
      COVER-NOTE-exposure.md               what to say when sending it
      CHECKSUMS.txt                        so a recipient can prove it is intact

Why the gates run again here
----------------------------
They ran at build time, on the same file. They run again at delivery because
delivery is the last moment anything can be stopped, and because the file may
have been touched in between — opened in an editor, "cleaned up", regenerated
by hand. Re-running costs a second. Shipping a report that quietly stopped
passing costs considerably more, and the failure mode is silent.

Refusing to package is the point
--------------------------------
If a gate fails, nothing is written. There is no `--force`. A deliverable that
you had to override a privacy check to produce is not a deliverable; it is an
incident, and the right move is to fix the report.

Every report is packaged as the shareable edition unless `--edition named` is
passed, which permits email addresses and stamps the manifest and the cover
note accordingly, so a named edition can never be mistaken for a shareable one
by whoever finds it later.

Usage
-----
    python3 -m pipeline.render.deliver report.html --out-dir deliveries
    python3 -m pipeline.render.deliver report.html --to "Head of Compliance"
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.render.validate import extract_block, validate

JsonObject = dict[str, Any]

REPORT_TITLES = {
    "waste": "Waste Ledger",
    "value": "Value X-Ray",
    "exposure": "Exposure Report",
}

REPORT_READERS = {
    "waste": "Finance",
    "value": "Head of AI",
    "exposure": "Compliance",
}

# What each report is for, in one sentence, in the words its reader uses.
REPORT_PURPOSE = {
    "waste": "what Claude cost last week, where money went that bought nothing, "
             "and what that is worth at renewal",
    "value": "what the spending actually bought, and what is getting in the way "
             "of it buying more",
    "exposure": "what sensitive material reached the AI last week, how it got "
                "there, and how much of it we can see",
}


class DeliveryRefused(RuntimeError):
    """A report that must not be packaged."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deliver(report_path: Path, out_dir: Path, *, edition: str = "shareable",
            recipient: str | None = None, sender: str | None = None) -> JsonObject:
    if not report_path.exists():
        raise DeliveryRefused(f"no such report: {report_path}")

    html = report_path.read_text(encoding="utf-8")
    block, error = extract_block(html)
    if block is None:
        raise DeliveryRefused(f"unreadable report: {error}")

    sidecar = report_path.with_suffix(".json")
    result = validate(report_path, sidecar if sidecar.exists() else None, edition)
    if result.failures:
        lines = "\n".join(f"    {gate}: {detail}" for gate, detail in result.failures)
        raise DeliveryRefused(
            f"{report_path.name} does not pass its own gates, so it is not packaged:\n"
            f"{lines}\n"
            "  Fix the report. There is no override here on purpose — a report you "
            "had to override a privacy check to send is not a report."
        )

    kind = str(block.get("report_kind") or "report")
    week = str(block.get("week_ending") or "undated")
    folder = out_dir / week
    folder.mkdir(parents=True, exist_ok=True)

    delivered_html = folder / report_path.name
    shutil.copy2(report_path, delivered_html)
    delivered_json = None
    if sidecar.exists():
        delivered_json = folder / sidecar.name
        shutil.copy2(sidecar, delivered_json)

    manifest = {
        "report": kind,
        "title": REPORT_TITLES.get(kind, kind),
        "written_for": REPORT_READERS.get(kind, "unspecified"),
        "week_label": block.get("week_label"),
        "week_ending": week,
        "edition": edition,
        "edition_note": (
            "Counts, categories and dates only. No names, no email addresses, no "
            "filenames, no conversation content."
            if edition == "shareable" else
            "NAMED EDITION — contains identifying detail. Do not forward. Do not "
            "store outside the systems agreed for it."
        ),
        "packaged_at": _now(),
        "packaged_by": sender or "unattributed",
        "recipient": recipient,
        "gates_passed": result.passed,
        "gates_warned": [f"{gate}: {detail}" for gate, detail in result.warnings],
        "files": {},
        "findings_count": len(block.get("findings") or []),
        "provenance": (
            "Built from this organisation's own Claude usage by the 100x Chief AI Officer pipeline. "
            "The data is read locally and nothing is sent anywhere. The report renders "
            "itself from the data record embedded inside it, so the file you received "
            "and the data it displays cannot disagree."
        ),
    }

    for path in [delivered_html] + ([delivered_json] if delivered_json else []):
        manifest["files"][path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}

    # One manifest per report, not one per folder: all three reports for a week
    # land in the same folder, and a shared manifest means the second one
    # silently erases the first one's record of what it passed.
    manifest_path = folder / f"MANIFEST-{kind}.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    (folder / f"COVER-NOTE-{kind}.md").write_text(
        _cover_note(manifest, block), encoding="utf-8")

    _rewrite_checksums(folder)

    return {
        "delivered_to": str(folder),
        "report": str(delivered_html),
        "manifest": str(manifest_path),
        "cover_note": str(folder / f"COVER-NOTE-{kind}.md"),
        "gates_passed": len(result.passed),
        "edition": edition,
    }


def _rewrite_checksums(folder: Path) -> None:
    """One checksum file covering everything in the folder, rebuilt each time.

    Recomputed from what is actually on disk rather than appended to, so a
    re-delivery of one report cannot leave a stale line for a file that has
    since changed.
    """
    lines = []
    for path in sorted(folder.iterdir()):
        if path.name == "CHECKSUMS.txt" or not path.is_file():
            continue
        lines.append(f"{sha256(path)}  {path.name}\n")
    (folder / "CHECKSUMS.txt").write_text("".join(lines), encoding="utf-8")


def _cover_note(manifest: JsonObject, block: JsonObject) -> str:
    """The email the sender would otherwise have to write from scratch.

    Written so it can be sent unedited. It states what the report is, what it
    covers, the one thing worth reading first, and — the part people forget —
    what the report cannot see, because a reader who discovers a blind spot on
    their own stops trusting everything else on the page.
    """
    kind = manifest["report"]
    verdict = (block.get("verdict") or [""])[0]
    findings = block.get("findings") or []
    moves = block.get("moves")
    if isinstance(moves, dict):
        moves = moves.get("items") or []
    first_move = moves[0] if moves else None

    lines = [
        f"# {manifest['title']} — {manifest['week_label']}",
        "",
        f"For: {manifest['written_for']}"
        + (f"  ·  Sent to: {manifest['recipient']}" if manifest.get("recipient") else ""),
        "",
        "## What this is",
        "",
        f"The {manifest['title']} covers {REPORT_PURPOSE.get(kind, 'this period')}. "
        "It is one HTML file — open it in any browser, no software needed, nothing to install.",
        "",
        "**Attach the file; do not paste it into the body of an email.** The page draws "
        "its own contents when a browser opens it, so an email preview pane, a chat "
        "preview or a document viewer shows nothing. Whoever you send it to should save "
        "the attachment and open it in a browser. The file itself says so if they land "
        "on it the other way.",
        "",
        "## The short version",
        "",
        verdict or "_See the report._",
        "",
    ]

    if first_move:
        text = first_move.get("move") or first_move.get("text") or ""
        owner = first_move.get("owner", "")
        lines += ["## If you only do one thing", "",
                  f"{text}" + (f"  \n_Owner: {owner}_" if owner else ""), ""]

    if findings:
        lines += ["## What is in it", "",
                  f"{len(findings)} findings, each with what it is worth, who owns it, and "
                  "what to do about it. Every one says whether it is new this week or has "
                  "been here before.", ""]

    lines += [
        "## What it cannot tell you",
        "",
        "Every number in this report is a floor drawn from what the data can see. It reads "
        "chat conversations only; other products are not visible to it and it does not guess "
        "about them. Recent weeks can still be revised for about a month as late data lands.",
        "",
        "## Handling",
        "",
        manifest["edition_note"],
        "",
        "---",
        "",
        f"Packaged {manifest['packaged_at']} by the 100x Chief AI Officer pipeline. "
        "Checksums are in `CHECKSUMS.txt`; the gates this file passed are listed in "
        "`MANIFEST.json`.",
        "",
    ]
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Package a validated report as a deliverable.")
    ap.add_argument("report", nargs="+", help="the rendered .html file(s)")
    ap.add_argument("--out-dir", default="deliveries")
    ap.add_argument("--edition", choices=["shareable", "named"], default="shareable")
    ap.add_argument("--to", dest="recipient", help="who this is going to")
    ap.add_argument("--from", dest="sender", help="who is sending it")
    args = ap.parse_args()

    results, refused = [], 0
    for name in args.report:
        try:
            results.append(deliver(Path(name), Path(args.out_dir),
                                   edition=args.edition,
                                   recipient=args.recipient, sender=args.sender))
        except DeliveryRefused as exc:
            print(f"deliver: {exc}", file=sys.stderr)
            refused += 1

    if results:
        print(json.dumps(results, indent=2))
        folder = results[0]["delivered_to"]
        print(f"\nReady to send: {folder}\n"
              f"  The cover note is written to be pasted as-is.",
              file=sys.stderr)
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
