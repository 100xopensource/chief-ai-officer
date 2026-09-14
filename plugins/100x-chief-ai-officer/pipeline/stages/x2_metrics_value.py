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
  abandonment  things that were used and then stopped being used. The most
               expensive failure mode, because it never shows up as an error.

What it does not measure, and why
---------------------------------
Reliability. Whether a connection failed is not published by this data source —
there is no failure count, and no call count either, anywhere in the analytics
surface. An earlier version of this stage summed four fields that only ever
existed in the project's own demo fixture, got zero for all of them in
production, and published a stat card reading "0% of connector calls failed"
over a source that has never published a failure. A metric a source cannot
support is not rendered here at all; the report says the reliability question
cannot be answered rather than answering it reassuringly.

Usage is therefore counted in distinct sessions and conversations that reached
for a connection, which is what the source does publish. People are counted as
the peak day, never a sum across days: summing distinct-user counts across a
week double-counts anyone active on more than one day, and once inflated a
connection from six people to fifteen.

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

from pipeline import config
from pipeline.detectors import families as det
from pipeline.lake import schema
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

# The tables this report's numbers come from. Their coverage is checked against
# the window, because they do not all reach the same day.
VALUE_DATASETS = ("analytics_users", "analytics_connectors", "analytics_skills",
                  "analytics_cost")

# The per-surface usage counts a connector or skill row carries. Each is a count
# of distinct sessions or conversations in which the thing was reached for —
# which is what this source publishes, in place of the call counts it does not.
USED_FIELDS = ("chat_distinct_conversation_used",
               "cc_distinct_session_used",
               "cowork_distinct_session_used")


def _sessions_used(row: JsonObject) -> float:
    """How many distinct sessions reached for this thing on this day.

    The Office surfaces arrive nested one level deeper, one block per app, and
    are summed in rather than dropped — a connection used only from Excel is
    still a connection in use, and dropping it would rank it last.
    """
    total = 0.0
    for field in USED_FIELDS:
        value = row.get(field)
        if isinstance(value, (int, float)):
            total += float(value)
    office = row.get("office_metrics")
    if isinstance(office, dict):
        for app in office.values():
            if not isinstance(app, dict):
                continue
            for key, value in app.items():
                if key.startswith("distinct_session_") and isinstance(value, (int, float)):
                    total += float(value)
    return total


def compute(root: Path, window: JsonObject) -> JsonObject:
    start, end = window["week_start"], window["week_end"]
    prior_start, prior_end = window["comparison_start"], window["comparison_end"]

    # Which contributing tables actually reach the end of the window. The cost
    # table does; the activity tables lag it by a couple of days, so the current
    # week can be measured over five days and compared against four seven-day
    # baselines without anything saying so.
    freshness = W.window_coverage(root, VALUE_DATASETS, start, end)

    return {
        "stage": "x2_metrics_value",
        "report": REPORT,
        "generated_at": _now(),
        "window": window,
        "adoption": _adoption(root, start, end, prior_start, prior_end, freshness),
        "depth": _depth(root, start, end),
        "connectors": _connectors(root, start, end, prior_start, prior_end),
        "skills": _skills(root, start, end),
        "reliability": _reliability(),
        "trend": _weekly_trend(root, window, freshness=freshness),
        "coverage": _coverage(root, start, end),
        "freshness": freshness,
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


def _adoption(root: Path, start: str, end: str, prior_start: str, prior_end: str,
              freshness: JsonObject | None = None) -> JsonObject:
    now = _active_users(root, start, end)
    before = _active_users(root, prior_start, prior_end)

    # The seat denominator is the one the operator stands behind, not the raw
    # snapshot. Both live here and in the Waste Ledger's seat count; they must
    # agree, or the two reports disagree about the size of the company.
    seats, excluded = _seat_count(root)

    # The prior window is four weeks long, so compare against its weekly shape
    # rather than its total, which would make any single week look like collapse.
    prior_weekly = _weekly_active_average(root, prior_start, prior_end)

    # A week measured over five days against baselines measured over seven is
    # not a comparison, and the difference reads as a decline every time.
    short = _short_dataset(freshness, "analytics_users")
    comparable = bool(prior_weekly) and not short
    change = (W.safe_share(len(now) - prior_weekly, prior_weekly) if comparable else None)

    if comparable:
        movement = f"that is {change:+.0f}%."
    elif short:
        movement = (
            f"no comparison is drawn: this week's activity data stops at {short['last_day']}, "
            f"covering {short['days_with_data']} of its 7 days, and a short week set against "
            "full ones reads as a fall that did not happen."
        )
    else:
        movement = "there is no earlier week to compare against."

    return {
        "available": bool(now),
        "active_people": len(now),
        "active_people_described": W.describe_people(len(now)),
        "seats_total": seats,
        "seats_excluded": excluded,
        "seats_note": config.seat_exclusion_note(root, excluded),
        "share_of_seats_pct": W.safe_share(len(now), seats) if seats else None,
        "prior_weekly_active_average": prior_weekly,
        "change_vs_prior_pct": change,
        "week_is_short": bool(short),
        "short_note": (
            f"This week's per-person activity data stops at {short['last_day']}, "
            f"{short['days_with_data']} of the window's 7 days. Activity tables "
            "finalise a couple of days behind the cost table, so the newest week is "
            "counted over fewer days than the weeks it would be compared with."
        ) if short else "",
        "newly_active": len(set(now) - set(before)),
        "stopped": len(set(before) - set(now)),
        "reader_note": (
            f"{W.describe_people(len(now))} did something with Claude in this week"
            + (f", out of {seats:,} who hold a seat" if seats else "")
            + ". Compared with the four weeks before it, " + movement
        ),
    }


def _seat_count(root: Path) -> tuple[int, int]:
    """How many seats there are, after the operator's exclusions. (total, excluded)

    Read through the config layer rather than straight off the snapshot. Counting
    the snapshot directly overstated the seat total, the idle-seat count and the
    headline recoverable figure — the number that goes to Finance — by thirteen
    per cent every week, because service accounts nobody holds were being priced
    as seats somebody did.
    """
    if not W.dataset_exists(root, "directory_users"):
        return 0, 0
    from pipeline.lake import datalake as lake
    excluded = config.seat_exclusions(root)
    ids = [str(u.get("user_id") or "") for u in lake.iter_snapshot(root, "directory_users")]
    ids = [i for i in ids if i]
    kept = [i for i in ids if i not in excluded]
    return len(kept), len(ids) - len(kept)


def _short_dataset(freshness: JsonObject, dataset: str) -> JsonObject | None:
    """The coverage record for a dataset that does not reach the end of the window."""
    entry = (freshness or {}).get("datasets", {}).get(dataset)
    return entry if entry and entry.get("short") else None


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
    """What data Claude was actually reaching for, and what it stopped reaching for.

    Ranked by distinct sessions that used the connection, because that is what
    the source publishes. There is no call count and no error count in this
    surface; a rank built on one would be a rank built on zeros.
    """
    if not W.dataset_exists(root, "analytics_connectors"):
        return {"available": False, "why": "no analytics_connectors dataset"}

    blocked = schema.reader_skip_reason(root, "analytics_connectors", "what people reached for")
    if blocked:
        return {"available": False, "why": blocked}

    from pipeline.lake import datalake as lake
    aliases = config.connector_aliases(root)

    def display(name: str) -> str:
        """One connection, one name — a bare identifier the operator has mapped
        collapses onto the mapped name, so two identities for one connection are
        counted once rather than twice."""
        return aliases.get(name, name)

    everything = []
    for row in lake.iter_dataset(root, "analytics_connectors"):
        name = str(row.get("connector_name") or "")
        if not name:
            continue
        everything.append({**row, "connector_name": display(name)})

    born = det.first_seen(everything, "connector_name")

    def usage(rows: list[JsonObject]) -> tuple[dict[str, float], dict[str, float]]:
        """Sessions summed across days; people taken at the peak day, not summed.

        Summing a distinct-user count across seven days counts anyone active on
        more than one of them once per day. It read as growth and it was
        arithmetic.
        """
        sessions: dict[str, float] = defaultdict(float)
        peak_people: dict[str, float] = defaultdict(float)
        for row in rows:
            name = str(row["connector_name"])
            sessions[name] += _sessions_used(row)
            people = row.get("distinct_user_count")
            if isinstance(people, (int, float)):
                peak_people[name] = max(peak_people[name], float(people))
        return dict(sessions), dict(peak_people)

    now, peak = usage([r for r in everything if start <= str(r.get("day") or "") <= end])
    before, _ = usage([r for r in everything
                       if prior_start <= str(r.get("day") or "") <= prior_end])
    before_weekly = {k: v / 4.0 for k, v in before.items()}

    ranked = []
    for name, sessions in sorted(now.items(), key=lambda kv: (-kv[1], kv[0])):
        was = before_weekly.get(name, 0.0)
        # Only trend a connector that existed for the whole comparison window.
        comparable = bool(born.get(name)) and born[name] <= prior_start
        ranked.append({
            "name": name,
            "sessions": int(sessions),
            "people_described": W.describe_people(int(peak.get(name, 0))),
            "first_seen": born.get(name),
            "comparable": comparable,
            "change_pct": W.safe_share(sessions - was, was) if (comparable and was) else None,
            # Two different reasons a change is absent, kept apart. Collapsing
            # them meant a data fault rendered as a considered editorial choice.
            "no_change_reason": (None if (comparable and was)
                                 else ("new this period" if not comparable
                                       else "nothing to compare against")),
            "unnamed": det.is_unnamed_connector(name),
            "aliased": name in set(aliases.values()),
        })

    faded = [{"name": name,
              "was_weekly_sessions": int(was),
              "now_sessions": int(now.get(name, 0)),
              "first_seen": born.get(name)}
             for name, was in sorted(before_weekly.items(), key=lambda kv: -kv[1])
             if was >= 5 and now.get(name, 0) < was * 0.5
             and born.get(name, "9999") <= prior_start]

    unnamed = [r for r in ranked if r["unnamed"]]
    return {
        "available": True,
        "in_use": ranked,
        "count_in_use": len(ranked),
        "unnamed_count": len(unnamed),
        "unnamed_largest_sessions": max((r["sessions"] for r in unnamed), default=0),
        "aliased_count": sum(1 for r in ranked if r["aliased"]),
        "faded": faded,
        "unit_note": (
            "Usage is counted in distinct sessions and conversations that reached "
            "for a connection — the measure this data source publishes. It does "
            "not publish how many calls were made, so none is shown."
        ),
        "birth_note": (
            "Each connection carries the date it first appeared. Anything younger than "
            "the comparison window is marked not comparable and is never shown as growth "
            "— a connection that did not exist last month always looks like infinite growth."
        ),
        "unnamed_note": (
            f"{len(unnamed)} connection(s) report under a bare identifier with no name "
            "and no owner, the largest of them reaching "
            f"{max((r['sessions'] for r in unnamed), default=0):,} session(s) this week. "
            "Nothing in this data can say what they are: the identifier is all the "
            "source publishes. Name them once in the connection alias file and every "
            "future report will use the name."
        ) if unnamed else "",
        "alias_note": (
            f"{sum(1 for r in ranked if r['aliased'])} connection(s) are shown under a "
            "name the operator supplied for a bare identifier. Where a connection "
            "reported under both, the two are counted as one."
        ) if aliases else "",
        "faded_note": (
            "A connection whose use halved or worse against its own earlier weekly average. "
            "This is the most expensive failure mode available: nothing errors, nothing "
            "alerts, people simply go back to doing it by hand."
        ) if faded else "Nothing in use dropped by half or more against its own average.",
    }


def _skills(root: Path, start: str, end: str) -> JsonObject:
    """Which capabilities got used, ranked by the sessions that used them.

    This read `invocation_count` for a year. No such field exists outside this
    project's own demo fixture, so every capability scored zero, the ranking was
    whatever order the dictionary happened to be built in, and the report said
    "across 0 uses" against a hundred and sixty-seven real sessions — while
    presenting the resulting arbitrary order to the reader as a ranking.
    """
    if not W.dataset_exists(root, "analytics_skills"):
        return {"available": False, "why": "no analytics_skills dataset"}

    blocked = schema.reader_skip_reason(root, "analytics_skills", "which capabilities got used")
    if blocked:
        return {"available": False, "why": blocked}

    rows = list(W.rows_in_window(root, "analytics_skills", start, end))
    by_skill: dict[str, dict[str, float]] = defaultdict(lambda: {"sessions": 0.0, "people": 0.0})
    for row in rows:
        name = str(row.get("skill_name") or "")
        if not name:
            continue
        by_skill[name]["sessions"] += _sessions_used(row)
        people = row.get("distinct_user_count")
        if isinstance(people, (int, float)):
            # Peak day, not a sum — the same double-count as connectors.
            by_skill[name]["people"] = max(by_skill[name]["people"], float(people))

    ranked = [{"name": name,
               "sessions": int(v["sessions"]),
               "people_described": W.describe_people(int(v["people"]))}
              for name, v in sorted(by_skill.items(),
                                    key=lambda kv: (-kv[1]["sessions"], kv[0]))]

    total = int(sum(v["sessions"] for v in by_skill.values()))
    return {
        "available": bool(ranked) and total > 0,
        "why": ("no capability recorded a single session in this window"
                if not (ranked and total > 0) else ""),
        "in_use": ranked[:12],
        "count_in_use": len(ranked),
        "sessions_total": total,
        "unit_note": (
            "Ranked by the number of distinct sessions in which each capability was "
            "used. This source does not publish a count of invocations, so none is shown."
        ),
    }


def _reliability() -> JsonObject:
    """Whether connections fail. They do; this data cannot say how often.

    Kept as an explicit, permanent "not measurable" rather than deleted, because
    a reader who remembers seeing a failure rate is owed the reason it is gone,
    and because the next person to go looking for one should find this note
    before they find a plausible-looking field name.
    """
    return {
        "available": False,
        "why": "connection reliability is not published by this data source",
        "reader_note": (
            "Whether a connection failed cannot be answered from this data. The "
            "usage tables publish how many sessions reached for a connection and "
            "nothing about how those attempts went — no failure count, no call "
            "count. No reliability figure appears in this report because none "
            "exists in the source, which is not the same as everything having "
            "worked."
        ),
    }


def _weekly_trend(root: Path, window: JsonObject, span: int = 10,
                  freshness: JsonObject | None = None) -> JsonObject:
    short = _short_dataset(freshness or {}, "analytics_users")
    points = []
    for start, end in W.weeks_back(window["week_start"], span):
        active = _active_users(root, start, end)
        current = start == window["week_start"]
        points.append({
            "label": _short_label(start),
            "week_start": start,
            "value": len(active),
            "current": current,
            # A point drawn from fewer than seven days is marked as such rather
            # than plotted beside full weeks as though it were one of them.
            "partial": bool(current and short),
        })
    return {
        "title": "People using Claude, by week",
        "unit": "people",
        "weeks": points,
        "caption": (
            "Each point counts people with any recorded activity in one "
            + ("Monday-to-Sunday week, except the marked point: this week's activity "
               f"data stops at {short['last_day']}, so it covers "
               f"{short['days_with_data']} days against the others' 7 and sits lower "
               "than a full week would. "
               if short else
               "complete Monday-to-Sunday week. The marked point is the week this "
               "report covers. ")
            + "Activity is only visible for products that report it, so this is a floor."
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
