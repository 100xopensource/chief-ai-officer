#!/usr/bin/env python3
"""Pandas reader for the partitioned analytics lake — one import for every skill.

The lake is *written* by datalake.py (stdlib-only, no pandas). This module is the
matching *read* side: it turns the on-disk JSONL/CSV layout into typed pandas
DataFrames, so analysis skills stop re-implementing glob+concat and stop getting
inconsistent dtypes across partitions.

Layout it reads (see references/data_contract.md):

    data/
      <dataset>/week=YYYY-Www/part.jsonl   -> read_dataset(name)   (time-series)
      <dataset>/snapshot.jsonl             -> read_snapshot(name)  (dimensions)
      _summary/<name>.csv                  -> read_summary(name)   (pre-aggregates)

Why this exists (the two dtype traps a plain `pd.read_json(lines=True)` hits):
  1. Money columns (`amount_cents`, `list_amount_cents`) are stored as STRINGS
     ("0.000000"). Naive reads leave them as text, so sums silently concatenate.
  2. Dimension columns that are all-null in one week (`product`, `model`,
     `cost_type`, `token_type`, `context_window`, `inference_geo`, `speed`) infer
     as float64 there but as object in a week that has values — so `concat` across
     weeks produces a mixed/So column. We pin those to pandas nullable "string".

Coercion is by COLUMN-NAME CONVENTION, not a frozen per-dataset schema, so new
columns added upstream keep working without editing this file:
  - `day`                       -> datetime64 (naive; it's a bucket, not an instant)
  - any column ending in `_at`  -> datetime64[UTC] (starting_at, created_at, ...)
  - MONEY_COLS                  -> numeric float
  - STRING_DIMS                 -> nullable "string" (kills the null-flip)
Everything else is left to pandas inference (ints/bools resolve fine once a
non-null value is present). Nested dict columns (e.g. chat_metrics) pass through
untouched as object.

Depends only on pandas. Import from any skill or stage:

    from pipeline.lake import lake_read
    cost = lake_read.read_dataset("analytics_cost", data_dir)
"""

from __future__ import annotations

import glob
import os
import re
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:  # keep import-time cost to just pandas
    from pandas import DataFrame

DEFAULT_DATA_DIR = "data"

# Money fields the fetchers store as fixed-point STRINGS — coerce to float.
MONEY_COLS = frozenset({
    "amount_cents", "amount_usd", "list_amount_cents", "list_amount_usd",
})

# Dimension columns that are frequently all-null in a given week and otherwise
# categorical text. Pinning them to nullable string stops the float64<->object
# dtype flip that breaks concat / groupby across partitions.
STRING_DIMS = frozenset({
    "product", "model", "cost_type", "token_type", "context_window",
    "inference_geo", "speed", "currency", "model_family",
})

_WEEK_RE = re.compile(r"week=([^/\\]+)")


# --- coercion ---------------------------------------------------------------

def coerce_types(df: "DataFrame") -> "DataFrame":
    """Apply the column-name conventions above. Safe on missing columns."""
    if df.empty:
        return df
    for col in df.columns:
        if col == "day":
            df[col] = pd.to_datetime(df[col], errors="coerce")           # bucket -> naive
        elif col.endswith("_at"):
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")  # instant -> UTC
        elif col in MONEY_COLS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        elif col in STRING_DIMS:
            df[col] = df[col].astype("string")
    return df


# --- time-series datasets (week partitions) ---------------------------------

def read_dataset(name: str, data_dir: str = DEFAULT_DATA_DIR, coerce: bool = True,
                 add_week: bool = True) -> "DataFrame":
    """Read every week partition of a time-series dataset into one DataFrame.

    Returns an empty DataFrame if the dataset has no partitions. Columns are
    unioned across weeks (missing -> NaN); when `add_week` is set, the partition
    key is exposed as a `week` column (convenient for period rollups; each row
    also still carries its own `day`).
    """
    files = sorted(glob.glob(os.path.join(data_dir, name, "week=*", "part.jsonl")))
    if not files:
        return pd.DataFrame()
    frames = []
    for path in files:
        frame = pd.read_json(path, lines=True)
        if add_week:
            match = _WEEK_RE.search(path.replace(os.sep, "/"))
            frame["week"] = match.group(1) if match else None
        frames.append(frame)
    df = pd.concat(frames, ignore_index=True)
    return coerce_types(df) if coerce else df


# --- snapshot datasets (dimensions) -----------------------------------------

def read_snapshot(name: str, data_dir: str = DEFAULT_DATA_DIR, coerce: bool = True) -> "DataFrame":
    """Read a single-file dimension dataset (data/<name>/snapshot.jsonl)."""
    path = os.path.join(data_dir, name, "snapshot.jsonl")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_json(path, lines=True)
    return coerce_types(df) if coerce else df


# --- summary aggregates (CSV) -----------------------------------------------

def read_summary(name: str, data_dir: str = DEFAULT_DATA_DIR) -> "DataFrame":
    """Read a pre-computed aggregate CSV from data/_summary/ (with or without .csv)."""
    stem = name[:-4] if name.endswith(".csv") else name
    path = os.path.join(data_dir, "_summary", f"{stem}.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


# --- discovery --------------------------------------------------------------

def available_datasets(data_dir: str = DEFAULT_DATA_DIR) -> dict[str, str]:
    """Map dataset name -> kind ('partitioned' | 'snapshot') for what's on disk."""
    out: dict[str, str] = {}
    if not os.path.isdir(data_dir):
        return out
    for entry in sorted(os.listdir(data_dir)):
        base = os.path.join(data_dir, entry)
        if not os.path.isdir(base) or entry.startswith("_"):
            continue
        if glob.glob(os.path.join(base, "week=*", "part.jsonl")):
            out[entry] = "partitioned"
        elif os.path.exists(os.path.join(base, "snapshot.jsonl")):
            out[entry] = "snapshot"
    return out


def read_any(name: str, data_dir: str = DEFAULT_DATA_DIR, coerce: bool = True) -> "DataFrame":
    """Read a dataset whether it's partitioned or a snapshot (auto-detect)."""
    kind = available_datasets(data_dir).get(name)
    if kind == "snapshot":
        return read_snapshot(name, data_dir, coerce=coerce)
    return read_dataset(name, data_dir, coerce=coerce)


# --- CLI preview ------------------------------------------------------------

def _main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Preview a lake dataset as pandas.")
    parser.add_argument("dataset", nargs="?", help="dataset name; omit to list what's available")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--rows", type=int, default=5, help="head rows to print")
    args = parser.parse_args()

    if not args.dataset:
        found = available_datasets(args.data_dir)
        print(f"datasets under {args.data_dir!r}:")
        for name, kind in found.items():
            print(f"  {name:28} {kind}")
        summaries = sorted(glob.glob(os.path.join(args.data_dir, "_summary", "*.csv")))
        print(f"summaries: {len(summaries)} CSV(s) under _summary/")
        return

    df = read_any(args.dataset, args.data_dir)
    if df.empty:
        print(f"{args.dataset}: no data found under {args.data_dir!r}")
        return
    print(f"{args.dataset}: {df.shape[0]} rows x {df.shape[1]} cols")
    print("\ndtypes:")
    print(df.dtypes.to_string())
    print(f"\nhead({args.rows}):")
    print(df.head(args.rows).to_string())


if __name__ == "__main__":
    _main()
