#!/usr/bin/env python3
"""Standing decisions the operator has made, that every stage must honour.

The problem this solves
-----------------------
Some facts about an organisation are not in the data and never will be. Which
accounts are service accounts. Which bare identifier is which connection. Which
of two names are the same team. Only the person running the reports knows, and
once they have worked it out they should have to record it exactly once.

A deployment kept a list of accounts confirmed to hold no active seat. The
pipeline never read it, because nothing read it — each stage built its own seat
count straight from the directory snapshot. The result was every seat figure and
the headline recoverable figure overstated by thirteen per cent, every week,
in the document that goes to Finance.

So this is a layer rather than a file: one place stages ask for the operator's
decisions, so a new decision is honoured everywhere the moment it is written,
and a stage that forgets to ask is a visible omission rather than an invisible
one.

Where it lives
--------------
    <data-dir>/_reports/config/seat_exclusions.json
    <data-dir>/_reports/config/connector_aliases.json

Next to the data it qualifies, not next to the code, because the decisions are
about this organisation and copying the tool elsewhere must not carry them.

Every file is optional. A lake with no config behaves exactly as before, and
`caio check` prints which files it found and how many entries each carries, so
a decision that is being honoured is visible and one that is not is too.

Usage
-----
    python3 -m pipeline.config --data-dir data
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json
import sys
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]

CONFIG_DIR = ("_reports", "config")

KNOWN = {
    "seat_exclusions.json": (
        "Accounts confirmed to hold no active seat — service accounts, closed "
        "accounts, shared mailboxes. Removed from every seat count and from the "
        "recoverable figure, which would otherwise price a seat nobody holds."
    ),
    "connector_aliases.json": (
        "Bare identifiers mapped to the name a reader would recognise. Two "
        "identities for one connection are merged, so a count of connections in "
        "use counts it once."
    ),
}


def config_dir(root: Path) -> Path:
    return Path(root).joinpath(*CONFIG_DIR)


def load(root: Path, name: str) -> Any:
    """Read one config file. Missing is fine; malformed is not.

    A config file that will not parse raises rather than falling back to empty.
    Silently ignoring an operator's decision because of a stray comma is the
    same failure as never reading it, and harder to notice.
    """
    path = config_dir(root) / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(
            f"{path} is not valid JSON ({exc}). It records a decision the reports "
            "depend on, so the run stops rather than quietly ignoring it."
        ) from None


# --------------------------------------------------------------------------
# seat exclusions
# --------------------------------------------------------------------------

def seat_exclusions(root: Path) -> set[str]:
    """Account ids the operator has confirmed hold no active seat.

    Accepts either shape, because both are things a person reasonably writes:

        ["user_abc", "user_def"]

        {"excluded": [{"user_id": "user_abc",
                       "reason": "service account",
                       "decided_on": "2026-08-17"}]}
    """
    raw = load(root, "seat_exclusions.json")
    if raw is None:
        return set()

    entries = raw.get("excluded", []) if isinstance(raw, dict) else raw
    out: set[str] = set()
    for entry in entries or []:
        if isinstance(entry, str):
            out.add(entry)
        elif isinstance(entry, dict) and entry.get("user_id"):
            out.add(str(entry["user_id"]))
    return out


def seat_exclusion_note(root: Path, applied: int) -> str:
    """What the reader is told about a denominator that has been adjusted.

    An exclusion changes the headline money figure, so it is stated rather than
    applied quietly. No identifier reaches this sentence — the count is the
    whole of what a reader needs and the whole of what they may have.
    """
    if not applied:
        return ""
    return (
        f"{applied} account(s) recorded as holding no active seat — service "
        "accounts and similar — are excluded from every seat count here, on the "
        "operator's standing instruction. Without that exclusion the seat total "
        "and the recoverable figure would both be higher than the truth."
    )


# --------------------------------------------------------------------------
# connector aliases
# --------------------------------------------------------------------------

def connector_aliases(root: Path) -> dict[str, str]:
    """Bare identifier -> the name a reader would recognise.

    Nothing in the lake can resolve these. A connection that reports as
    `19b950ec-…` reports that way in every dataset, so the report can only say
    "an unnamed connection" — true, useless, and impossible to act on, which
    matters most for the largest ones. The mapping comes out of the admin
    console once, by hand, and is honoured from then on.

        {"19b950ec-0c72-4e2e-9d3e-8a1f4c6b2e77": "Salesforce"}
        {"aliases": {"19b950ec-…": "Salesforce"}}
    """
    raw = load(root, "connector_aliases.json")
    if raw is None:
        return {}
    mapping = raw.get("aliases", raw) if isinstance(raw, dict) else {}
    return {str(k): str(v) for k, v in (mapping or {}).items() if k and v}


# --------------------------------------------------------------------------
# what is in force
# --------------------------------------------------------------------------

def describe(root: Path) -> list[JsonObject]:
    """Which config files exist and how many entries each carries.

    Printed by `caio check`, so an operator can see at a glance that the
    decision they recorded last month is still being honoured this week.
    """
    out = []
    for name, purpose in KNOWN.items():
        path = config_dir(root) / name
        entry: JsonObject = {"file": name, "path": str(path),
                             "present": path.exists(), "purpose": purpose}
        if path.exists():
            try:
                if name == "seat_exclusions.json":
                    entry["entries"] = len(seat_exclusions(root))
                elif name == "connector_aliases.json":
                    entry["entries"] = len(connector_aliases(root))
            except ValueError as exc:
                entry["error"] = str(exc)
        out.append(entry)
    return out


def render_human(entries: list[JsonObject]) -> str:
    lines = ["Standing decisions in force for this lake", ""]
    for entry in entries:
        if entry.get("error"):
            lines.append(f"  BROKEN  {entry['file']}: {entry['error']}")
        elif entry["present"]:
            lines.append(f"  in use  {entry['file']} — {entry.get('entries', 0)} entry(s)")
        else:
            lines.append(f"  none    {entry['file']} — not present, nothing excluded or renamed")
    lines += ["", "Write these under " + str(Path("<data-dir>").joinpath(*CONFIG_DIR)) + ".", ""]
    for entry in entries:
        lines.append(f"  {entry['file']}: {entry['purpose']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Show the standing decisions this lake carries.")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    entries = describe(Path(args.data_dir))
    print(json.dumps(entries, indent=2) if args.json else render_human(entries), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
