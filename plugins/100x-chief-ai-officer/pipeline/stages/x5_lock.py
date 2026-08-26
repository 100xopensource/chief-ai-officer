#!/usr/bin/env python3
"""x5 — lock the findings into a ledger, and say how each one moved.

A weekly report that only says what is true this week is a worse product than
one that says what changed. "Still happening, third week running" is a
different sentence from "new this week", and it is the difference between a
reader acting and a reader filing.

So every finding gets a stable key, and every run compares this week's findings
against the ledger of previous runs to assign one of five statuses:

    new         not present in the previous run
    recurring   present, and materially unchanged
    worsening   present, and its measure moved the wrong way
    improving   present, and its measure moved the right way
    resolved    present last time, absent now

Why the key is derived, not assigned
------------------------------------
A finding's key comes from what it is about — the report, the rule that
produced it, and the thing it concerns — never from its position in a list.
Position-based ids renumber the moment a finding drops out, and a renumbered
ledger silently reports every finding as new. That happened once and cost the
report its credibility for a month.

Why zero findings is a legal result
-----------------------------------
Nothing here pads. If the rules produce no findings, the ledger says so and the
report says so. A report that always finds five things is a report that finds
five things whether or not they are there.

Usage
-----
    python3 -m pipeline.stages.x5_lock --data-dir data --report exposure
    python3 -m pipeline.stages.x5_lock --data-dir data --all
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
from typing import Any

from pipeline.stages import _window as W
from pipeline.stages import x2_metrics_exposure, x2_metrics_value, x2_metrics_waste
from pipeline.stages.x0_gapcheck import resolve_window

JsonObject = dict[str, Any]

LEDGER_FILE = "ledger.json"

# A measure has to move by more than this to count as movement rather than
# noise. Weekly figures wobble; a report that calls a 2% drift "improving"
# teaches its reader to ignore the word.
MATERIAL_CHANGE_PCT = 15.0

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


# --------------------------------------------------------------------------
# deriving findings from metrics
# --------------------------------------------------------------------------

def findings_for_waste(metrics: JsonObject) -> list[JsonObject]:
    out: list[JsonObject] = []
    spend = metrics["spend"]
    seats = metrics.get("seats", {})
    cache = metrics.get("cache", {})
    concentration = metrics.get("concentration", {})

    if seats.get("available") and seats.get("seats_silent_and_established", 0) >= W.SMALL_GROUP_FLOOR:
        out.append({
            "key": "waste/idle-seats",
            "headline": "Seats that cost money and did nothing.",
            "detail": (
                f"{seats['silent_established_described']} held a seat for the whole week "
                f"and recorded no spend at all. Priced at what an active seat costs "
                f"({W.money(seats['average_spend_per_active_seat_usd'])} a week), that is "
                f"about {W.money(seats['recoverable_if_removed_usd'])} a week, or "
                f"{W.money(seats['recoverable_if_removed_usd'] * 52)} a year, sitting in "
                "seats nobody used. " + seats["floor_note"]
            ),
            "severity": "high" if seats["recoverable_if_removed_usd"] > 500 else "medium",
            "confidence": "measured",
            "owner": "Finance",
            "action": "Take the list of established silent seats into the renewal conversation.",
            "count_label": seats["silent_established_described"],
            "measure": seats["recoverable_if_removed_usd"],
            "direction": "down_is_better",
        })

    if cache.get("available") and cache["cache_read_share_pct"] < 50:
        out.append({
            "key": "waste/cache-underused",
            "headline": "Most of what is sent to the model is charged at full price.",
            "detail": (
                f"Only {cache['cache_read_share_pct']:.0f}% of everything sent to the model "
                "came from cache. Cache is billed at a fraction of fresh input, so this is "
                "the cheapest saving on this page — it changes how requests are made, not "
                "what anyone does."
            ),
            "severity": "medium",
            "confidence": "measured",
            "owner": "Platform",
            "action": "Reuse a stable prefix across requests so the cache can be hit.",
            "count_label": f"{cache['cache_read_share_pct']:.0f}% from cache",
            "measure": cache["cache_read_share_pct"],
            "direction": "up_is_better",
        })

    if spend.get("change_pct") is not None and spend["change_pct"] > 25:
        out.append({
            "key": "waste/spend-rising",
            "headline": "Spend is up sharply against its own recent average.",
            "detail": (
                f"This week cost {W.money(spend['week_usd'])} against a "
                f"{W.money(spend['prior_weekly_average_usd'])} average over the four weeks "
                f"before it — {spend['change_pct']:+.0f}%. At this week's rate the year "
                f"costs {W.money(spend['annualised_run_rate_usd'])}. {spend['run_rate_note']}"
            ),
            "severity": "medium",
            "confidence": "measured",
            "owner": "Finance",
            "action": "Confirm the rise is work you wanted before it becomes the baseline.",
            "count_label": f"{spend['change_pct']:+.0f}% against the four-week average",
            "measure": spend["week_usd"],
            "direction": "down_is_better",
        })

    if concentration.get("available") and concentration["top_group_share_pct"] >= 50:
        out.append({
            "key": "waste/concentration",
            "headline": "A small group accounts for most of the bill.",
            "detail": concentration["reader_note"] + (
                " This is not a problem to fix. It is who to ask what is working, and "
                "whose loss would change the renewal case most."
            ),
            "severity": "low",
            "confidence": "measured",
            "owner": "Head of AI",
            "action": "Talk to the heaviest users before renewal, not after.",
            "count_label": f"{concentration['top_group_share_pct']:.0f}% of spend",
            "measure": concentration["top_group_share_pct"],
            "direction": "neutral",
        })

    return out


def findings_for_value(metrics: JsonObject) -> list[JsonObject]:
    out: list[JsonObject] = []
    adoption = metrics.get("adoption", {})
    depth = metrics.get("depth", {})
    friction = metrics.get("friction", {})
    connectors = metrics.get("connectors", {})

    if friction.get("available") and friction.get("worst") and \
            friction["worst"]["error_rate_pct"] >= 15:
        worst = friction["worst"]
        out.append({
            "key": f"value/failing-connector/{worst['name']}",
            "headline": "A data connection is failing often enough that people work around it.",
            "detail": (
                f"One connection failed {worst['error_rate_pct']:.0f}% of the time this week "
                f"— {worst['errors']:,} failures out of {worst['attempts']:,} attempts. "
                "Nobody files a ticket about this; they go back to doing it by hand, which "
                "costs more and shows up nowhere. " + friction["threshold_note"]
            ),
            "severity": "high" if worst["error_rate_pct"] >= 25 else "medium",
            "confidence": "measured",
            "owner": "Platform",
            "action": "Fix the failing connection, or retire it and say so.",
            "count_label": f"{worst['error_rate_pct']:.0f}% of calls failed",
            "measure": worst["error_rate_pct"],
            "direction": "down_is_better",
        })

    if connectors.get("available") and connectors.get("faded"):
        faded = connectors["faded"][0]
        out.append({
            "key": f"value/abandoned/{faded['name']}",
            "headline": "Something people relied on is being abandoned quietly.",
            "detail": (
                f"One data connection went from about {faded['was_weekly_calls']:,} calls a "
                f"week to {faded['now_calls']:,}. " + connectors["faded_note"]
            ),
            "severity": "medium",
            "confidence": "measured",
            "owner": "Head of AI",
            "action": "Ask the people who stopped using it why, before the capability is lost.",
            "count_label": f"down to {faded['now_calls']:,} calls",
            "measure": faded["now_calls"],
            "direction": "up_is_better",
        })

    if connectors.get("available") and connectors.get("unnamed_count", 0) > 0:
        out.append({
            "key": "value/unnamed-connectors",
            "headline": "Some data connections have no name and no owner.",
            "detail": (
                f"{connectors['unnamed_count']} of the {connectors['count_in_use']} "
                "connections in use appear under a bare identifier rather than a product "
                "name. They may be entirely legitimate. The finding is that nobody can "
                "currently say which, and a connection nobody can name is a connection "
                "nobody is reviewing."
            ),
            "severity": "medium",
            "confidence": "measured",
            "owner": "IT",
            "action": "Attach an owner and a readable name to each in the admin settings.",
            "count_label": f"{connectors['unnamed_count']} unnamed",
            "measure": connectors["unnamed_count"],
            "direction": "down_is_better",
        })

    if depth.get("available") and depth["habitual_share_pct"] < 40:
        out.append({
            "key": "value/shallow-adoption",
            "headline": "Most people who try it do not come back.",
            "detail": depth["reader_note"] + (
                " Spend follows the habitual group; the case for renewal rests on whether "
                "that group grows, not on how many people opened it once."
            ),
            "severity": "medium",
            "confidence": "measured",
            "owner": "Head of AI",
            "action": "Find out what the habitual group does differently, and teach that.",
            "count_label": f"{depth['habitual_share_pct']:.0f}% use it regularly",
            "measure": depth["habitual_share_pct"],
            "direction": "up_is_better",
        })

    if adoption.get("available") and adoption.get("change_vs_prior_pct") is not None \
            and adoption["change_vs_prior_pct"] <= -15:
        out.append({
            "key": "value/adoption-falling",
            "headline": "Fewer people used it than in recent weeks.",
            "detail": adoption["reader_note"] + (
                " A single week is not a trend, and holiday weeks read exactly like this. "
                "It is worth one look, not an intervention."
            ),
            "severity": "low",
            "confidence": "measured",
            "owner": "Head of AI",
            "action": "Check the calendar before reading anything into it.",
            "count_label": f"{adoption['change_vs_prior_pct']:+.0f}% active people",
            "measure": adoption["active_people"],
            "direction": "up_is_better",
        })

    return out


def findings_for_exposure(metrics: JsonObject) -> list[JsonObject]:
    out: list[JsonObject] = []
    leads = metrics.get("leads", {})
    arrival = metrics.get("arrival", {})
    reach = metrics.get("reach", {})
    survival = metrics.get("survival", {})
    blind = metrics.get("blind_spots", {})
    governance = metrics.get("governance", {})

    judgement = metrics.get("judgement", {})

    # Confirmed findings first: these were read and independently agreed, and
    # they are the only thing on the page that is a finding rather than a lead.
    if judgement.get("available") and judgement["statuses"]["confirmed"]:
        confirmed = judgement["statuses"]["confirmed"]
        families = ", ".join(judgement["confirmed_by_family"]) or "several categories"
        out.append({
            "key": "exposure/confirmed",
            "headline": "Sensitive material confirmed by reading it.",
            "detail": (
                f"{confirmed:,} flagged passages were read and judged real, then "
                f"independently re-read by a second pass that agreed. Covering "
                f"{families}. " + judgement["reader_note"] + " "
                + (judgement.get("stand_in_warning") or "")
            ).strip(),
            "severity": "high",
            "confidence": "confirmed",
            "owner": "Compliance",
            "action": "Route these to their named reviewers; each one is real.",
            "count_label": f"{confirmed:,} confirmed",
            "measure": confirmed,
            "direction": "down_is_better",
        })

    if judgement.get("available") and judgement["statuses"]["disputed"]:
        disputed = judgement["statuses"]["disputed"]
        out.append({
            "key": "exposure/disputed",
            "headline": "The two readings disagreed about some passages.",
            "detail": (
                f"{disputed:,} passages were called real by the first reader and not "
                "by the second. They are published as neither confirmed nor cleared, "
                "because a disagreement is a fact about the material rather than a "
                "tie to break silently. A person should settle these."
            ),
            "severity": "medium",
            "confidence": "measured",
            "owner": "Compliance",
            "action": "Have a person read the disputed passages and settle them.",
            "count_label": f"{disputed:,} disputed",
            "measure": disputed,
            "direction": "down_is_better",
        })

    for family_id, info in (leads.get("by_family") or {}).items():
        if info["count"] < 5:
            continue
        out.append({
            "key": f"exposure/family/{family_id}",
            "headline": info["title"] + " — matched in this week's conversations.",
            "detail": (
                f"{info['count']:,} passages matched this family's patterns. "
                f"{info['why_it_matters']} These are candidates, not confirmed findings: "
                "they have been matched, but nobody has read them. " + survival.get("method_note", "")
            ),
            "severity": "high" if family_id in ("D1", "D3") else "medium",
            "confidence": "lead",
            "owner": "Compliance",
            "action": "Read the strongest matches and confirm or clear each one.",
            "count_label": f"{info['count']:,} matched",
            "measure": info["count"],
            "direction": "down_is_better",
        })

    if arrival.get("machine_share_pct", 0) >= 50:
        out.append({
            "key": "exposure/arrived-via-tools",
            "headline": "Most of what was matched was handed back by tools, not typed by anyone.",
            "detail": arrival["reader_note"] + " " + arrival.get("error_note", ""),
            "severity": "high",
            "confidence": "measured",
            "owner": "Platform",
            "action": "Change what the tools return, rather than briefing people.",
            "count_label": f"{arrival['machine_share_pct']:.0f}% came from tools",
            "measure": arrival["machine_share_pct"],
            "direction": "down_is_better",
        })

    if blind.get("unreadable_share_of_spend_pct", 0) >= 40:
        out.append({
            "key": "exposure/blind-spot",
            "headline": "Most activity runs where this report cannot look.",
            "detail": blind["reader_note"],
            "severity": "medium",
            "confidence": "measured",
            "owner": "Head of AI",
            "action": "Decide whether to turn on usage-log collection for the rest.",
            "count_label": f"{blind['unreadable_share_of_spend_pct']:.0f}% not readable",
            "measure": blind["unreadable_share_of_spend_pct"],
            "direction": "down_is_better",
        })

    empty = governance.get("conversations_with_no_messages", 0)
    if empty:
        out.append({
            "key": "exposure/empty-exports",
            "headline": "Some conversations export with nothing readable inside.",
            "detail": (
                f"{empty:,} conversations came back with a record but no readable messages. "
                "From outside we cannot tell whether that is ordinary delete-then-export "
                "behaviour or something dropping records. Until we know, we cannot say the "
                "feed shows everything."
            ),
            "severity": "medium",
            "confidence": "measured",
            "owner": "IT",
            "action": "Decide the retention policy, then check exports come back complete.",
            "count_label": f"{empty:,} conversations",
            "measure": empty,
            "direction": "down_is_better",
        })

    if reach.get("people_with_leads", 0) >= W.SMALL_GROUP_FLOOR:
        out.append({
            "key": "exposure/reach",
            "headline": "The material is spread across a large share of the people using it.",
            "detail": (
                f"{reach['people_with_leads_described']} had at least one match in their "
                f"conversations, out of {reach['people_active']:,} active in the week. "
                + reach["floor_note"]
            ),
            "severity": "medium",
            "confidence": "lead",
            "owner": "Compliance",
            "action": "Treat this as a systems question, not a small number of individuals.",
            "count_label": reach["people_with_leads_described"],
            "measure": reach["people_with_leads"],
            "direction": "down_is_better",
        })

    return out


DERIVERS = {
    "waste": (x2_metrics_waste, findings_for_waste),
    "value": (x2_metrics_value, findings_for_value),
    "exposure": (x2_metrics_exposure, findings_for_exposure),
}


# --------------------------------------------------------------------------
# the ledger
# --------------------------------------------------------------------------

def ledger_path(root: Path) -> Path:
    return root / "_reports" / LEDGER_FILE


def read_ledger(root: Path) -> JsonObject:
    path = ledger_path(root)
    if not path.exists():
        return {"runs": [], "findings": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"runs": [], "findings": {}}


def assign_status(finding: JsonObject, previous: JsonObject | None) -> tuple[str, str]:
    """Status plus the sentence a reader sees about it."""
    if previous is None:
        return "new", "New this week."

    before = previous.get("measure")
    now = finding.get("measure")
    seen = int(previous.get("weeks_seen", 1)) + 1

    if not isinstance(before, (int, float)) or not isinstance(now, (int, float)) or not before:
        return "recurring", f"Seen in {seen} runs in a row."

    change = 100.0 * (now - before) / abs(before)
    direction = finding.get("direction", "neutral")

    if abs(change) < MATERIAL_CHANGE_PCT or direction == "neutral":
        return "recurring", f"Seen in {seen} runs in a row, at about the same level."

    worse = change > 0 if direction == "down_is_better" else change < 0
    if worse:
        return "worsening", f"Seen in {seen} runs in a row, and moving the wrong way."
    return "improving", f"Seen in {seen} runs in a row, and moving the right way."


def lock(root: Path, report: str, window: JsonObject | None = None,
         *, write: bool = True) -> JsonObject:
    module, derive = DERIVERS[report]
    window = window or resolve_window(W.resolve_as_of(root))
    metrics = module.compute(root, window)
    fresh = derive(metrics)

    ledger = read_ledger(root)
    history: dict[str, JsonObject] = ledger.get("findings", {})

    locked = []
    for finding in fresh:
        previous = history.get(finding["key"])
        status, status_note = assign_status(finding, previous)
        locked.append({
            **finding,
            "status": status,
            "status_note": status_note,
            "weeks_seen": int((previous or {}).get("weeks_seen", 0)) + 1,
            "first_seen_week": (previous or {}).get("first_seen_week", window["week_start"]),
            "previous_measure": (previous or {}).get("measure"),
        })

    locked.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 3), -abs(f.get("measure") or 0)))

    fresh_keys = {f["key"] for f in fresh}
    resolved = [
        {**info, "key": key, "status": "resolved",
         "status_note": "Present in the previous run, absent now."}
        for key, info in history.items()
        if key.startswith(f"{report}/") and key not in fresh_keys
    ]

    result = {
        "stage": "x5_lock",
        "report": report,
        "generated_at": _now(),
        "window": window,
        "findings": locked,
        "resolved": resolved,
        "counts": {
            "total": len(locked),
            "new": sum(1 for f in locked if f["status"] == "new"),
            "recurring": sum(1 for f in locked if f["status"] == "recurring"),
            "worsening": sum(1 for f in locked if f["status"] == "worsening"),
            "improving": sum(1 for f in locked if f["status"] == "improving"),
            "resolved": len(resolved),
        },
        "metrics": metrics,
        "zero_note": (
            "No findings this week. That is a result, not a gap — the rules ran and "
            "nothing crossed a threshold. Nothing has been padded to fill the page."
        ) if not locked else None,
    }

    if write:
        _persist(root, report, locked, resolved, window)
    return result


def _persist(root: Path, report: str, locked: list[JsonObject],
             resolved: list[JsonObject], window: JsonObject) -> None:
    ledger = read_ledger(root)
    findings = ledger.get("findings", {})

    for finding in locked:
        findings[finding["key"]] = {
            "measure": finding.get("measure"),
            "severity": finding["severity"],
            "weeks_seen": finding["weeks_seen"],
            "first_seen_week": finding["first_seen_week"],
            "last_seen_week": window["week_start"],
            "headline": finding["headline"],
        }
    for finding in resolved:
        findings.pop(finding["key"], None)

    ledger["findings"] = findings
    runs = [r for r in ledger.get("runs", [])
            if not (r.get("report") == report and r.get("week_start") == window["week_start"])]
    runs.append({
        "report": report,
        "week_start": window["week_start"],
        "locked_at": _now(),
        "finding_count": len(locked),
    })
    ledger["runs"] = runs[-200:]

    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser(description="Lock findings into the ledger.")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--report", choices=sorted(DERIVERS))
    ap.add_argument("--all", action="store_true", help="lock every report")
    ap.add_argument("--as-of", help="run date (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true", help="do not write the ledger")
    ap.add_argument("--out-dir", help="also write each report's locked record here")
    args = ap.parse_args()

    if not args.report and not args.all:
        print("x5: choose --report or --all", file=sys.stderr)
        return 2

    root = Path(args.data_dir)
    window = resolve_window(W.resolve_as_of(root, args.as_of))
    reports = sorted(DERIVERS) if args.all else [args.report]

    summary = {}
    for report in reports:
        result = lock(root, report, window, write=not args.dry_run)
        summary[report] = result["counts"]
        if args.out_dir:
            out = Path(args.out_dir) / f"{report}_locked.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"window": window["week_label"], "locked": summary}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
