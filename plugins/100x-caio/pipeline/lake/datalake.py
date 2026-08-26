#!/usr/bin/env python3
"""Tiny stdlib-only partitioned data lake for Claude analytics.

Layout (Hive-style ISO-week partitions, one JSONL file per week):

    data/
      <dataset>/week=YYYY-Www/part.jsonl
      _state.json          # per-dataset watermark + last run metadata

Design goals:
  - Incremental: each ISO week is an immutable partition. Re-writing a week
    overwrites it wholesale, so re-running a fetch never duplicates rows — but
    callers must hand over complete weeks (fetchers snap their start back to the
    week's Monday). A per-dataset watermark (a day) records progress.
  - Analytics-friendly: each partition is newline-delimited JSON, readable with
    pandas (`pd.read_json(path, lines=True)`), DuckDB
    (`read_json_auto('data/<dataset>/*/*.jsonl')`), or any line reader. Every row
    also carries its own `day` field, so partitioning is convenience, not
    load-bearing for analysis.

This module is import-only (no side effects) and depends only on the standard
library.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

DEFAULT_DATA_DIR = "data"
STATE_FILE = "_state.json"
PARTITION_FILE = "part.jsonl"
SNAPSHOT_FILE = "snapshot.jsonl"

JsonObject = dict[str, Any]


# --- day helpers -------------------------------------------------------------

def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def day_str(value: date) -> str:
    return value.isoformat()


def day_range(start: date, end: date) -> list[str]:
    """Inclusive list of YYYY-MM-DD strings from start to end."""
    if end < start:
        return []
    days = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


# --- week (ISO) helpers ------------------------------------------------------

def week_start(day: date) -> date:
    """Monday of the ISO week containing `day`."""
    return day - timedelta(days=day.isoweekday() - 1)


def week_key(value) -> str:
    """ISO-week partition key, e.g. '2026-W05'. Accepts a date or YYYY-MM-DD[...]."""
    day = value if isinstance(value, date) else parse_day(str(value)[:10])
    iso = day.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


# --- paths -------------------------------------------------------------------

def dataset_dir(root: Path, dataset: str) -> Path:
    return root / dataset


def partition_file(root: Path, dataset: str, week: str) -> Path:
    return dataset_dir(root, dataset) / f"week={week}" / PARTITION_FILE


def list_partitions(root: Path, dataset: str) -> list[str]:
    base = dataset_dir(root, dataset)
    if not base.is_dir():
        return []
    weeks = []
    for child in base.iterdir():
        if child.is_dir() and child.name.startswith("week="):
            weeks.append(child.name[len("week="):])
    return sorted(weeks)


# --- partition I/O -----------------------------------------------------------

def write_partition(root: Path, dataset: str, week: str, rows: Iterable[JsonObject]) -> int:
    """Overwrite the partition for `week` with `rows`. Returns row count."""
    path = partition_file(root, dataset, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
            count += 1
    return count


def iter_partition(root: Path, dataset: str, week: str) -> Iterator[JsonObject]:
    path = partition_file(root, dataset, week)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def iter_dataset(root: Path, dataset: str) -> Iterator[JsonObject]:
    """Iterate every row across all partitions of a dataset (week order)."""
    for week in list_partitions(root, dataset):
        yield from iter_partition(root, dataset, week)


def write_rows(root: Path, dataset: str, rows: Iterable[JsonObject], day_key: str = "day") -> dict[str, int]:
    """Group rows into ISO-week partitions (by each row's `day_key`) and overwrite.

    Callers must hand over COMPLETE weeks: a partition is overwritten wholesale,
    so re-fetching only part of a week would drop the rest. Fetchers snap their
    start back to the Monday of the week (see week_start) to guarantee this.
    Only weeks that actually have rows get a file, so backfills stay compact.
    """
    grouped: dict[str, list[JsonObject]] = {}
    for row in rows:
        grouped.setdefault(week_key(row.get(day_key)), []).append(row)

    written: dict[str, int] = {}
    for week, week_rows in sorted(grouped.items()):
        written[week] = write_partition(root, dataset, week, week_rows)
    return written


def upsert_rows(root: Path, dataset: str, rows: Iterable[JsonObject], key: str, day_key: str = "day") -> dict[str, int]:
    """Merge rows into ISO-week partitions by `key`, preserving existing rows.

    Unlike write_rows (which overwrites a week wholesale), this reads each
    affected week, merges incoming rows over existing ones by `key` (incoming
    wins), and writes the week back. Use for incremental sources that fetch a
    scattered subset (e.g. compliance content pulled by updated_at), where a
    wholesale overwrite would drop rows the current run didn't refetch.
    """
    incoming_by_week: dict[str, dict[Any, JsonObject]] = {}
    for row in rows:
        bucket = incoming_by_week.setdefault(week_key(row.get(day_key)), {})
        bucket[row.get(key)] = row

    written: dict[str, int] = {}
    for week, incoming in sorted(incoming_by_week.items()):
        merged: dict[Any, JsonObject] = {}
        for existing in iter_partition(root, dataset, week):
            merged[existing.get(key)] = existing
        merged.update(incoming)  # incoming wins
        written[week] = write_partition(root, dataset, week, list(merged.values()))
    return written


# --- snapshot datasets (dimension / reference data) --------------------------

def snapshot_file(root: Path, dataset: str) -> Path:
    return dataset_dir(root, dataset) / SNAPSHOT_FILE


def write_snapshot(root: Path, dataset: str, rows: Iterable[JsonObject]) -> int:
    """Overwrite a non-time-series (dimension) dataset as one snapshot.jsonl.

    Use for reference data that has no meaningful time axis (org roster, groups,
    role catalog): there's nothing to partition or accumulate, each run replaces
    the whole table. Lives under data/<dataset>/snapshot.jsonl so dimension and
    time-series datasets share the same one-folder-per-dataset layout. Read it
    back with pandas (`pd.read_json(path, lines=True)`) like any other dataset.
    """
    path = snapshot_file(root, dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
            count += 1
    return count


def iter_snapshot(root: Path, dataset: str) -> Iterator[JsonObject]:
    path = snapshot_file(root, dataset)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


# --- state / watermarks ------------------------------------------------------

def state_path(root: Path) -> Path:
    return root / STATE_FILE


def read_state(root: Path) -> JsonObject:
    path = state_path(root)
    if not path.exists():
        return {"datasets": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"datasets": {}}
    state.setdefault("datasets", {})
    return state


def write_state(root: Path, state: JsonObject) -> None:
    root.mkdir(parents=True, exist_ok=True)
    state_path(root).write_text(json.dumps(state, indent=2, default=str) + "\n", encoding="utf-8")


def get_watermark(state: JsonObject, dataset: str) -> str | None:
    return state.get("datasets", {}).get(dataset, {}).get("watermark_day")


def set_dataset_state(state: JsonObject, dataset: str, **fields: Any) -> None:
    state.setdefault("datasets", {}).setdefault(dataset, {}).update(fields)


def advance_watermark(state: JsonObject, dataset: str, candidate: str) -> str:
    """Move the watermark forward to `candidate` if it's newer; return the result."""
    current = get_watermark(state, dataset)
    new_value = candidate if current is None or parse_day(candidate) > parse_day(current) else current
    set_dataset_state(state, dataset, watermark_day=new_value)
    return new_value


def incremental_start(
    state: JsonObject,
    dataset: str,
    trailing_days: int,
    today: date | None = None,
    earliest: str | None = None,
    lookback_days: int | None = None,
) -> date:
    """Compute the first day to fetch for an incremental run.

    With a watermark, re-fetch a trailing window ending at the watermark so
    today's partial bucket and any late-settling numbers get refreshed. Without
    one (first run), backfill everything from `earliest` — unless `lookback_days`
    is given, which bounds the first run to that many days before today.
    """
    today = today or today_utc()
    watermark = get_watermark(state, dataset)
    if watermark:
        return parse_day(watermark) - timedelta(days=max(trailing_days - 1, 0))
    if lookback_days is not None:
        return today - timedelta(days=lookback_days)
    if earliest is not None:
        return parse_day(earliest)
    return today - timedelta(days=30)
