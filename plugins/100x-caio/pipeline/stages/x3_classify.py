#!/usr/bin/env python3
"""x3 — read the flagged passages and judge them. Candidates become verdicts.

x2 found things that *look* like sensitive data. This stage is where something
actually reads each one and decides whether it is. That distinction is the
whole point of the pipeline: roughly 44% of flagged passages survive being
read, and some families survive none, so a match count published as a result
overstates reality by more than double.

Three things this stage is careful about
----------------------------------------
**It reads strongest-first, and records how far it got.** A corpus can produce
thousands of candidates and a budget rarely covers all of them. So the queue is
ordered by how reliable the pattern has been historically, the run is bounded,
and the fraction read is written into the record. Everything downstream treats
counts as floors because this number says exactly how much of the pile was
turned over.

**It never persists what it read.** The passage text is fetched from the raw
store at judgement time and thrown away. What is stored is a verdict, a
severity and a reason — and the reason is scrubbed of values, names and
filenames before it is written, because the record outlives the run and is read
by people who must not see them.

**It never confirms anything on its own.** A verdict here is one reader's
answer. Confirmation requires x4 to agree, independently. This stage cannot
promote anything to a finding, and does not try.

Judges
------
    demo        offline, deterministic. CI, tests, the synthetic lake.
    worksheet   writes a file, a person fills it in, run again to read it back.
    anthropic   a model reads each passage. Needs a key.

Usage
-----
    caio judge --data-dir data                       # demo judge, offline
    caio judge --data-dir data --judge worksheet     # write a review file
    caio judge --data-dir data --judge anthropic --limit 200
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
from typing import Any, Iterator

from pipeline.detectors import families as det
from pipeline.judges import get_judge
from pipeline.judges.base import (
    CONTEXT_CHARS,
    VERDICT_FALSE_ALARM,
    VERDICT_REAL,
    VERDICT_UNSURE,
    Candidate,
)
from pipeline.lake import raw as rawstore

JsonObject = dict[str, Any]

VERDICTS_FILE = "verdicts.jsonl"
SUMMARY_FILE = "classify_summary.json"

# Read the most reliable patterns first. When a budget runs out part-way, what
# was read should be the part most likely to matter, and what was skipped
# should be the part least likely to survive anyway.
PRECISION_ORDER = {"high": 0, "medium": 1, "low": 2, "zero-without-context": 3}

# A default that keeps an exploratory run from turning into a bill. Raise it
# deliberately; the summary always says what fraction was covered.
DEFAULT_LIMIT = 250


def log(message: str) -> None:
    print(message, file=sys.stderr)


# --------------------------------------------------------------------------
# recovering the passage
# --------------------------------------------------------------------------

def _text_for(root: Path, candidate_row: JsonObject, cache: dict[str, Any]) -> str:
    """Fetch the passage a candidate points at, out of the raw store.

    The raw store is the reason this is possible at all: the derived tables keep
    no text, so a judgement pass would be impossible if the original responses
    had not been persisted before parsing.
    """
    chat_id = str(candidate_row.get("chat_id") or "")
    if not chat_id:
        return ""

    if chat_id not in cache:
        _, messages = rawstore.read_chat(root, chat_id)
        cache.clear()  # one chat at a time; the corpus does not fit in memory
        cache[chat_id] = {str(m.get("uuid") or m.get("id") or ""): m for m in messages}

    message = cache[chat_id].get(str(candidate_row.get("message_id") or ""))
    if not message:
        return ""

    content = message.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return ""

    index = candidate_row.get("block_idx")
    if not isinstance(index, int) or index >= len(content):
        return ""

    block = content[index]
    return _block_text(block) if isinstance(block, dict) else ""


def _block_text(block: JsonObject) -> str:
    """Same shape-handling the parser uses — a tool result nests its text."""
    if isinstance(block.get("text"), str):
        return block["text"]
    inner = block.get("content")
    if isinstance(inner, str):
        return inner
    if isinstance(inner, list):
        return "\n\n".join(part.get("text", "") for part in inner
                           if isinstance(part, dict) and isinstance(part.get("text"), str))
    if isinstance(block.get("input"), dict):
        return json.dumps(block["input"], ensure_ascii=False)
    return ""


# --------------------------------------------------------------------------

def load_candidates(root: Path, run_id: str) -> list[JsonObject]:
    path = root / "_reports" / run_id / "candidates.jsonl"
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def group_by_passage(rows: list[JsonObject]) -> list[JsonObject]:
    """One entry per passage, not per pattern match.

    A single passage often trips several patterns at once — a result payload
    with both a signed link and a long number in it fires twice. A reader reads
    that passage once and judges it once, so the unit of judgement is the
    passage.

    Keying verdicts by passage without grouping first was a real defect: the
    verdict file collapsed six hundred matches into fifty-nine entries and the
    coverage figures silently described the wrong denominator.
    """
    grouped: dict[str, JsonObject] = {}
    for row in rows:
        block_id = str(row.get("block_id") or "")
        if not block_id:
            continue
        existing = grouped.get(block_id)
        if existing is None:
            merged = dict(row)
            merged["patterns"] = [str(row.get("pattern_id") or "")]
            merged["families"] = [str(row.get("family") or "")]
            merged["match_count"] = 1
            grouped[block_id] = merged
            continue

        existing["match_count"] += 1
        for key, value in (("patterns", row.get("pattern_id")),
                           ("families", row.get("family"))):
            if value and str(value) not in existing[key]:
                existing[key].append(str(value))

        # The strongest pattern that hit this passage decides how urgently it is
        # read and which family the verdict is filed under.
        if (PRECISION_ORDER.get(str(row.get("expect_precision")), 9)
                < PRECISION_ORDER.get(str(existing.get("expect_precision")), 9)):
            existing["expect_precision"] = row.get("expect_precision")
            existing["family"] = row.get("family")
            existing["pattern_id"] = row.get("pattern_id")

        # Any pattern calling it a placeholder, and any truncation, applies to
        # the whole passage.
        existing["likely_placeholder"] = bool(existing.get("likely_placeholder")) or \
            bool(row.get("likely_placeholder"))
        existing["truncated"] = bool(existing.get("truncated")) or bool(row.get("truncated"))

    return list(grouped.values())


def latest_run(root: Path) -> str | None:
    reports = root / "_reports"
    if not reports.is_dir():
        return None
    runs = sorted(p.name for p in reports.iterdir()
                  if p.is_dir() and (p / "candidates.jsonl").exists())
    return runs[-1] if runs else None


def load_verdicts(root: Path, run_id: str) -> dict[str, JsonObject]:
    path = root / "_reports" / run_id / VERDICTS_FILE
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


def _queue(candidates: list[JsonObject], already: set[str]) -> list[JsonObject]:
    """Strongest-first, skipping anything already judged."""
    pending = [c for c in candidates if c.get("block_id") not in already]
    pending.sort(key=lambda c: (
        PRECISION_ORDER.get(str(c.get("expect_precision")), 9),
        bool(c.get("likely_placeholder")),
        str(c.get("day") or ""),
    ))
    return pending


def _to_candidate(root: Path, row: JsonObject, cache: dict[str, Any]) -> Candidate:
    family = det.FAMILIES_BY_ID.get(str(row.get("family")))
    return Candidate(
        block_id=str(row["block_id"]),
        chat_id=str(row.get("chat_id") or ""),
        family=str(row.get("family") or ""),
        family_title=family.title if family else str(row.get("family") or "unknown"),
        pattern_id=str(row.get("pattern_id") or ""),
        expect_precision=str(row.get("expect_precision") or ""),
        surface=str(row.get("surface") or ""),
        day=str(row.get("day") or ""),
        text=_text_for(root, row, cache)[:CONTEXT_CHARS],
        tool_name=row.get("tool_name"),
        integration=row.get("integration"),
        is_error=bool(row.get("is_error")),
        truncated=bool(row.get("truncated")),
        likely_placeholder=bool(row.get("likely_placeholder")),
        also_matched=tuple(p for p in row.get("patterns", [])
                           if p and p != row.get("pattern_id")),
    )


# --------------------------------------------------------------------------

def _reusable(verdicts: dict[str, JsonObject], judge: Any) -> dict[str, JsonObject]:
    """Verdicts the current judge may treat as already done.

    Two exclusions. A verdict from a different judge is somebody else's answer —
    reusing it would let an offline stand-in silently satisfy a real reading.
    And a worksheet placeholder is a note that a passage is *waiting* to be
    read, not a reading of it.
    """
    name = getattr(judge, "name", "")
    return {
        block_id: row for block_id, row in verdicts.items()
        if row.get("judged_by") == name and not row.get("awaiting_review")
    }


def classify(root: Path, run_id: str, *, judge_name: str = "demo",
             limit: int = DEFAULT_LIMIT, resume: bool = True,
             **judge_options) -> JsonObject:
    matches = load_candidates(root, run_id)
    if not matches:
        return {"error": f"no candidates for run '{run_id}'. Run the scan first."}
    candidates = group_by_passage(matches)

    out_path = root / "_reports" / run_id / VERDICTS_FILE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not resume:
        # Starting over means starting over. Leaving the old verdicts in place
        # while ignoring them for scheduling made --restart a no-op, and the
        # coverage figures then described a file nobody had rebuilt.
        out_path.unlink(missing_ok=True)

    judge = get_judge(judge_name, **judge_options)

    # Only reuse verdicts this same judge produced. Verdicts from different
    # judges are not interchangeable — a run by the offline stand-in must never
    # make a real reading think the work is already done, which is exactly what
    # a shared "already judged" set caused.
    existing = _reusable(load_verdicts(root, run_id), judge) if resume else {}
    pending = _queue(candidates, set(existing))
    batch = pending[:limit] if limit else pending

    cache: dict[str, Any] = {}
    written = 0
    counts = {VERDICT_REAL: 0, VERDICT_FALSE_ALARM: 0, VERDICT_UNSURE: 0}
    unreadable = 0

    # Append rather than rewrite: a run interrupted after two hundred
    # judgements should cost nothing to resume.
    with out_path.open("a", encoding="utf-8") as handle:
        for row in batch:
            candidate = _to_candidate(root, row, cache)
            if not candidate.text:
                # The passage cannot be recovered from the raw store. That is a
                # fact about coverage, not a verdict, and it stays unjudged.
                unreadable += 1
                continue
            verdict = judge.judge_one(candidate)
            record = verdict.to_row()
            record.update({
                "run_id": run_id,
                "family": candidate.family,
                "families": row.get("families", [candidate.family]),
                "pattern_id": candidate.pattern_id,
                "match_count": row.get("match_count", 1),
                "surface": candidate.surface,
                "day": candidate.day,
                "chat_id": candidate.chat_id,
            })
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
            written += 1

    judge.close()

    # Coverage is measured over verdicts that are real answers, from any judge —
    # a passage read by a person last week is still read this week.
    all_verdicts = load_verdicts(root, run_id)
    judged_total = sum(1 for row in all_verdicts.values()
                       if not row.get("awaiting_review"))
    summary = {
        "stage": "x3_classify",
        "run_id": run_id,
        "generated_at": _now(),
        "judge": judge_name,
        "judge_name": getattr(judge, "name", judge_name),
        "matches_total": len(matches),
        "candidates_total": len(candidates),
        "passage_note": (
            f"{len(matches):,} pattern matches fall in {len(candidates):,} distinct "
            "passages. A reader reads a passage once, so the passage is the unit "
            "of judgement and of every coverage figure below."
        ),
        "judged_total": judged_total,
        "judged_this_run": written,
        "unreadable": unreadable,
        "remaining": max(0, len(candidates) - judged_total - unreadable),
        "read_fraction_pct": round(100.0 * judged_total / len(candidates), 1),
        "verdicts_this_run": counts,
        "survival_rate_pct": (round(100.0 * counts[VERDICT_REAL] / written, 1)
                              if written else None),
        "floor_note": (
            f"{judged_total:,} of {len(candidates):,} candidates have been read, "
            "strongest-first. Everything unread stays a candidate and is never "
            "counted as a finding, which is why every published count is a floor."
        ),
        "not_confirmed_note": (
            "A verdict here is one reader's answer. Nothing is confirmed until an "
            "independent pass has agreed with it — see x4."
        ),
    }
    if getattr(judge, "pending_count", 0):
        summary["worksheet"] = str(getattr(judge, "path", ""))
        summary["awaiting_review"] = judge.pending_count

    (root / "_reports" / run_id / SUMMARY_FILE).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser(description="Read flagged passages and judge them.")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--run-id", help="which scan run to judge (default: the newest)")
    ap.add_argument("--judge", default="demo", choices=["demo", "worksheet", "anthropic"])
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"most passages to read in this run (default: {DEFAULT_LIMIT}; "
                         "0 for no limit)")
    ap.add_argument("--model", help="model id, for the anthropic judge")
    ap.add_argument("--worksheet", default="review-worksheet.md",
                    help="where the review file lives, for the worksheet judge")
    ap.add_argument("--restart", action="store_true",
                    help="ignore verdicts already recorded for this run")
    args = ap.parse_args()

    root = Path(args.data_dir)
    run_id = args.run_id or latest_run(root)
    if not run_id:
        log("x3: no scan output found. Run the scan first.")
        return 1

    options: JsonObject = {"worksheet": args.worksheet}
    if args.model:
        options["model"] = args.model

    result = classify(root, run_id, judge_name=args.judge, limit=args.limit,
                      resume=not args.restart, **options)
    if "error" in result:
        log(f"x3: {result['error']}")
        return 1

    print(json.dumps(result, indent=2))
    if result.get("awaiting_review"):
        log(f"\nWrote {result['worksheet']} with {result['awaiting_review']} passage(s) "
            "to read.\nFill in each VERDICT line, then run this again to read it back.")
    else:
        log(f"\n{result['judged_total']:,} of {result['candidates_total']:,} candidates "
            f"read ({result['read_fraction_pct']}%). Nothing is confirmed until x4 agrees.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
