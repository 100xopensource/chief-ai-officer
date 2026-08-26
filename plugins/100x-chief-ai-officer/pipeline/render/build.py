#!/usr/bin/env python3
"""Build a report: template + render JSON -> a self-rendering HTML file.

The mechanism
-------------
Each report is one HTML file that renders itself from the JSON inside its
`<script type="application/json" id="render-block">` tag. The markup, the CSS,
and the rendering script are fixed. Producing next week's edition means
replacing that JSON and nothing else.

That constraint is the point. A report format where each edition is hand-edited
drifts: a heading changes, a caveat gets trimmed, a number is updated in the
prose but not the chart. When the only mutable surface is a data block, the
design cannot rot and every difference between two editions is a data
difference you can diff.

What this module guarantees
---------------------------
  · The template outside the render block is preserved byte for byte. If a
    build changes so much as a space in the CSS, it fails rather than shipping.
  · The embedded block round-trips: what comes out of the built file parses back
    to exactly the data that went in.
  · A sidecar copy of the JSON is written next to the report, so the validator
    can prove later that the shipped file still matches its source.
  · Nothing is written unless every check passes. A half-built report is worse
    than none, because it looks finished.

Usage
-----
    python3 -m pipeline.render.build --report waste --block waste_render.json \\
            --out waste-ledger-2026-07-12.html
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TEMPLATES = Path(__file__).resolve().parent / "templates"

REPORTS = {
    "waste": ("waste_ledger.html", "waste-ledger"),
    "value": ("value_xray.html", "value-xray"),
    "exposure": ("exposure_report.html", "exposure-report"),
}

BLOCK_RE = re.compile(
    r'(<script[^>]*id=["\']render-block["\'][^>]*>)(.*?)(</script>)',
    re.DOTALL,
)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class BuildError(RuntimeError):
    """A build that must not produce a file."""


def template_path(report: str) -> Path:
    if report not in REPORTS:
        raise BuildError(f"unknown report '{report}'. Known: {', '.join(sorted(REPORTS))}")
    path = TEMPLATES / REPORTS[report][0]
    if not path.exists():
        raise BuildError(
            f"template missing: {path}\n"
            "Templates are the report's design and are not reconstructed from a "
            "description. Restore the file rather than improvising one."
        )
    return path


def _locate_block(html: str) -> re.Match[str]:
    """Find the real render block, not a mention of it in a header comment."""
    masked = COMMENT_RE.sub(lambda m: " " * len(m.group(0)), html)
    match = BLOCK_RE.search(masked)
    if not match:
        raise BuildError('template has no <script id="render-block"> tag')
    return match


def build(report: str, block: dict[str, Any], out: Path,
          *, template: Path | None = None, write_sidecar: bool = True) -> dict[str, Any]:
    source = template or template_path(report)
    html = source.read_text(encoding="utf-8")

    match = _locate_block(html)
    start, end = match.start(2), match.end(2)

    payload = json.dumps(block, indent=2, ensure_ascii=False)
    built = f"{html[:start]}\n{payload}\n{html[end:]}"

    _assert_only_block_changed(html, built, start, end)
    _assert_roundtrip(built, block)

    if out.exists():
        backup = out.with_suffix(out.suffix + ".bak")
        shutil.copy2(out, backup)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(built, encoding="utf-8")

    sidecar = None
    if write_sidecar:
        sidecar = out.with_suffix(".json")
        sidecar.write_text(payload + "\n", encoding="utf-8")

    return {
        "report": report,
        "template": str(source),
        "out": str(out),
        "sidecar": str(sidecar) if sidecar else None,
        "bytes": len(built),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _assert_only_block_changed(original: str, built: str, start: int, end: int) -> None:
    """Everything outside the render block must be identical.

    Checked by comparing the two halves directly rather than trusting that a
    string replacement did what it looked like it did. A build that quietly
    reflows the markup would break the "swap only the JSON" contract that makes
    two editions comparable.
    """
    head_before, tail_before = original[:start], original[end:]
    built_match = _locate_block(built)
    head_after = built[:built_match.start(2)]
    tail_after = built[built_match.end(2):]

    if head_before != head_after:
        raise BuildError("markup before the render block changed; refusing to write")
    if tail_before != tail_after:
        raise BuildError("markup after the render block changed; refusing to write")


def _assert_roundtrip(built: str, block: dict[str, Any]) -> None:
    """The embedded block must parse back to exactly what went in."""
    match = _locate_block(built)
    try:
        parsed = json.loads(match.group(2).strip())
    except ValueError as exc:
        raise BuildError(f"embedded block does not parse after build: {exc}") from None
    if parsed != block:
        raise BuildError(
            "embedded block does not round-trip to the input data. Something was "
            "lost or altered in serialisation; refusing to write."
        )


def default_out(report: str, block: dict[str, Any], out_dir: Path) -> Path:
    """Name the file after the report and the week it covers."""
    stem = REPORTS[report][1]
    label = str(block.get("week_ending") or block.get("week_label") or "").strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", label)
    suffix = match.group(0) if match else datetime.now(timezone.utc).date().isoformat()
    return out_dir / f"{stem}-{suffix}.html"


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a report from its template and render JSON.")
    ap.add_argument("--report", required=True, choices=sorted(REPORTS),
                    help="which report to build")
    ap.add_argument("--block", required=True, help="the render JSON")
    ap.add_argument("--out", help="output path (default: <report>-<week>.html in --out-dir)")
    ap.add_argument("--out-dir", default=".", help="directory for the default filename")
    ap.add_argument("--template", help="override the template (for local design work)")
    ap.add_argument("--no-sidecar", action="store_true",
                    help="skip writing the .json sidecar the validator byte-matches against")
    args = ap.parse_args()

    block_path = Path(args.block)
    if not block_path.exists():
        print(f"build: no such render block: {block_path}", file=sys.stderr)
        return 2

    try:
        block = json.loads(block_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"build: render block is not valid JSON: {exc}", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else default_out(args.report, block, Path(args.out_dir))

    try:
        result = build(
            args.report, block, out,
            template=Path(args.template) if args.template else None,
            write_sidecar=not args.no_sidecar,
        )
    except BuildError as exc:
        print(f"build: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    print(
        f"\nBuilt {out}. Run the gates before sharing it:\n"
        f"  python3 -m pipeline.render.validate {out}"
        + (f" --json-sidecar {result['sidecar']}" if result["sidecar"] else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
