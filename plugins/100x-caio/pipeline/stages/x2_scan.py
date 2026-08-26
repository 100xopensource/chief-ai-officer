#!/usr/bin/env python3
"""Stage x2 — the mechanical scan. Code reads everything; no model reads anything.

This stage sweeps the whole corpus with the detector families and produces a
candidate list. It is deliberately dumb: it matches patterns, records addresses,
and makes no judgements at all. Its output is the input to the reading stage,
which is where judgement happens and where the money gets spent.

Why the split matters
---------------------
Reading is expensive and pattern matching is free. A scan over the full corpus
costs a few seconds of local compute; reading the same material costs real money
and real time. Doing the cheap exhaustive pass first, then reading only the
strongest candidates in a bounded budget, is what makes full coverage
affordable. Skipping the scan and reading everything is how a previous run
burned two days of an organisation's entire usage allowance in minutes.

What it writes
--------------
    _reports/<run>/candidates.jsonl   one row per hit: address, family, pattern
    _reports/<run>/scan_summary.json  counts by family, pattern, surface, week
    _reports/detectors/<run>/         the exact patterns used (saved here, now)

Candidate rows carry a fingerprint of the matched value, never the value. The
same secret appearing four hundred times collapses to one fingerprint with a
count of four hundred, which is both more useful to a reader and safer to store.

Exclusions
----------
Applied here rather than at reporting time, so no downstream stage can forget
them: the operator's own account, conversations about this analysis, and
conversations that export with no messages. All three would otherwise inflate
every count, and the third has a history of doing exactly that.
"""

from __future__ import annotations

# Runnable two ways: `python3 -m pipeline.stages.x2_scan` and `python3 <path>/x2_scan.py`.
# The second has no package context, so put the plugin root on the path first.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))


import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pipeline.detectors import families as det
from pipeline.lake import datalake as lake

JsonObject = dict[str, Any]

EXCLUSIONS_FILE = "exclusion_list.json"


def log(message: str) -> None:
    print(f"[scan] {message}", file=sys.stderr)


def load_exclusions(root: Path) -> dict[str, Any]:
    """Chats and accounts excluded from every count.

    Kept as a file rather than a flag so the same set applies to every stage and
    every rerun, and so the decision is reviewable rather than remembered.
    """
    path = Path(root) / "_reports" / EXCLUSIONS_FILE
    if not path.exists():
        return {"chat_ids": [], "user_ids": [], "reasons": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log(f"exclusion list at {path} is unreadable; proceeding with none")
        return {"chat_ids": [], "user_ids": [], "reasons": {}}
    data.setdefault("chat_ids", [])
    data.setdefault("user_ids", [])
    return data


def build_exclusions(root: Path, operator_user_id: str | None = None) -> dict[str, Any]:
    """Derive the default exclusion set from the lake and write it out.

    Three groups, each with a reason recorded next to it:

      · The operator's account. Its usage is this pipeline's own overhead, and
        counting it means the report measures itself.
      · Conversations about this analysis. Somebody asking Claude about Claude
        usage is observability, not a business signal.
      · Conversations that export with no messages. They have titles, so any
        count taken from titles inflates; they have no content, so they cannot
        contribute a finding either way.
    """
    chats = list(lake.iter_dataset(root, "dim_chat"))

    husks = [c["chat_id"] for c in chats if c.get("is_husk")]

    meta_terms = ("claude usage", "ai usage", "usage report", "cost report",
                  "waste ledger", "value x-ray", "exposure report", "token usage")
    meta = [
        c["chat_id"] for c in chats
        if any(term in str(c.get("chat_name") or "").lower() for term in meta_terms)
    ]

    operator_chats = (
        [c["chat_id"] for c in chats if c.get("user_id") == operator_user_id]
        if operator_user_id else []
    )

    excluded = sorted(set(husks) | set(meta) | set(operator_chats))
    payload = {
        "generated_at": _now(),
        "chat_ids": excluded,
        "user_ids": [operator_user_id] if operator_user_id else [],
        "counts": {
            "empty_exports": len(husks),
            "analysis_about_this_work": len(meta),
            "operator_account": len(operator_chats),
            "total_distinct": len(excluded),
        },
        "reasons": {
            "empty_exports": "Conversations that export with a title and no messages. "
                             "Counting them from titles inflates every total; they "
                             "carry no content to find.",
            "analysis_about_this_work": "Conversations about Claude usage itself. "
                                        "Observability, not a business signal.",
            "operator_account": "The account running this pipeline. Its usage is the "
                                "pipeline's own overhead.",
        },
    }

    path = Path(root) / "_reports" / EXCLUSIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def iter_scannable(root: Path, excluded_chats: set[str]) -> Iterator[JsonObject]:
    """Every block that belongs to one of the four scan surfaces, minus exclusions."""
    for block in lake.iter_dataset(root, "fact_block"):
        if not block.get("surface"):
            continue
        if block.get("chat_id") in excluded_chats:
            continue
        yield block


def scan(root: Path, run_id: str, *, include_low_precision: bool = True) -> dict[str, Any]:
    exclusions = load_exclusions(root)
    excluded = set(exclusions.get("chat_ids", []))
    log(f"{len(excluded)} conversation(s) excluded from all counts")

    # Saved now, at scan time, not at publication time.
    det.save_definitions(root, date.fromisoformat(run_id) if _is_date(run_id) else None)

    out_dir = Path(root) / "_reports" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = out_dir / "candidates.jsonl"

    by_family: Counter[str] = Counter()
    by_pattern: Counter[str] = Counter()
    by_surface: Counter[str] = Counter()
    by_week: Counter[str] = Counter()
    fingerprints: Counter[str] = Counter()
    placeholders = 0
    scanned = 0
    truncated_hits = 0

    with candidates_path.open("w", encoding="utf-8") as out:
        for block in iter_scannable(root, excluded):
            scanned += 1
            text = block.get("text") or ""
            if not text:
                continue
            surface = block["surface"]

            for family in det.TEXT_FAMILIES:
                if family.surfaces and surface not in family.surfaces:
                    continue
                for pattern_id, matched in family.scan(text):
                    pattern = next(p for p in family.patterns if p.id == pattern_id)
                    if not include_low_precision and pattern.expect_precision in {"low", "zero-without-context"}:
                        continue

                    is_placeholder = (
                        family.id == "D1" and det.looks_like_placeholder(matched)
                    )
                    if is_placeholder:
                        placeholders += 1

                    digest = det.fingerprint(matched)
                    fingerprints[digest] += 1

                    row = {
                        # Address only. The matched text is never written.
                        "block_id": block["block_id"],
                        "chat_id": block["chat_id"],
                        "message_id": block["message_id"],
                        "block_idx": block["block_idx"],
                        "surface": surface,
                        "day": block["day"],
                        "week": lake.week_key(block["day"]) if block.get("day") else None,
                        "family": family.id,
                        "pattern_id": pattern_id,
                        "expect_precision": pattern.expect_precision,
                        "integration": block.get("integration"),
                        "tool_name": block.get("tool_name"),
                        "is_error": bool(block.get("is_error")),
                        "truncated": bool(block.get("truncated")),
                        "char_len": block.get("char_len"),
                        "value_fingerprint": digest,
                        "likely_placeholder": is_placeholder,
                        "verdict": None,  # filled in by the reading stage
                    }
                    out.write(json.dumps(row, separators=(",", ":")) + "\n")

                    by_family[family.id] += 1
                    by_pattern[pattern_id] += 1
                    by_surface[surface] += 1
                    if row["week"]:
                        by_week[row["week"]] += 1
                    if row["truncated"]:
                        truncated_hits += 1

            if scanned % 20_000 == 0:
                log(f"  {scanned:,} blocks, {sum(by_family.values()):,} leads")

    total = sum(by_family.values())
    repeated = {f: n for f, n in fingerprints.items() if n > 1}

    summary = {
        "run_id": run_id,
        "scanned_at": _now(),
        "detector_version": det.DETECTOR_VERSION,
        "blocks_scanned": scanned,
        "conversations_excluded": len(excluded),
        "leads_total": total,
        "leads_by_family": dict(by_family.most_common()),
        "leads_by_pattern": dict(by_pattern.most_common()),
        "leads_by_surface": dict(by_surface.most_common()),
        "leads_by_week": dict(sorted(by_week.items())),
        "distinct_values": len(fingerprints),
        "values_appearing_more_than_once": len(repeated),
        "most_repeated_value_count": max(fingerprints.values()) if fingerprints else 0,
        "likely_placeholders": placeholders,
        "leads_in_truncated_payloads": truncated_hits,
        "governance": _governance_signals(root, excluded),
        "reading_this": (
            "Every number above is a LEAD COUNT, not a finding count. Historically "
            "about 44% of leads survive being read, and some patterns survive at 0%. "
            "Publishing any of these figures as a result would overstate reality. "
            "The next stage reads a bounded sample and judges; only what survives an "
            "independent check is published, as a floor."
        ),
    }

    (out_dir / "scan_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _governance_signals(root: Path, excluded: set[str]) -> dict[str, Any]:
    """The two families computed from metadata rather than matched against text."""
    connectors = list(lake.iter_dataset(root, "analytics_connectors"))
    names = {str(c.get("connector_name")) for c in connectors if c.get("connector_name")}
    unnamed = sorted(n for n in names if det.is_unnamed_connector(n))

    # Consult before trending anything: a connector born mid-window will show
    # spectacular growth that is entirely an artefact of when it appeared.
    born = det.first_seen(connectors, "connector_name")

    chats = list(lake.iter_dataset(root, "dim_chat"))
    husks = sum(1 for c in chats if c.get("is_husk"))

    attachments = list(lake.iter_dataset(root, "fact_attachment"))
    named = [a for a in attachments if a.get("display_name")]
    defaults = sum(1 for a in named if det.is_default_name(a["display_name"]))

    return {
        "connectors_total": len(names),
        "connectors_unnamed": len(unnamed),
        "connectors_unnamed_note": (
            "Connections presenting as an identifier rather than a product name. They "
            "may be legitimate; the finding is that nobody can currently say which."
        ),
        "connector_first_seen": born,
        "connector_first_seen_note": (
            "Check this before comparing any connector across time. A connector that "
            "first appears mid-window shows enormous growth purely because it did not "
            "exist earlier — an artefact that has previously been published as a "
            "finding before being caught."
        ),
        "conversations_with_no_messages": husks,
        "attachments_total": len(attachments),
        "attachments_with_default_names": defaults,
        "attachments_default_name_note": (
            "Auto-generated names excluded from any filename-based count. Treating "
            "them as chosen names once produced a confident finding about dozens of "
            "people sharing a document they had not shared."
        ),
    }


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sweep the corpus for candidate sensitive material. Produces leads, not findings.",
    )
    ap.add_argument("--data-dir", default="data", help="lake root")
    ap.add_argument("--run-id", default=date.today().isoformat(),
                    help="names the output folder under _reports/ (default: today)")
    ap.add_argument("--operator-user-id",
                    help="the account running this pipeline, excluded from all counts")
    ap.add_argument("--rebuild-exclusions", action="store_true",
                    help="regenerate the exclusion list before scanning")
    ap.add_argument("--high-precision-only", action="store_true",
                    help="skip patterns known to be mostly noise")
    args = ap.parse_args()

    root = Path(args.data_dir)
    if not (root / "fact_block").is_dir():
        print(
            "No fact_block dataset. This stage scans conversation content, which means "
            "the content pull and parse must have run first.",
            file=sys.stderr,
        )
        return 2

    if args.rebuild_exclusions or not (root / "_reports" / EXCLUSIONS_FILE).exists():
        built = build_exclusions(root, args.operator_user_id)
        log(f"exclusion list: {built['counts']['total_distinct']} conversation(s)")

    summary = scan(root, args.run_id, include_low_precision=not args.high_precision_only)
    print(json.dumps(summary, indent=2))
    log(
        f"{summary['leads_total']:,} leads from {summary['blocks_scanned']:,} blocks. "
        "These are candidates. Nothing here is publishable until it has been read."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
