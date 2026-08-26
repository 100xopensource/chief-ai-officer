"""Shared reading helpers for the x2 metric stages.

Deliberately standard-library only, and deliberately small. Every metric stage
needs the same three things: rows inside a date window, rows inside the window
before it, and a way to describe a group of people without ever describing a
group so small that describing it names them.

Why not pandas
--------------
The metric stages run in CI, in a plugin skill, and on a laptop that has just
cloned the repository. `iter_dataset` streams JSON lines and costs nothing to
import. pandas stays where the analysis read side wants it — `lake.lake_read` —
rather than becoming a hard dependency of the thing that produces the numbers.

The small-group floor
---------------------
`describe_people` exists because a count is an identifier when the population
is small. "3 people" in a 112-seat company, filtered by a team and a week, is a
name with extra steps. Every person-count that reaches a reader goes through
this function, which refuses to render one to four as a digit.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from pipeline.lake import datalake as lake

JsonObject = dict[str, Any]

# Below this, a person-count is written as words, never as a digit.
SMALL_GROUP_FLOOR = 5


# --------------------------------------------------------------------------
# windows
# --------------------------------------------------------------------------

def in_window(row: JsonObject, start: str, end: str, day_key: str = "day") -> bool:
    day = str(row.get(day_key) or "")
    return bool(day) and start <= day <= end


def rows_in_window(root: Path, dataset: str, start: str, end: str,
                   day_key: str = "day") -> Iterator[JsonObject]:
    """Stream one dataset's rows for one inclusive date range."""
    for row in lake.iter_dataset(root, dataset):
        if in_window(row, start, end, day_key):
            yield row


def dataset_exists(root: Path, dataset: str) -> bool:
    directory = root / dataset
    if not directory.is_dir():
        return False
    return any(directory.glob("week=*/part.jsonl")) or (directory / "snapshot.jsonl").exists()


def latest_day(root: Path, datasets: Iterable[str] = ()) -> str | None:
    """The most recent day any dataset in this lake has data for.

    The run window should be resolved from the data, not from the wall clock.
    A lake pulled on Friday and analysed on Monday still describes Friday, and
    a report that silently reports an empty week because the calendar moved on
    is worse than one that reports the last week it can actually see.
    """
    candidates = list(datasets) or ["analytics_cost", "fact_block", "dim_chat",
                                    "analytics_users", "analytics_usage"]
    best: str | None = None
    for dataset in candidates:
        if not dataset_exists(root, dataset):
            continue
        partitions = sorted(p.name.replace("week=", "")
                            for p in (root / dataset).glob("week=*"))
        if not partitions:
            continue
        for row in lake.iter_partition(root, dataset, partitions[-1]):
            day = str(row.get("day") or "")
            if day and (best is None or day > best):
                best = day
    return best


def resolve_as_of(root: Path, explicit: str | None = None) -> date:
    """The date the run should behave as if it were.

    An explicit --as-of always wins. Otherwise the run date comes from the lake:
    the day after the newest day of data, so the newest complete week of data is
    the week the report covers. Falling back to the wall clock would make a
    report about a lake pulled last month silently report an empty week.
    """
    if explicit:
        return date.fromisoformat(explicit)
    newest = latest_day(root)
    if newest:
        return date.fromisoformat(newest) + timedelta(days=1)
    return datetime.now(timezone.utc).date()


def weeks_back(week_start: str, count: int) -> list[tuple[str, str]]:
    """The `count` complete weeks ending with the one starting at `week_start`.

    Returned oldest first, as (start, end) pairs, so a trend line reads left to
    right the way a reader expects.
    """
    anchor = date.fromisoformat(week_start)
    out = []
    for step in range(count - 1, -1, -1):
        start = anchor - timedelta(days=7 * step)
        out.append((start.isoformat(), (start + timedelta(days=6)).isoformat()))
    return out


# --------------------------------------------------------------------------
# safe description
# --------------------------------------------------------------------------

def describe_people(count: int) -> str:
    """Render a person-count in a form that cannot identify anyone.

    Under five is always words. This has been the rule since a report named a
    three-person group by describing it precisely enough that everyone in the
    company could name all three.
    """
    if count <= 0:
        return "nobody"
    if count < SMALL_GROUP_FLOOR:
        return "fewer than five people"
    return f"{count:,} people"


def describe_count(count: int, singular: str, plural: str | None = None) -> str:
    """Non-person counts. No floor applies — a count of conversations is not a person."""
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count:,} {word}"


def safe_share(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------

def total(rows: Iterable[JsonObject], field: str) -> float:
    out = 0.0
    for row in rows:
        value = row.get(field)
        if isinstance(value, (int, float)):
            out += float(value)
    return out


def group_sum(rows: Iterable[JsonObject], key: str, field: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        value = row.get(field)
        if not isinstance(value, (int, float)):
            continue
        out[str(row.get(key) or "unattributed")] = out.get(str(row.get(key) or "unattributed"), 0.0) + float(value)
    return out


def distinct(rows: Iterable[JsonObject], key: str) -> set[str]:
    return {str(row[key]) for row in rows if row.get(key)}


def top_n(mapping: dict[str, float], count: int = 5) -> list[tuple[str, float]]:
    return sorted(mapping.items(), key=lambda kv: -kv[1])[:count]


def round_money(value: float) -> float:
    return round(value, 2)


def money(value: float) -> str:
    """Dollars a reader can read. No cents above ten dollars — nobody acts on cents."""
    if abs(value) >= 10:
        return f"${value:,.0f}"
    return f"${value:,.2f}"
