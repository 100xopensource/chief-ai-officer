#!/usr/bin/env python3
"""What each metric stage needs to find in the lake, and whether it is there.

Why this file exists
--------------------
A metric that reads a field the data does not have does not crash. It sums
nothing, reports zero, and every other check in this project passes it: the
render is well-formed, the privacy gates find nothing to object to, and the
reconciliation block ties out perfectly, because zero equals zero.

That is not a hypothetical. A weekly run published a stat card reading "0% of
connector calls failed" against a data source that publishes no failure data at
all, and a "most-used capabilities" list sorted by a key that was zero for every
row — so the order was whatever the dictionary happened to be built in,
presented to the reader as a ranking. Both reports passed every gate.

The root cause was one mistake made twice: the metric code was written against
the synthetic demo lake, whose shape did not match what the live API returns,
and nothing ever compared the two. This module is the comparison. It declares
the fields each stage consumes, checks them against rows actually in the lake,
and makes a missing field a stated skip instead of a silent zero.

The rule it enforces
--------------------
A field that is absent, or present but null or zero in every row, is not a
measurement of zero. It is an absence of measurement, and those are different
things that a reader is entitled to have told apart.

Usage
-----
    python3 -m pipeline.lake.schema --data-dir data
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from pipeline.lake import datalake as lake

JsonObject = dict[str, Any]

# How many rows to look at before deciding a field is absent. A field present on
# any row in the sample counts as present — the API omits a product's block on a
# day it reported nothing, so absence from one row proves nothing.
SAMPLE_ROWS = 2000


class FieldContract:
    """The fields one stage reads out of one dataset.

    `required` is what the stage cannot compute without: if any are missing, the
    stage must skip and say so. `at_least_one` is a group where the stage needs
    some of them but not all — the three per-surface usage counts, for instance,
    where a company that never uses Claude Code legitimately has that column
    null throughout.
    """

    def __init__(self, dataset: str, consumer: str, required: Iterable[str] = (),
                 at_least_one: Iterable[str] = (), note: str = "",
                 reader_why: str = "") -> None:
        self.dataset = dataset
        self.consumer = consumer
        self.required = tuple(required)
        self.at_least_one = tuple(at_least_one)
        self.note = note
        # Two audiences, two sentences. The operator needs the field names, to
        # go and look. The reader of the report needs a plain sentence, because
        # a column of field names in an executive document is the same thing as
        # a blank space — and a blank space is how the last one went wrong.
        self.reader_why = reader_why


# Every field any metric stage reads, by the stage that reads it. Adding a read
# without adding it here is how the last failure happened, so the end-to-end
# tests assert this list covers what the stages actually touch.
CONTRACTS = [
    FieldContract(
        "analytics_users", "adoption and depth",
        required=["user_id", "day"],
        at_least_one=["chat_message_count", "cc_distinct_session_count",
                      "cowork_message_count", "cowork_action_count", "web_search_count"],
        note="without an activity column nobody can be called active",
        reader_why="the per-person activity table in this data does not carry the columns "
                   "that say what anyone did, so nobody can be counted as active",
    ),
    FieldContract(
        "analytics_cost", "spend",
        required=["day", "amount_usd"],
    ),
    FieldContract(
        "analytics_user_cost", "idle seats",
        required=["day", "user_id", "amount_usd"],
    ),
    FieldContract(
        "directory_users", "the seat denominator",
        required=["user_id"],
        note="created_at is what separates an idle seat from a new one",
    ),
    FieldContract(
        "analytics_connectors", "what people reached for",
        required=["day", "connector_name", "distinct_user_count"],
        at_least_one=["chat_distinct_conversation_used", "cc_distinct_session_used",
                      "cowork_distinct_session_used", "office_metrics"],
        note="this source publishes no failure or call-volume data; usage is "
             "measured in distinct sessions that reached for the connection",
        reader_why="the connections table in this data does not record how much each "
                   "connection was used, so nothing can be said about which ones "
                   "people reached for",
    ),
    FieldContract(
        "analytics_skills", "which capabilities got used",
        required=["day", "skill_name", "distinct_user_count"],
        at_least_one=["chat_distinct_conversation_used", "cc_distinct_session_used",
                      "cowork_distinct_session_used", "office_metrics"],
        reader_why="the capabilities table in this data does not record how much each one "
                   "was used, so no ranking of them can be built",
    ),
]

BY_DATASET: dict[str, list[FieldContract]] = {}
for _contract in CONTRACTS:
    BY_DATASET.setdefault(_contract.dataset, []).append(_contract)


# --------------------------------------------------------------------------

def fields_present(root: Path, dataset: str, sample: int = SAMPLE_ROWS) -> set[str]:
    """Every key that appears on at least one row of the dataset.

    A key whose value is null on every row it appears on does not count as
    present. The API returns null for a product that reported nothing, and a
    column that is null all the way down cannot be summed into anything a reader
    should see.
    """
    seen: set[str] = set()
    rows = _sample_rows(root, dataset, sample)
    for row in rows:
        for key, value in row.items():
            if value is None:
                continue
            seen.add(key)
    return seen


def _sample_rows(root: Path, dataset: str, sample: int) -> list[JsonObject]:
    """Rows from the newest partitions first — schema drift shows up there.

    The oldest weeks of a lake were written by whatever the API returned months
    ago. What matters is what it returns now, so the newest partition is read
    first and the sample is filled backwards from there.
    """
    snapshot = lake.snapshot_file(root, dataset)
    if snapshot.exists():
        return list(lake.iter_snapshot(root, dataset))[:sample]

    out: list[JsonObject] = []
    for week in reversed(lake.list_partitions(root, dataset)):
        for row in lake.iter_partition(root, dataset, week):
            out.append(row)
            if len(out) >= sample:
                return out
    return out


def check_dataset(root: Path, dataset: str) -> list[JsonObject]:
    """One result per contract on this dataset. An empty list means no contract."""
    contracts = BY_DATASET.get(dataset, [])
    if not contracts:
        return []

    present = fields_present(root, dataset)
    if not present:
        return [{
            "dataset": dataset,
            "consumer": contract.consumer,
            "ok": False,
            "missing": list(contract.required),
            "why": f"{dataset} has no rows to read a shape from",
            "reader_why": contract.reader_why or f"{dataset} is empty",
            "note": contract.note,
        } for contract in contracts]

    out = []
    for contract in contracts:
        missing = [f for f in contract.required if f not in present]
        if contract.at_least_one and not any(f in present for f in contract.at_least_one):
            missing.append(" or ".join(contract.at_least_one))
        out.append({
            "dataset": dataset,
            "consumer": contract.consumer,
            "ok": not missing,
            "missing": missing,
            "why": (f"{dataset} carries no {', '.join(missing)} — "
                    f"{contract.consumer} cannot be measured from it"
                    if missing else ""),
            "reader_why": (contract.reader_why if missing else ""),
            "note": contract.note,
        })
    return out


def check(root: Path) -> list[JsonObject]:
    """Every contract whose dataset is present in this lake."""
    out = []
    for dataset in sorted(BY_DATASET):
        if not lake.dataset_dir(root, dataset).is_dir():
            continue
        out.extend(check_dataset(root, dataset))
    return out


def missing_for(root: Path, dataset: str, consumer: str | None = None) -> list[str]:
    """The fields a stage is about to read and will not find.

    Call this before computing, and return `{"available": False, "why": ...}`
    rather than a zero, when it comes back non-empty. A zero and an absence look
    identical downstream and mean opposite things.
    """
    for result in check_dataset(root, dataset):
        if consumer and result["consumer"] != consumer:
            continue
        if not result["ok"]:
            return result["missing"]
    return []


def skip_reason(root: Path, dataset: str, consumer: str) -> str | None:
    """The operator's sentence — field names included — or None if the shape is fine."""
    for result in check_dataset(root, dataset):
        if result["consumer"] == consumer and not result["ok"]:
            return result["why"]
    return None


def reader_skip_reason(root: Path, dataset: str, consumer: str) -> str | None:
    """The same absence in words a reader of the report can use.

    A report that says a thing could not be measured has to say it in the
    register of the rest of the report. Printing four underscored field names
    into an executive document is, for its reader, indistinguishable from
    printing nothing — and printing nothing is what let a missing measurement
    pass as a measurement of zero.
    """
    for result in check_dataset(root, dataset):
        if result["consumer"] == consumer and not result["ok"]:
            return result.get("reader_why") or result["why"]
    return None


def render_human(results: list[JsonObject]) -> str:
    if not results:
        return "No dataset in this lake has a declared shape to check.\n"

    lines = ["What each report expects to find, and what is actually there", ""]
    for result in results:
        mark = "ok  " if result["ok"] else "GONE"
        lines.append(f"  {mark}  {result['dataset']} — {result['consumer']}")
        if not result["ok"]:
            lines.append(f"        missing: {', '.join(result['missing'])}")
    broken = [r for r in results if not r["ok"]]
    lines.append("")
    if broken:
        lines.append(
            f"{len(broken)} measurement(s) cannot be computed from this lake's shape. "
            "They will be reported as not measurable rather than as zero — a zero "
            "here would read as good news."
        )
    else:
        lines.append("Every declared field is present. This says the shape is right, "
                     "not that the numbers are.")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Check the lake's shape against what the metric stages read.")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = check(Path(args.data_dir))
    print(json.dumps(results, indent=2) if args.json else render_human(results), end="")
    return 1 if any(not r["ok"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
