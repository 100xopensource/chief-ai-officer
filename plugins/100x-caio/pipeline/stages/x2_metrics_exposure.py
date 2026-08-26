#!/usr/bin/env python3
"""x2 — the Exposure Report's numbers. Deterministic; no model reads anything.

The question this answers: what sensitive material reached the AI, how did it
get there, and how much of it can we actually see?

The distinction this whole stage exists to preserve
---------------------------------------------------
A pattern match is a LEAD. A lead that a person or a model has read and judged
is a FINDING. This stage counts leads and computes what fraction of them can be
expected to survive being read, using the precision each pattern family carries
from its own history. It never promotes a lead to a finding — that happens at
x3 and is checked at x4, independently.

Every number that leaves here is therefore labelled, and the report layer is
built so that publishing a lead count as if it were a finding count is awkward
rather than easy. Roughly 44% of leads have historically survived reading, and
some families survive at 0%; a lead count published as a result overstates
reality by more than a factor of two.

What it measures
----------------
  leads          by family, by surface, by week, from the scan stage
  survival       leads weighted by each family's historical precision, giving
                 an expected finding count stated as a range, never a point
  reach          how many people and conversations are involved, floored so a
                 small group is never rendered as a digit
  arrival        how the material got there: typed by a person, sent into a
                 tool, or handed back by one. The single most decision-relevant
                 split in the report, because only one of the three is
                 addressed by talking to people.
  blind spots    what the conversation feed cannot see at all, priced by spend
  governance     connectors nobody owns, conversations that export empty,
                 attachments whose contents are not retrievable

Usage
-----
    python3 -m pipeline.stages.x2_metrics_exposure --data-dir data
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.detectors import families as det
from pipeline.lake import datalake as lake
from pipeline.stages import _window as W
from pipeline.stages.x0_gapcheck import resolve_window
from pipeline.stages.x3_classify import group_by_passage, load_verdicts
from pipeline.stages.x4_verify import load_verifications, resolve

JsonObject = dict[str, Any]

REPORT = "exposure"

# What a lead from each precision class has historically been worth once read.
# These are survival rates, measured over prior audits, not guesses. They are
# used only to bound an expectation — never to promote anything.
SURVIVAL = {
    "high": (0.75, 0.95),
    "medium": (0.35, 0.65),
    "low": (0.05, 0.25),
    "zero-without-context": (0.0, 0.05),
}
DEFAULT_SURVIVAL = (0.2, 0.6)

# How a reader is told each surface happened. The wording matters more than the
# key: "returned by a tool" and "typed by a person" call for different actions
# from different people, and a report that merges them tells nobody what to do.
SURFACE_ENGLISH = {
    "typed_prompt": "typed into a prompt by a person",
    "tool_input": "sent into a tool by the assistant",
    "tool_output": "handed back by a tool inside its results",
    "attachment": "carried in an attached file",
}


def compute(root: Path, window: JsonObject, run_id: str | None = None) -> JsonObject:
    scan = _load_scan(root, run_id)
    candidates = _load_candidates(root, scan.get("run_id")) if scan else []

    start, end = window["week_start"], window["week_end"]
    in_week = [c for c in candidates if start <= str(c.get("day") or "") <= end]

    return {
        "stage": "x2_metrics_exposure",
        "report": REPORT,
        "generated_at": _now(),
        "window": window,
        "scan_available": bool(scan),
        "scan_run_id": scan.get("run_id") if scan else None,
        "detector_version": scan.get("detector_version") if scan else None,
        "leads": _leads(scan, in_week, candidates),
        "survival": _survival(candidates, in_week),
        "arrival": _arrival(in_week, candidates),
        "reach": _reach(root, in_week, window),
        "trend": _weekly_trend(candidates, window),
        "truncation": _truncation(candidates, in_week),
        "blind_spots": _blind_spots(root, start, end),
        "judgement": _judgement(root, scan.get("run_id") if scan else None,
                                candidates, in_week),
        "governance": (scan or {}).get("governance", {}),
        "reading_this": (
            "Every count here is a count of candidates — a pattern matched, nothing "
            "read. Candidates are not findings. The survival range says what to "
            "expect once they are read; historically about 44% survive, and some "
            "families survive at none. Publishing a candidate count as a result "
            "would overstate what is really there by more than double."
        ),
    }


# --------------------------------------------------------------------------

def _judgement(root: Path, run_id: str | None, everything: list[JsonObject],
               in_week: list[JsonObject]) -> JsonObject:
    """What survived being read — the difference between a match and a finding.

    Until x3 and x4 have run, this reports that nothing has been read and the
    report says so on its own face. That is the honest state, not a gap: a
    match count published as a finding count overstates reality by more than
    double.
    """
    if not run_id:
        return {"available": False,
                "why": "no scan run to judge",
                "reader_note": "Nothing has been read. Every count on this page is "
                               "a pattern match, not a finding."}

    verdicts = load_verdicts(root, run_id)
    if not verdicts:
        return {
            "available": False,
            "why": "nothing judged yet",
            "reader_note": (
                "Nothing has been read yet. Every count on this page is a pattern "
                "match — a candidate — and not a finding. Run the reading pass to "
                "turn candidates into findings."
            ),
        }

    verifications = load_verifications(root, run_id)
    week_ids = {str(c.get("block_id")) for c in in_week}
    passages = group_by_passage(everything)

    statuses = {"confirmed": 0, "disputed": 0, "unverified": 0, "cleared": 0}
    week_statuses = dict(statuses)
    by_family: dict[str, int] = defaultdict(int)
    severities: dict[str, int] = defaultdict(int)

    for passage in passages:
        block_id = str(passage.get("block_id"))
        verdict = verdicts.get(block_id)
        status = resolve(verdict, verifications.get(block_id))
        statuses[status] += 1
        if block_id in week_ids:
            week_statuses[status] += 1
            if status == "confirmed":
                family = det.FAMILIES_BY_ID.get(str(passage.get("family")))
                by_family[family.title if family else str(passage.get("family"))] += 1
                severities[str((verdict or {}).get("severity") or "internal")] += 1

    judged = statuses["confirmed"] + statuses["disputed"] + statuses["cleared"]
    read_pct = W.safe_share(judged, len(passages))
    survival = W.safe_share(statuses["confirmed"], judged) if judged else None

    # Who actually did the reading, counted from the verdicts themselves rather
    # than from the last run's summary. A lake can be part-read by a stand-in
    # and part-read by a person, and crediting the whole thing to whichever ran
    # most recently would overstate how much of it was really read.
    readers: dict[str, int] = defaultdict(int)
    for row in verdicts.values():
        if not row.get("awaiting_review"):
            readers[str(row.get("judged_by") or "unknown")] += 1
    for row in verifications.values():
        readers[str(row.get("judged_by") or "unknown")] += 0  # named, not counted twice

    stand_in_count = readers.get("demo-offline", 0)
    stand_in = stand_in_count > 0
    judge_name = (max(readers, key=readers.get) if readers else "unknown")

    return {
        "available": True,
        "judged_by": judge_name,
        "read_by": dict(sorted(readers.items(), key=lambda kv: -kv[1])),
        "is_a_real_reading": not stand_in,
        "stand_in_share_pct": W.safe_share(stand_in_count, judged) if judged else 0.0,
        "passages_total": len(passages),
        "passages_read": judged,
        "read_share_pct": read_pct,
        "statuses": statuses,
        "week": week_statuses,
        "confirmed_by_family": dict(sorted(by_family.items(), key=lambda kv: -kv[1])),
        "confirmed_by_severity": dict(severities),
        "measured_survival_pct": survival,
        "reader_note": (
            f"{judged:,} of {len(passages):,} flagged passages have been read "
            f"({read_pct:.0f}%). Of those, {survival:.0f}% survived a second, "
            "independent reading and are reported as findings. The rest were "
            "cleared as false alarms."
            if survival is not None else "Nothing has been read yet."
        ),
        "stand_in_warning": (
            f"{W.safe_share(stand_in_count, judged):.0f}% of these verdicts were "
            "produced offline by a stand-in, not by a person and not by a model. "
            "They demonstrate the shape of the result and must not be read as an "
            "actual reading of this material."
        ) if stand_in else None,
        "floor_note": (
            "Passages are read strongest-first, so anything unread is the part "
            "least likely to have survived anyway — but it is unread, and that is "
            "why every count here is a floor."
        ),
    }


def _read_summary(root: Path, run_id: str) -> JsonObject:
    path = root / "_reports" / run_id / "classify_summary.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _load_scan(root: Path, run_id: str | None) -> JsonObject:
    reports = root / "_reports"
    if not reports.is_dir():
        return {}
    if run_id:
        path = reports / run_id / "scan_summary.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    runs = sorted(p for p in reports.iterdir()
                  if p.is_dir() and (p / "scan_summary.json").exists())
    if not runs:
        return {}
    return json.loads((runs[-1] / "scan_summary.json").read_text(encoding="utf-8"))


def _load_candidates(root: Path, run_id: str | None) -> list[JsonObject]:
    if not run_id:
        return []
    path = root / "_reports" / run_id / "candidates.jsonl"
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _leads(scan: JsonObject, in_week: list[JsonObject],
           everything: list[JsonObject]) -> JsonObject:
    by_family: dict[str, int] = defaultdict(int)
    for candidate in in_week:
        by_family[str(candidate.get("family"))] += 1

    titled = {}
    for family_id, count in sorted(by_family.items(), key=lambda kv: -kv[1]):
        family = det.FAMILIES_BY_ID.get(family_id)
        titled[family_id] = {
            "count": count,
            "title": family.title if family else family_id,
            "why_it_matters": family.why if family else "",
        }

    return {
        "week": len(in_week),
        "all_time": len(everything),
        "blocks_scanned": scan.get("blocks_scanned", 0),
        "by_family": titled,
        "by_pattern": _count(in_week, "pattern_id"),
        "distinct_values_week": len({c.get("value_fingerprint") for c in in_week
                                     if c.get("value_fingerprint")}),
        "placeholders_excluded": sum(1 for c in in_week if c.get("likely_placeholder")),
    }


def _survival(everything: list[JsonObject], in_week: list[JsonObject]) -> JsonObject:
    """Expected findings, as a range, from each lead's own precision class."""
    low = high = 0.0
    by_class: dict[str, int] = defaultdict(int)
    for candidate in in_week:
        precision = str(candidate.get("expect_precision") or "")
        by_class[precision or "unclassified"] += 1
        floor, ceiling = SURVIVAL.get(precision, DEFAULT_SURVIVAL)
        low += floor
        high += ceiling

    return {
        "leads_week": len(in_week),
        "expected_findings_low": int(low),
        "expected_findings_high": int(high),
        "leads_by_precision_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        "reader_note": (
            f"Of the {len(in_week):,} passages matched in this week, somewhere between "
            f"{int(low):,} and {int(high):,} would be expected to survive being read. "
            "That range is a prediction from how each pattern has behaved before, not "
            "a result. Nothing is published until it has actually been read."
        ),
        "method_note": (
            "Each pattern family carries its own historical survival rate. Unmistakable "
            "shapes — a vendor key prefix, a private key header — survive most of the "
            "time. Families that only mean something in context survive rarely or never."
        ),
    }


def _arrival(in_week: list[JsonObject], everything: list[JsonObject]) -> JsonObject:
    """Typed by a person, sent into a tool, or returned by one.

    This is the split that decides who owns the fix. Material a person typed is
    a training and policy question. Material a tool handed back is an
    engineering question, and no amount of talking to people will touch it.
    """
    counts = _count(in_week, "surface")
    grand = sum(counts.values()) or 1
    segments = []
    for surface, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        segments.append({
            "surface": surface,
            "english": SURFACE_ENGLISH.get(surface, surface),
            "count": count,
            "share_pct": W.safe_share(count, grand),
        })

    machine = sum(c for s, c in counts.items() if s in ("tool_output", "tool_input"))
    return {
        "segments": segments,
        "machine_share_pct": W.safe_share(machine, grand),
        "typed_share_pct": W.safe_share(counts.get("typed_prompt", 0), grand),
        "reader_note": (
            f"{W.safe_share(machine, grand):.0f}% of what was matched this week arrived "
            "through tools rather than being typed by anyone. That share cannot be "
            "fixed by talking to people; it is fixed by changing what the tools return."
        ),
        "surfaces_scanned": sorted(SURFACE_ENGLISH),
        "error_note": (
            "Errors thrown by tools are scanned too. They are counted as a property of "
            "the tool's output rather than as a surface of their own, because an error "
            "is still something a tool handed back."
        ),
    }


def _reach(root: Path, in_week: list[JsonObject], window: JsonObject) -> JsonObject:
    """How many people and conversations, floored so nobody is identifiable."""
    chats = {str(c.get("chat_id")) for c in in_week if c.get("chat_id")}

    owners: set[str] = set()
    if W.dataset_exists(root, "dim_chat"):
        for row in lake.iter_dataset(root, "dim_chat"):
            if str(row.get("chat_id")) in chats and row.get("user_id"):
                owners.add(str(row["user_id"]))

    active: set[str] = set()
    if W.dataset_exists(root, "analytics_users"):
        for row in W.rows_in_window(root, "analytics_users",
                                    window["week_start"], window["week_end"]):
            if row.get("user_id"):
                active.add(str(row["user_id"]))

    return {
        "conversations_with_leads": len(chats),
        "people_with_leads": len(owners),
        "people_with_leads_described": W.describe_people(len(owners)),
        "people_active": len(active),
        "share_of_active_pct": W.safe_share(len(owners), len(active)) if active else None,
        "floor_note": (
            "A floor, not a total. Only material the conversation feed can read is "
            "counted, and only the strongest matches are read first — so the true "
            "number is at least this and cannot be smaller."
        ),
    }


def _weekly_trend(everything: list[JsonObject], window: JsonObject,
                  span: int = 10) -> JsonObject:
    points = []
    for start, end in W.weeks_back(window["week_start"], span):
        count = sum(1 for c in everything if start <= str(c.get("day") or "") <= end)
        points.append({
            "label": _short_label(start),
            "week_start": start,
            "value": count,
            "current": start == window["week_start"],
        })
    return {
        "title": "Sensitive-data matches, by week",
        "unit": "matches",
        "weeks": points,
        "caption": (
            "Each point counts pattern matches in one complete week — candidates, not "
            "confirmed findings. The marked point is the week this report covers. "
            "Counts are floors: only the conversation feed is scanned, and results "
            "cut off at the source cannot be seen past the cut."
        ),
    }


def _truncation(everything: list[JsonObject], in_week: list[JsonObject]) -> JsonObject:
    """How much of what was scanned was cut off before we could read it."""
    cut = sum(1 for c in in_week if c.get("truncated"))
    return {
        "leads_in_truncated_payloads": cut,
        "share_pct": W.safe_share(cut, len(in_week)),
        "reader_note": (
            "Some tool results are cut off at about 10,000 characters at the source, and "
            "anything past the cut cannot be examined at all. "
            + (f"{cut:,} of this week's matches sit inside one of those. "
               if cut else "None of this week's matches sit inside one of those. ")
            + "It is one of the reasons every count here is a floor."
        ),
    }


def _blind_spots(root: Path, start: str, end: str) -> JsonObject:
    """What the conversation feed cannot see, priced by what it costs."""
    rows = list(W.rows_in_window(root, "analytics_cost", start, end))
    by_product = W.group_sum(rows, "product", "amount_usd")
    grand = sum(by_product.values())
    visible = by_product.get("chat", 0.0)
    unreadable = sorted((k for k in by_product if k != "chat"),
                        key=lambda k: -by_product[k])

    return {
        "readable_products": ["chat"],
        "unreadable_products": unreadable,
        "readable_share_of_spend_pct": W.safe_share(visible, grand),
        "unreadable_share_of_spend_pct": W.safe_share(grand - visible, grand),
        "reader_note": (
            f"The feed this report reads covers chat conversations only — about "
            f"{W.safe_share(visible, grand):.0f}% of what was spent. The other "
            f"{W.safe_share(grand - visible, grand):.0f}% runs in products this feed "
            "cannot see at all, so this report does not guess about them. Turning on "
            "usage-log collection would bring that activity into view; it is a decision, "
            "not a technical obstacle."
        ),
    }


def _count(rows: list[JsonObject], key: str) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for row in rows:
        out[str(row.get(key))] += 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _short_label(day: str) -> str:
    parsed = date.fromisoformat(day)
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{months[parsed.month - 1]} {parsed.day}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute the Exposure Report's numbers.")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--as-of", help="run date (YYYY-MM-DD); defaults to the day after "
                                    "the newest day of data in the lake")
    ap.add_argument("--run-id", help="which scan run to read (default: the newest)")
    ap.add_argument("--out", help="write the metrics record here")
    args = ap.parse_args()

    root = Path(args.data_dir)
    result = compute(root, resolve_window(W.resolve_as_of(root, args.as_of)), args.run_id)

    if not result["scan_available"]:
        print("x2-exposure: no scan output found. Run pipeline.stages.x2_scan first.",
              file=sys.stderr)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
