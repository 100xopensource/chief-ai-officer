#!/usr/bin/env python3
"""x0 — the gap check. What can this lake answer, and what is it missing?

Run this before anything else. It answers three questions and nothing more:

  1. What time window does this run actually cover? A report that says "last
     week" must mean one specific Monday-to-Sunday, resolved from the run date
     and stated once, so every stage downstream agrees.
  2. Which reports can be produced from the data present? Each report needs a
     specific set of datasets. If one is missing, that report is blocked — and
     saying so is more useful than producing it from nothing.
  3. What is stale, thin, or absent? Not to stop the run, but so the report can
     tell its reader what it could not see.

Why this exists as a stage rather than a check inside each report
-----------------------------------------------------------------
The predecessor asked "is the lake fresh?" and answered yes or no. That is the
wrong question. A lake can be perfectly fresh and still unable to answer the
question you are asking, because the dataset that would answer it was never
pulled. Freshness is one input to a gap check, not a substitute for one.

The output is a plain record: which reports are clear to run, which are blocked
and on what, and the exact dates the window resolves to. Later stages read it
rather than each re-deriving a window and drifting apart.

Standard library only. This must run when the lake is empty, when pandas is
missing, and when nothing has ever been pulled — the three moments a person is
most likely to run it.

Usage
-----
    python3 -m pipeline.stages.x0_gapcheck --data-dir data
    python3 -m pipeline.stages.x0_gapcheck --data-dir data --as-of 2026-07-20
    python3 -m pipeline.stages.x0_gapcheck --data-dir data --json

Exit codes, so a caller can branch:
    0  every report is clear to run
    1  at least one report is blocked
    2  the lake does not exist
"""

from __future__ import annotations

# Runnable two ways: `python3 -m pipeline.stages.x0_gapcheck` and
# `python3 <path>/x0_gapcheck.py`. The second has no package context.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline import config
from pipeline.lake import schema

JsonObject = dict[str, Any]

STATE_FILE = "_state.json"

# What each report cannot be produced without, and what merely makes it better.
#
# The split matters. A missing required dataset means the report would be
# invented rather than measured, so it is blocked. A missing optional dataset
# means the report is narrower than it could be — which the report says out
# loud rather than hiding.
REPORT_NEEDS = {
    "waste": {
        "title": "Waste Ledger",
        "reader": "Finance",
        "required": ["analytics_cost"],
        "optional": ["analytics_user_cost", "analytics_usage", "analytics_users",
                     "directory_users", "directory_group_members"],
    },
    "value": {
        "title": "Value X-Ray",
        "reader": "Head of AI",
        "required": ["analytics_users"],
        "optional": ["analytics_cost", "analytics_connectors", "analytics_skills",
                     "dim_chat", "fact_message", "directory_group_members"],
    },
    "exposure": {
        "title": "Exposure Report",
        "reader": "Compliance",
        "required": ["fact_block"],
        "optional": ["dim_chat", "fact_message", "fact_attachment",
                     "analytics_connectors", "analytics_cost"],
    },
}

# A dataset older than this is reported as stale. It does not block anything:
# a fortnight-old lake still answers questions, it just answers them about a
# fortnight ago, and the reader is entitled to know that.
STALE_AFTER_DAYS = 8


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------

def resolve_window(as_of: date) -> JsonObject:
    """Turn a run date into the exact dates every downstream stage will use.

    "Last week" is the most recently completed Monday-to-Sunday. Running on a
    Wednesday reports the week that ended on Sunday — never the partial week in
    progress, because a partial week compared against full ones always looks
    like a collapse and has been published as one before.
    """
    monday_this_week = as_of - timedelta(days=as_of.weekday())
    week_start = monday_this_week - timedelta(days=7)
    week_end = week_start + timedelta(days=6)

    prior_start = week_start - timedelta(days=28)
    prior_end = week_start - timedelta(days=1)

    return {
        "as_of": as_of.isoformat(),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "week_label": _week_label(week_start, week_end),
        "comparison_start": prior_start.isoformat(),
        "comparison_end": prior_end.isoformat(),
        "comparison_label": "the four weeks before that, averaged",
        "partial_week_start": monday_this_week.isoformat(),
        "note": (
            "'Last week' is the most recently completed Monday to Sunday. The "
            "week in progress is never compared against completed weeks; a "
            "part-week always reads as a collapse."
        ),
    }


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _week_label(start: date, end: date) -> str:
    """'Week of July 6-12, 2026' — dates a reader recognises, never week numbers."""
    if start.month == end.month:
        return f"Week of {_MONTHS[start.month - 1]} {start.day}–{end.day}, {end.year}"
    return (f"Week of {_MONTHS[start.month - 1]} {start.day} – "
            f"{_MONTHS[end.month - 1]} {end.day}, {end.year}")


# --------------------------------------------------------------------------
# reading the lake without depending on it
# --------------------------------------------------------------------------

def survey_datasets(root: Path) -> dict[str, JsonObject]:
    """What is actually on disk, by walking it — not by trusting the state file.

    The state file records what a run believed it wrote. The directory records
    what exists. When those disagree the directory is right, and the disagreement
    is itself worth reporting.
    """
    found: dict[str, JsonObject] = {}
    if not root.exists():
        return found

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_") or entry.name == "raw":
            continue
        partitions = sorted(p.name.replace("week=", "")
                            for p in entry.iterdir() if p.name.startswith("week="))
        snapshot = entry / "snapshot.jsonl"
        rows = 0
        for path in list(entry.glob("week=*/part.jsonl")) + ([snapshot] if snapshot.exists() else []):
            try:
                with path.open(encoding="utf-8") as handle:
                    rows += sum(1 for line in handle if line.strip())
            except OSError:
                continue
        found[entry.name] = {
            "rows": rows,
            "partitions": partitions,
            "kind": "snapshot" if snapshot.exists() and not partitions else "partitioned",
            "latest_partition": partitions[-1] if partitions else None,
        }
    return found


def read_state(root: Path) -> JsonObject:
    path = root / STATE_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _age_days(stamp: str | None, as_of: date) -> float | None:
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(str(stamp).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    reference = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)
    return round((reference - moment).total_seconds() / 86400.0, 1)


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------

def gapcheck(root: Path, as_of: date | None = None) -> JsonObject:
    as_of = as_of or datetime.now(timezone.utc).date()
    window = resolve_window(as_of)
    present = survey_datasets(root)
    state = read_state(root).get("datasets", {}) or {}

    reports: dict[str, JsonObject] = {}
    for key, spec in REPORT_NEEDS.items():
        missing = [d for d in spec["required"] if d not in present or present[d]["rows"] == 0]
        thin = [d for d in spec["optional"] if d not in present or present[d]["rows"] == 0]
        reports[key] = {
            "title": spec["title"],
            "reader": spec["reader"],
            "status": "blocked" if missing else "clear",
            "blocked_on": missing,
            "narrower_without": thin,
            "reader_note": _reader_note(spec, missing, thin, root),
        }

    stale = []
    for name, info in present.items():
        stamp = (state.get(name) or {}).get("data_refreshed_at") or \
                (state.get(name) or {}).get("last_run") or \
                (state.get(name) or {}).get("last_parse")
        age = _age_days(stamp, as_of)
        if age is not None and age > STALE_AFTER_DAYS:
            stale.append({"dataset": name, "age_days": age, "last_written": stamp})

    undeclared = sorted(set(present) - set(state))
    declared_missing = sorted(d for d in state if d not in present)

    # Three things this check used to miss, each of which let a wrong number
    # through with everything else looking fine.
    #
    # shape      a dataset can be present, fresh and full of rows and still not
    #            carry the field a stage is about to read. That reads downstream
    #            as a measurement of zero, which is the most reassuring possible
    #            way to be wrong.
    # coverage   the datasets do not all reach the same day. Cost reaches the end
    #            of the week; activity finalises a couple of days behind it, so
    #            the reported week gets measured over five days for some numbers
    #            and compared against four seven-day baselines.
    # decisions  the operator's standing exclusions and aliases, so a decision
    #            being honoured is visible and one being ignored is too.
    shape = schema.check(root)
    coverage = W_coverage(root, window["week_start"], window["week_end"])
    decisions = config.describe(root)

    blocked = [k for k, r in reports.items() if r["status"] == "blocked"]
    return {
        "stage": "x0_gapcheck",
        "generated_at": _now(),
        "data_dir": str(root),
        "lake_exists": root.exists(),
        "window": window,
        "reports": reports,
        "datasets_present": {k: v["rows"] for k, v in sorted(present.items())},
        "stale": sorted(stale, key=lambda s: -s["age_days"]),
        "on_disk_but_not_in_state": undeclared,
        "in_state_but_not_on_disk": declared_missing,
        "shape": shape,
        "shape_broken": [r for r in shape if not r["ok"]],
        "coverage": coverage,
        "decisions": decisions,
        "decisions_broken": [d["file"] for d in decisions if d.get("error")],
        "clear": [k for k, r in reports.items() if r["status"] == "clear"],
        "blocked": blocked,
        "summary": _summary(reports, stale, root,
                            [d["file"] for d in decisions if d.get("error")]),
    }


def W_coverage(root: Path, start: str, end: str) -> JsonObject:
    """Per-dataset coverage of the reported week. Imported late to keep this
    stage importable from `_window`, which imports this one for the window."""
    from pipeline.stages import _window as W
    return W.window_coverage(
        root, ["analytics_cost", "analytics_user_cost", "analytics_users",
               "analytics_connectors", "analytics_skills"], start, end)


# What a person actually does about a missing dataset, said at the point they
# find out it is missing. The message used to name the table and stop there —
# "fact_block is not in this lake. Pull it, then run again" — which names a
# thing the reader has never heard of and an action they cannot take, since
# the pull it refers to is gated on a consent decision the message never
# mentions. A blocked report should be the most helpful message in the run: it
# is the one the reader is stuck on.
REMEDY = {
    "fact_block": (
        "This is the conversation text, and it is the one pull that reads what "
        "people wrote. It is deliberately gated: the first run stops, explains "
        "what will be stored and where, and asks for the name of the person "
        "accountable for the decision, which is saved next to the data.\n"
        "{indent}To start that:  caio pull content --data-dir {root}"
    ),
    "dim_chat": "Comes with the conversation pull:  caio pull content --data-dir {root}",
    "fact_message": "Comes with the conversation pull:  caio pull content --data-dir {root}",
    "fact_attachment": "Comes with the conversation pull:  caio pull content --data-dir {root}",
    "analytics_cost": "caio pull analytics --data-dir {root}",
    "analytics_users": "caio pull analytics --data-dir {root}",
    "analytics_user_cost": "caio pull analytics --data-dir {root}",
    "analytics_usage": "caio pull analytics --data-dir {root}",
    "analytics_connectors": "caio pull analytics --data-dir {root}",
    "analytics_skills": "caio pull analytics --data-dir {root}",
    "directory_users": "caio pull directory --data-dir {root}",
    "directory_group_members": "caio pull directory --data-dir {root}",
}


def _remedy(missing: list[str], root: Path, indent: str = " " * 11) -> str:
    """The command that fixes it, deduplicated, or nothing if we cannot say.

    `indent` lines continuation lines up under the first, which sits at a
    different column in the printed check than in a report's own reader note.
    """
    seen: list[str] = []
    for dataset in missing:
        text = REMEDY.get(dataset)
        if text:
            text = text.format(root=root, indent=indent)
            if text not in seen:
                seen.append(text)
    return ("\n" + indent).join(seen)


def _reader_note(spec: JsonObject, missing: list[str], thin: list[str],
                 root: Path | None = None) -> str:
    """One sentence a report can print about its own coverage."""
    if missing:
        note = (f"The {spec['title']} cannot be produced: "
                f"{_english_list(missing)} {'is' if len(missing) == 1 else 'are'} "
                "not in this lake.")
        indent = " " * 9
        remedy = _remedy(missing, root or Path("data"), indent)
        return f"{note}\n{indent}{remedy}" if remedy else f"{note} Pull it, then run again."
    if thin:
        return (f"The {spec['title']} can be produced, but without "
                f"{_english_list(thin)} it answers less than it could. "
                "The report says so where it matters.")
    return f"The {spec['title']} has everything it needs."


def _english_list(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _summary(reports: dict[str, JsonObject], stale: list[JsonObject], root: Path,
             broken_config: list[str] | None = None) -> str:
    if not root.exists():
        return (f"No lake at {root}. Nothing can be reported until data is pulled, or "
                "until a synthetic lake is generated to try the tools on.")
    # A config that will not parse stops every build, so nothing is ready to run
    # regardless of what the data can answer. Saying "ready" above a BROKEN line
    # would make this command contradict itself, and the build contradict it.
    if broken_config:
        return (f"{_english_list(broken_config)} will not parse, so no report can be "
                "built until it is fixed. Nothing else here has been ruled out; the "
                "config is simply read first.")
    clear = [r["title"] for r in reports.values() if r["status"] == "clear"]
    blocked = [r["title"] for r in reports.values() if r["status"] == "blocked"]
    parts = []
    if clear:
        parts.append(f"Ready to run: {_english_list(clear)}.")
    if blocked:
        parts.append(f"Blocked: {_english_list(blocked)}.")
    if stale:
        oldest = stale[0]
        parts.append(f"Oldest data is {oldest['dataset']} at {oldest['age_days']} days; "
                     "numbers describe when the data was pulled, not today.")
    return " ".join(parts) or "Nothing found to report on."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def render_human(result: JsonObject) -> str:
    lines = [
        f"Gap check — {result['data_dir']}",
        f"  Window     {result['window']['week_label']}"
        f"  ({result['window']['week_start']} to {result['window']['week_end']})",
        f"  Compared   {result['window']['comparison_label']}",
        "",
    ]
    for report in result["reports"].values():
        mark = "clear  " if report["status"] == "clear" else "BLOCKED"
        lines.append(f"  {mark}  {report['title']:<16} for {report['reader']}")
        if report["blocked_on"]:
            lines.append(f"           missing: {', '.join(report['blocked_on'])}")
            # The remedy, here, where somebody is stuck. Naming the missing
            # table and stopping told the reader the name of a thing they had
            # never heard of and nothing they could do about it.
            remedy = _remedy(report["blocked_on"], Path(result["data_dir"]))
            if remedy:
                lines.append(f"           {remedy}")
        elif report["narrower_without"]:
            lines.append(f"           narrower without: {', '.join(report['narrower_without'])}")
    broken = result.get("shape_broken") or []
    if broken:
        lines.append("")
        lines.append("  Present but the wrong shape — these will be reported as not")
        lines.append("  measurable rather than as zero:")
        for item in broken:
            lines.append(f"    {item['dataset']:<26} {item['consumer']}")
            lines.append(f"      missing: {', '.join(item['missing'])}")

    coverage = result.get("coverage") or {}
    if coverage.get("any_short"):
        lines.append("")
        lines.append("  Does not reach the end of the reported week:")
        for name in coverage["short_datasets"]:
            entry = coverage["datasets"][name]
            lines.append(f"    {name:<26} stops at {entry['last_day']} "
                         f"({entry['days_with_data']} of {entry['expected_days']} days)")
        lines.append("    A week measured over fewer days reads as a fall that did not")
        lines.append("    happen, so anything drawn from these is labelled, not compared.")

    in_force = [d for d in (result.get("decisions") or []) if d.get("present")]
    if in_force:
        lines.append("")
        lines.append("  Standing decisions being honoured:")
        for entry in in_force:
            # A file that will not parse must never print as "0 entry(s)". That
            # is indistinguishable from a file recording no exclusions, and it
            # is wrong in the reassuring direction: the run that follows refuses
            # to build, and this is the command meant to have said why.
            if entry.get("error"):
                lines.append(f"    {entry['file']:<26} BROKEN — it will stop the next build")
                lines.append(f"      {entry['error']}")
            else:
                lines.append(f"    {entry['file']:<26} {entry.get('entries', 0)} entry(s)")

    if result["stale"]:
        lines.append("")
        lines.append("  Stale (still usable, just older than it looks):")
        for item in result["stale"][:6]:
            lines.append(f"    {item['dataset']:<26} {item['age_days']} days")
    if result["in_state_but_not_on_disk"]:
        lines.append("")
        lines.append("  Recorded as written but not on disk: "
                     + ", ".join(result["in_state_but_not_on_disk"]))
    lines.append("")
    lines.append(f"  {result['summary']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="What can this lake answer, and what is it missing?")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--as-of", help="run date (YYYY-MM-DD); defaults to today in UTC")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--out", help="also write the record here")
    args = ap.parse_args()

    as_of = None
    if args.as_of:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            print(f"x0: --as-of must be YYYY-MM-DD, got {args.as_of!r}", file=sys.stderr)
            return 2

    root = Path(args.data_dir)
    result = gapcheck(root, as_of)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(result, indent=2) if args.json else render_human(result))

    if not result["lake_exists"]:
        return 2
    return 1 if result["blocked"] else 0


if __name__ == "__main__":
    sys.exit(main())
