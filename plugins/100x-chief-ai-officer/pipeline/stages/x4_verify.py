#!/usr/bin/env python3
"""x4 — an independent second read that tries to break x3's conclusions.

Only what survives this becomes a finding.

Why it is separate, and blind
-----------------------------
A checker who has been shown the answer agrees. That is not a check; it is a
signature on somebody else's work. So this stage re-reads the passage from the
raw store and forms its own verdict with no access to x3's verdict, x3's
reason, or x3's severity. The comparison happens afterwards, in code, once both
answers exist.

The one asymmetry: when the judge is a model, x4's brief tells it that it is
the second reader and that its job is to reach its own answer rather than to
agree. That is a stance, not information about the first answer.

What survives
-------------
    confirmed   x3 said real, x4 agreed. This is the only thing published as a
                finding.
    disputed    x3 said real, x4 did not. Published as neither — a disagreement
                is a fact worth surfacing, not a tie to be broken silently.
    unverified  x3 said real, x4 has not looked yet. Stays a candidate.
    cleared     x3 said false alarm. Not re-read; a passage nobody thinks is
                real does not need a second opinion, and the budget is better
                spent on the ones that might be.

The default is to verify everything x3 called real. That is the honest setting:
sampling verification means some published findings were never checked, and if
the budget will not stretch, the right lever is reading fewer passages in x3
rather than confirming unchecked ones here.

Usage
-----
    caio verify --data-dir data
    caio verify --data-dir data --judge anthropic
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.judges import get_judge
from pipeline.judges.base import VERDICT_REAL
from pipeline.stages.x3_classify import (
    _to_candidate,
    group_by_passage,
    latest_run,
    load_candidates,
    load_verdicts,
)

JsonObject = dict[str, Any]

VERIFICATIONS_FILE = "verifications.jsonl"
SUMMARY_FILE = "verify_summary.json"

STATUS_CONFIRMED = "confirmed"
STATUS_DISPUTED = "disputed"
STATUS_UNVERIFIED = "unverified"
STATUS_CLEARED = "cleared"


def log(message: str) -> None:
    print(message, file=sys.stderr)


def load_verifications(root: Path, run_id: str) -> dict[str, JsonObject]:
    path = root / "_reports" / run_id / VERIFICATIONS_FILE
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                row = json.loads(line)
                out[row["block_id"]] = row
    return out


def resolve(verdict: JsonObject | None,
            verification: JsonObject | None) -> str:
    """Where one passage ended up, given both passes' answers."""
    if verdict is None:
        return STATUS_UNVERIFIED
    if verdict.get("verdict") != VERDICT_REAL:
        return STATUS_CLEARED
    if verification is None:
        return STATUS_UNVERIFIED
    return (STATUS_CONFIRMED if verification.get("verdict") == VERDICT_REAL
            else STATUS_DISPUTED)


def verify(root: Path, run_id: str, *, judge_name: str = "demo",
           limit: int = 0, resume: bool = True, **judge_options) -> JsonObject:
    # Grouped the same way x3 groups them, so both stages count the same
    # denominator and a verdict always has a passage to belong to.
    candidates = {str(c["block_id"]): c
                  for c in group_by_passage(load_candidates(root, run_id))}
    verdicts = load_verdicts(root, run_id)
    if not verdicts:
        return {"error": f"nothing has been judged for run '{run_id}'. Run x3 first."}

    out_path = root / "_reports" / run_id / VERIFICATIONS_FILE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not resume:
        out_path.unlink(missing_ok=True)

    already = load_verifications(root, run_id) if resume else {}

    # Only what x3 called real is worth a second read.
    claimed = [block_id for block_id, row in verdicts.items()
               if row.get("verdict") == VERDICT_REAL]
    pending = [b for b in claimed if b not in already]
    batch = pending[:limit] if limit else pending

    # A judge told it is the second reader — a stance, never the first answer.
    judge = get_judge(judge_name, second_opinion=True, **judge_options)

    cache: dict[str, Any] = {}
    written = 0
    with out_path.open("a", encoding="utf-8") as handle:
        for block_id in batch:
            row = candidates.get(block_id)
            if not row:
                continue
            candidate = _to_candidate(root, row, cache)
            if not candidate.text:
                continue
            # The judge is handed the passage and nothing else. x3's verdict is
            # not in scope here and is not passed in any form.
            second = judge.judge_one(candidate)
            record = second.to_row()
            record.update({"run_id": run_id, "pass": "verify",
                           "family": candidate.family})
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    judge.close()

    verifications = load_verifications(root, run_id)
    statuses = {STATUS_CONFIRMED: 0, STATUS_DISPUTED: 0,
                STATUS_UNVERIFIED: 0, STATUS_CLEARED: 0}
    by_family: dict[str, dict[str, int]] = {}

    for block_id in candidates:
        status = resolve(verdicts.get(block_id), verifications.get(block_id))
        statuses[status] += 1
        family = str(candidates[block_id].get("family") or "unknown")
        by_family.setdefault(family, {k: 0 for k in statuses})[status] += 1

    checked = statuses[STATUS_CONFIRMED] + statuses[STATUS_DISPUTED]
    agreement = round(100.0 * statuses[STATUS_CONFIRMED] / checked, 1) if checked else None
    judged = len(verdicts)
    survival = round(100.0 * statuses[STATUS_CONFIRMED] / judged, 1) if judged else None

    summary = {
        "stage": "x4_verify",
        "run_id": run_id,
        "generated_at": _now(),
        "judge": judge_name,
        "judge_name": getattr(judge, "name", judge_name),
        "verified_this_run": written,
        "statuses": statuses,
        "by_family": by_family,
        "agreement_pct": agreement,
        "measured_survival_pct": survival,
        "candidates_total": len(candidates),
        "judged_total": judged,
        "confirmed_note": (
            f"{statuses[STATUS_CONFIRMED]:,} passage(s) were called real by one reader "
            "and independently agreed by a second. Only these are published as findings."
        ),
        "disputed_note": (
            f"{statuses[STATUS_DISPUTED]:,} passage(s) were called real by the first "
            "reader and not by the second. They are published as neither — a "
            "disagreement is a fact worth surfacing, not a tie to break silently."
        ),
        "survival_note": (
            f"Of everything read, {survival}% survived both passes."
            if survival is not None else "Nothing has been read yet."
        ),
        "independence_note": (
            "The second pass re-read each passage from the raw store and never saw "
            "the first pass's verdict, reason or severity. A checker shown the answer "
            "agrees, which is a signature rather than a check."
        ),
    }
    (root / "_reports" / run_id / SUMMARY_FILE).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Independently re-read what x3 called real.")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--run-id", help="which run to verify (default: the newest)")
    ap.add_argument("--judge", default="demo", choices=["demo", "worksheet", "anthropic"])
    ap.add_argument("--limit", type=int, default=0,
                    help="most passages to re-read (default: 0, meaning all of them)")
    ap.add_argument("--model", help="model id, for the anthropic judge")
    ap.add_argument("--worksheet", default="verify-worksheet.md")
    ap.add_argument("--restart", action="store_true",
                    help="ignore verifications already recorded for this run")
    args = ap.parse_args()

    root = Path(args.data_dir)
    run_id = args.run_id or latest_run(root)
    if not run_id:
        log("x4: no scan output found.")
        return 1

    options: JsonObject = {"worksheet": args.worksheet}
    if args.model:
        options["model"] = args.model

    result = verify(root, run_id, judge_name=args.judge, limit=args.limit,
                    resume=not args.restart, **options)
    if "error" in result:
        log(f"x4: {result['error']}")
        return 1

    print(json.dumps(result, indent=2))
    statuses = result["statuses"]
    log(f"\n{statuses[STATUS_CONFIRMED]:,} confirmed, {statuses[STATUS_DISPUTED]:,} disputed, "
        f"{statuses[STATUS_CLEARED]:,} cleared as false alarms, "
        f"{statuses[STATUS_UNVERIFIED]:,} still unread.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
