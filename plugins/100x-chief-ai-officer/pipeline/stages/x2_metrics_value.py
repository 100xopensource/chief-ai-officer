#!/usr/bin/env python3
"""x2 — the Value X-Ray's numbers. Deterministic; no model reads anything.

The question this answers: the money was spent — what did it actually buy, and
what is getting in the way of it buying more?

Spend is easy to measure and tells you nothing about value. This stage measures
the three things that do: how many people genuinely use it, what they reach for,
and where the tooling fights them.

What it measures
----------------
  adoption     how many people were active, how that moved, and how deep the
               usage goes rather than just how wide
  depth        the split between people who use it once and people who use it
               all day. An average hides both.
  what got used connectors and skills, ranked, each with the date it first
               appeared
  friction     tools that fail. An error rate is the clearest signal available
               that spend is buying frustration rather than output.
  abandonment  things that were used and then stopped being used. The most
               expensive failure mode, because it never shows up as an error.

The birth check
---------------
Every dimension carries the date it first appeared, and nothing is trended
across a dimension younger than the window. A connector that first appeared in
week three shows infinite growth by week four, and that artefact has been
published as a finding before being caught. `first_seen` is computed here so no
later stage has to remember to ask.

Usage
-----
    python3 -m pipeline.stages.x2_metrics_value --data-dir data
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
from pipeline.stages import _window as W
from pipeline.stages.x0_gapcheck import resolve_window

JsonObject = dict[str, Any]

REPORT = "value"

# Activity fields in analytics_users that mean "this person did something".
# Kept explicit rather than "any non-zero column" so a new column added upstream
# does not silently change what the word "active" means.
ACTIVITY_FIELDS = (
    "chat_message_count",
    "cc_distinct_session_count",
    "cowork_message_count",
    "cowork_action_count",
    "web_search_count",
)

# Someone with more days of activity than this in a week is using it as part of
# how they work, not trying it out.
HABIT_DAYS = 3


def compute(root: Path, window: JsonObject) -> JsonObject:
    start, end = window["week_start"], window["week_end"]
    prior_start, prior_end = window["comparison_start"], window["comparison_end"]

    return {
        "stage": "x2_metrics_value",
        "report": REPORT,
        "generated_at": _now(),
        "window": window,
        "adoption": _adoption(root, start, end, prior_start, prior_end),
        "depth": _depth(root, start, end),
        "connectors": _connectors(root, start, end, prior_start, prior_end),
        "skills": _skills(root, start, end),
        "friction": _friction(root, start, end),
        "trend": _weekly_trend(root, window),
        "coverage": _coverage(root, start, end),
    }


# --------------------------------------------------------------------------

def _active_users(root: Path, start: str, end: str) -> dict[str, int]:
    """user_id -> number of distinct days with any recorded activity."""
    days: dict[str, set[str]] = defaultdict(set)
    if not W.dataset_exists(root, "analytics_users"):
        return {}
    for row in W.rows_in_window(root, "analytics_users", start, end):
        user = str(row.get("user_id") or "")
        if not user:
            continue
        if any(isinstance(row.get(f), (int, float)) and row[f] > 0 for f in ACTIVITY_FIELDS):
            days[user].add(str(row.get("day")))
    return {user: len(seen) for user, seen in days.items()}


def _adoption(root: Path, start: str, end: str,
              prior_start: str, prior_end: str) -> JsonObject:
    now = _active_users(root, start, end)
    before = _active_users(root, prior_start, prior_end)

    seats = 0
    if W.dataset_exists(root, "directory_users"):
        from pipeline.lake import datalake as lake
        seats = sum(1 for _ in lake.iter_snapshot(root, "directory_users"))

    # The prior window is four weeks long, so compare against its weekly shape
    # rather than its total, which would make any single week look like collapse.
    prior_weekly = _weekly_active_average(root, prior_start, prior_end)

    return {
        "available": bool(now),
        "active_people": len(now),
        "active_people_described": W.describe_people(len(now)),
        "seats_total": seats,
        "share_of_seats_pct": W.safe_share(len(now), seats) if seats else None,
        "prior_weekly_active_average": prior_weekly,
        "change_vs_prior_pct": (W.safe_share(len(now) - prior_weekly, prior_weekly)
                                if prior_weekly else None),
        "newly_active": len(set(now) - set(before)),
        "stopped": len(set(before) - set(now)),
        "reader_note": (
            f"{W.describe_people(len(now))} did something with Claude in this week"
            + (f", out of {seats:,} who hold a seat" if seats else "")
            + ". Compared with the four weeks before it, "
            + (f"that is {W.safe_share(len(now) - prior_weekly, prior_weekly):+.0f}%."
               if prior_weekly else "there is no earlier week to compare against.")
        ),
    }


def _weekly_active_average(root: Path, start: str, end: str) -> float:
    """Average weekly active people across a multi-week window."""
    weeks: dict[str, set[str]] = defaultdict(set)
    if not W.dataset_exists(root, "analytics_users"):
        return 0.0
    for row in W.rows_in_window(root, "analytics_users", start, end):
        user = str(row.get("user_id") or "")
        day = str(row.get("day") or "")
        if not user or not day:
            continue
        if any(isinstance(row.get(f), (int, float)) and row[f] > 0 for f in ACTIVITY_FIELDS):
            weeks[_week_of(day)].add(user)
    return round(sum(len(v) for v in weeks.values()) / len(weeks), 1) if weeks else 0.0


def _depth(root: Path, start: str, end: str) -> JsonObject:
    """One-off users versus people for whom it is part of the job."""
    days = _active_users(root, start, end)
    if not days:
        return {"available": False, "why": "no per-person activity in this window"}

    habitual = [u for u, count in days.items() if count >= HABIT_DAYS]
    once = [u for u, count in days.items() if count == 1]

    return {
        "available": True,
        "active_people": len(days),
        "habitual_people": len(habitual),
        "habitual_described": W.describe_people(len(habitual)),
        "single_day_people": len(once),
        "single_day_described": W.describe_people(len(once)),
        "habitual_share_pct": W.safe_share(len(habitual), len(days)),
        "reader_note": (
            f"Of everyone active, {W.describe_people(len(habitual))} used it on "
            f"{HABIT_DAYS} days or more — that is the group for whom this is part of "
            f"how they work. {W.describe_people(len(once))} used it on exactly one day. "
            "An average across both would describe neither."
        ),
    }


def _connectors(root: Path, start: str, end: str,
                prior_start: str, prior_end: str) -> JsonObject:
    """What data Claude was actually reaching for, and what it stopped reaching for."""
    if not W.dataset_exists(root, "analytics_connectors"):
        return {"available": False, "why": "no analytics_connectors dataset"}

    from pipeline.lake import datalake as lake
    everything = list(lake.iter_dataset(root, "analytics_connectors"))
    born = det.first_seen(everything, "connector_name")

    def usage(rows: list[JsonObject]) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for row in rows:
            name = str(row.get("connector_name") or "")
            calls = (row.get("read_call_count") or 0) + (row.get("write_call_count") or 0)
            if name:
                out[name] += float(calls)
        return dict(out)

    now = usage([r for r in everything if start <= str(r.get("day") or "") <= end])
    before = usage([r for r in everything if prior_start <= str(r.get("day") or "") <= prior_end])
    before_weekly = {k: v / 4.0 for k, v in before.items()}

    ranked = []
    for name, calls in sorted(now.items(), key=lambda kv: -kv[1]):
        was = before_weekly.get(name, 0.0)
        # Only trend a connector that existed for the whole comparison window.
        comparable = bool(born.get(name)) and born[name] <= prior_start
        ranked.append({
            "name": name,
            "calls": int(calls),
            "first_seen": born.get(name),
            "comparable": comparable,
            "change_pct": W.safe_share(calls - was, was) if (comparable and was) else None,
            "unnamed": det.is_unnamed_connector(name),
        })

    faded = [{"name": name,
              "was_weekly_calls": int(was),
              "now_calls": int(now.get(name, 0)),
              "first_seen": born.get(name)}
             for name, was in sorted(before_weekly.items(), key=lambda kv: -kv[1])
             if was >= 5 and now.get(name, 0) < was * 0.5
             and born.get(name, "9999") <= prior_start]

    return {
        "available": True,
        "in_use": ranked,
        "count_in_use": len(ranked),
        "unnamed_count": sum(1 for r in ranked if r["unnamed"]),
        "faded": faded,
        "birth_note": (
            "Each connector carries the date it first appeared. Anything younger than "
            "the comparison window is marked not comparable and is never shown as growth "
            "— a connector that did not exist last month always looks like infinite growth."
        ),
        "faded_note": (
            "A connector whose use halved or worse against its own earlier weekly average. "
            "This is the most expensive failure mode available: nothing errors, nothing "
            "alerts, people simply go back to doing it by hand."
        ) if faded else "Nothing in use dropped by half or more against its own average.",
    }


def _skills(root: Path, start: str, end: str) -> JsonObject:
    if not W.dataset_exists(root, "analytics_skills"):
        return {"available": False, "why": "no analytics_skills dataset"}

    rows = list(W.rows_in_window(root, "analytics_skills", start, end))
    by_skill: dict[str, dict[str, float]] = defaultdict(lambda: {"invocations": 0.0, "people": 0.0})
    for row in rows:
        name = str(row.get("skill_name") or "")
        if not name:
            continue
        by_skill[name]["invocations"] += float(row.get("invocation_count") or 0)
        by_skill[name]["people"] = max(by_skill[name]["people"],
                                       float(row.get("distinct_user_count") or 0))

    ranked = [{"name": name,
               "invocations": int(v["invocations"]),
               "people_described": W.describe_people(int(v["people"]))}
              for name, v in sorted(by_skill.items(), key=lambda kv: -kv[1]["invocations"])]

    return {
        "available": True,
        "in_use": ranked[:12],
        "count_in_use": len(ranked),
        "invocations_total": int(sum(v["invocations"] for v in by_skill.values())),
    }


def _friction(root: Path, start: str, end: str) -> JsonObject:
    """Where the tooling fails. Errors are spend that bought nothing."""
    if not W.dataset_exists(root, "analytics_connectors"):
        return {"available": False, "why": "no analytics_connectors dataset"}

    rows = list(W.rows_in_window(root, "analytics_connectors", start, end))
    per: dict[str, dict[str, float]] = defaultdict(lambda: {"calls": 0.0, "errors": 0.0})
    for row in rows:
        name = str(row.get("connector_name") or "")
        if not name:
            continue
        per[name]["calls"] += float((row.get("read_call_count") or 0)
                                    + (row.get("write_call_count") or 0)
                                    + (row.get("unclassified_call_count") or 0))
        per[name]["errors"] += float(row.get("error_call_count") or 0)

    ranked = []
    for name, v in per.items():
        attempts = v["calls"] + v["errors"]
        if attempts < 10:  # too few attempts for a rate to mean anything
            continue
        ranked.append({
            "name": name,
            "attempts": int(attempts),
            "errors": int(v["errors"]),
            "error_rate_pct": W.safe_share(v["errors"], attempts),
        })
    ranked.sort(key=lambda r: -r["error_rate_pct"])

    total_attempts = sum(r["attempts"] for r in ranked)
    total_errors = sum(r["errors"] for r in ranked)

    return {
        "available": True,
        "by_connector": ranked,
        "overall_error_rate_pct": W.safe_share(total_errors, total_attempts),
        "worst": ranked[0] if ranked else None,
        "threshold_note": (
            "Connectors with fewer than ten attempts in the week are left out; a rate "
            "computed over a handful of calls says more about the handful than the tool."
        ),
        "reader_note": (
            f"{W.safe_share(total_errors, total_attempts):.0f}% of all connector calls "
            "failed this week. Every failure is spend that bought nothing, and a person "
            "who had to do the thing another way."
        ) if ranked else "No connector had enough calls this week to judge its error rate.",
    }


def _weekly_trend(root: Path, window: JsonObject, span: int = 10) -> JsonObject:
    points = []
    for start, end in W.weeks_back(window["week_start"], span):
        active = _active_users(root, start, end)
        points.append({
            "label": _short_label(start),
            "week_start": start,
            "value": len(active),
            "current": start == window["week_start"],
        })
    return {
        "title": "People using Claude, by week",
        "unit": "people",
        "weeks": points,
        "caption": (
            "Each point counts people with any recorded activity in one complete "
            "Monday-to-Sunday week. The marked point is the week this report covers. "
            "Activity is only visible for products that report it, so this is a floor."
        ),
    }


def _coverage(root: Path, start: str, end: str) -> JsonObject:
    rows = list(W.rows_in_window(root, "analytics_cost", start, end))
    by_product = W.group_sum(rows, "product", "amount_usd")
    grand = sum(by_product.values())
    visible = by_product.get("chat", 0.0)
    return {
        "content_visible_share_pct": W.safe_share(visible, grand),
        "reader_note": (
            "Usage counts cover every product that reports activity. Reading what people "
            f"were actually doing is narrower — only the {W.safe_share(visible, grand):.0f}% "
            "of spend that is chat can be read. Into the rest this report cannot see at "
            "all, so anything here about the work itself describes the readable share "
            "and nothing more."
        ),
    }


def _week_of(day: str) -> str:
    parsed = date.fromisoformat(day)
    monday = parsed.toordinal() - parsed.weekday()
    return date.fromordinal(monday).isoformat()


def _short_label(day: str) -> str:
    parsed = date.fromisoformat(day)
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{months[parsed.month - 1]} {parsed.day}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute the Value X-Ray's numbers.")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--as-of", help="run date (YYYY-MM-DD); defaults to the day after "
                                    "the newest day of data in the lake")
    ap.add_argument("--out", help="write the metrics record here")
    args = ap.parse_args()

    root = Path(args.data_dir)
    result = compute(root, resolve_window(W.resolve_as_of(root, args.as_of)))

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
