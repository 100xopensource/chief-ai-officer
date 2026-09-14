#!/usr/bin/env python3
"""Turn a locked findings record into the render block its template expects.

This is the only place where measured numbers become reader-facing sentences,
and it is deliberately the dullest module in the project. Everything it writes
is assembled from values the metric stages computed; it invents no figures,
softens no caveats, and drops nothing that travels with a number.

The rules it enforces, because each one has failed before
---------------------------------------------------------
  · Person-counts under five are words, never digits. A count of three in a
    small company, filtered by a week, is a name.
  · Every finding carries an owner and an action inside the organisation's
    control. A finding nobody can act on was correctly killed.
  · Caveats ship with the numbers they qualify. The validator requires them by
    report kind, because they are the first thing cut in a rewrite and the
    reason the numbers are true.
  · Zero findings renders as zero findings. Nothing is padded.
  · No internal vocabulary survives: no detector ids, no stage names, no week
    numbers. The reader has three minutes.

Usage
-----
    python3 -m pipeline.render.compose --data-dir data --report exposure \\
            --out exposure_block.json
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
from typing import Any

from pipeline.stages import _window as W
from pipeline.stages.x5_lock import lock

JsonObject = dict[str, Any]

BLOCK_VERSION = "v3"

# Confidence, in the reader's words. "measured" means arithmetic over the data;
# "lead" means a pattern matched and nobody has read it yet. Collapsing these
# two into one word is how a lead count gets published as a result.
CONFIDENCE_LABEL = {
    "measured": "measured",
    "lead": "candidate",
    "confirmed": "confirmed",
}

SCOPE_TAG = "Shareable edition · counts and categories only · no names, no content"
FOOTER = "Built from your own Claude usage · nothing leaves your systems"


# --------------------------------------------------------------------------

def compose(root: Path, report: str, *, scope_tag: str | None = None,
            write_ledger: bool = False) -> JsonObject:
    locked = lock(root, report, write=write_ledger)
    builder = {"waste": _waste, "value": _value, "exposure": _exposure}[report]
    return builder(locked, scope_tag or SCOPE_TAG)


def _base(locked: JsonObject, report: str, scope_tag: str) -> JsonObject:
    window = locked["window"]
    return {
        "RENDER_BLOCK": BLOCK_VERSION,
        "report_kind": report,
        "week_label": window["week_label"],
        "week_ending": window["week_end"],
        "scope_tag": scope_tag,
    }


def _trend(metrics_trend: JsonObject | None) -> JsonObject | None:
    """Metric trends carry `weeks`; templates read `series`. Renamed here once."""
    if not metrics_trend:
        return None
    return {
        "title": metrics_trend["title"],
        "unit": metrics_trend.get("unit", ""),
        "series": [{k: v for k, v in point.items() if k != "week_start"}
                   for point in metrics_trend["weeks"]],
        "caption": metrics_trend["caption"],
    }


def _findings(locked: JsonObject) -> list[JsonObject]:
    out = []
    for finding in locked["findings"]:
        out.append({
            "severity": finding["severity"],
            "confidence": CONFIDENCE_LABEL.get(finding["confidence"], finding["confidence"]),
            "count_label": finding["count_label"],
            "headline": finding["headline"],
            "detail": finding["detail"],
            "status": finding["status"],
            "status_note": finding["status_note"],
            "action": {"text": finding["action"], "owner": finding["owner"]},
        })
    return out


def _findings_count(locked: JsonObject) -> str:
    total = locked["counts"]["total"]
    if total == 0:
        return "no findings this week"
    fresh = locked["counts"]["new"]
    word = "finding" if total == 1 else "findings"
    return f"{total} {word}" + (f" · {fresh} new" if fresh else " · none new")


def _moves(locked: JsonObject, limit: int = 4) -> list[JsonObject]:
    """The actions, most consequential first. One per finding, never invented."""
    moves = []
    for rank, finding in enumerate(locked["findings"][:limit], start=1):
        moves.append({
            "rank": rank,
            "move": finding["action"],
            "owner": finding["owner"],
            "when": "this week" if finding["severity"] == "high" else "this month",
            "at_stake": finding["headline"] + " " + finding["status_note"],
        })
    return moves


# --------------------------------------------------------------------------
# waste
# --------------------------------------------------------------------------

def _waste(locked: JsonObject, scope_tag: str) -> JsonObject:
    m = locked["metrics"]
    spend, seats, cache = m["spend"], m["seats"], m["cache"]
    coverage, concentration = m["coverage"], m["concentration"]
    per_month = spend["week_usd"] * 52 / 12

    block = _base(locked, "waste", scope_tag)
    block.update({
        "verdict": [
            f"Claude cost {W.money(spend['week_usd'])} in the reported week, against "
            f"{W.money(spend['prior_weekly_average_usd'])} a week averaged over the four weeks "
            f"before it. Held at this rate the year costs "
            f"{W.money(spend['annualised_run_rate_usd'])} — an upper bound on this week's shape, "
            "not a forecast.",

            (f"{seats['silent_established_described'].capitalize()} held a seat all week and "
             f"spent nothing, which prices out at about "
             f"{W.money(seats['recoverable_if_removed_usd'])} a week. That is the ceiling on "
             "what removing them would save, not a promised saving."
             if seats.get("available") else
             "Seat-level waste cannot be measured: this lake has no record of who holds a seat."),

            f"{coverage['content_visible_share_pct']:.0f}% of the spend is chat, which is the "
            "only part whose content can be read. The cost side is complete for every product; "
            "anything here about what the money bought covers the readable share and says so.",
        ],
        "stats": [
            {"value": W.money(spend["week_usd"]),
             "label": "Spent in the reported week",
             "sub": f"Across {spend['requests']:,} requests. Recent weeks can still be revised "
                    "for about a month as late usage lands."},
            {"value": f"{spend['change_pct']:+.0f}%" if spend["change_pct"] is not None else "—",
             "label": "Against the four-week average",
             "sub": f"{W.money(spend['prior_weekly_average_usd'])} a week over the four weeks "
                    "before this one."},
            {"value": (seats["silent_established_described"] if seats.get("available") else "—"),
             "label": "Seats with no spend all week",
             "sub": "Counted only where the seat existed before the week began, so a new joiner "
                    "is never counted as idle."},
        ],
        "projection": {
            "intro": "Held at this week's rate, and treating the rate as a bound rather than "
                     "a settled figure:",
            "per_month": W.money(per_month),
            "per_month_per_user": (W.money(per_month / seats["seats_total"])
                                   if seats.get("seats_total") else "—"),
            "per_year": W.money(spend["annualised_run_rate_usd"]),
        },
        "trend": _trend(m.get("trend")),
        "verifline": (
            "Every figure was recomputed from the lake by a second, independent pass, and the "
            "per-product amounts add back to the company total. Where a number is an upper "
            "bound rather than a settled figure, it says so in the same sentence."
        ),
        "moves": {
            "total_label": (W.money(seats["recoverable_if_removed_usd"])
                            + " a week at most" if seats.get("available") else ""),
            "items": [
                {"n": move["rank"], "text": move["move"],
                 "amount": move.get("amount", ""), "owner": move["owner"]}
                for move in _moves(locked)
            ],
            "note": "Ordered by how much cost one action removes, not by how alarming it "
                    "sounds. Amounts are ceilings on a saving, not promised savings.",
        },
        "findings_count": _findings_count(locked),
        "findings": [
            {
                "status": finding["status_note"],
                "tags": [finding["severity"]],
                "headline": finding["headline"],
                "the_number": {"value": finding["count_label"], "unit": "this week"},
                "support": [finding["detail"]],
                "action": {"text": finding["action"], "owner": finding["owner"]},
            }
            for finding in locked["findings"]
        ],
        "watch": _watch_waste(m),
        "need_from_you": _need_from_you("waste"),
        "how_to_read": _how_to_read("waste", locked),
        "reconciliation": {
            "week_total_by_product": {
                # Sum the parts the reader can see rather than a separately rounded
                # grand total: rounding each product to the cent leaves the two a
                # cent apart, and a reader who adds the column up is right.
                "total": round(sum(spend["by_product"].values()), 2),
                "parts": [{"label": product, "value": amount}
                          for product, amount in spend["by_product"].items()],
                "explained": None,
            }
        },
        "appendix": [
            {"title": "The week's spend, by product",
             "table": {"head": ["Product", "Spend"],
                       "rows": [[product, W.money(amount)]
                                for product, amount in spend["by_product"].items()]},
             "notes": [coverage["reader_note"]]},
            {"title": "The week's spend, by model",
             "table": {"head": ["Model", "Spend"],
                       "rows": [[model, W.money(amount)]
                                for model, amount in spend["by_model"].items()]},
             "notes": ["Moving work to a cheaper model is the most reversible saving here, "
                       "because nothing about how anyone works has to change."]},
            {"title": "How the seat figure was reached",
             "table": None,
             "notes": [seats.get("floor_note", "No seat directory in this lake."),
                       concentration.get("reader_note", "")]},
        ],
        "footer_note": FOOTER,
    })
    return block


def _watch_waste(m: JsonObject) -> list[JsonObject]:
    """Things that are not findings yet but would be if they continued."""
    out = [
        {"label": "Cache hit rate",
         "detail": (f"{m['cache']['cache_read_share_pct']:.0f}% of input came from cache. "
                    "If this falls, the bill rises without anyone doing anything differently.")
         if m["cache"].get("available") else "Not measurable from this lake."},
        {"label": "Where the spend sits",
         "detail": m["coverage"]["reader_note"]},
        {"label": "The run rate",
         "detail": m["spend"]["run_rate_note"]},
    ]
    if m["concentration"].get("available"):
        out.append({"label": "Concentration",
                    "detail": m["concentration"]["reader_note"]})
    return out


# --------------------------------------------------------------------------
# value
# --------------------------------------------------------------------------

def _value(locked: JsonObject, scope_tag: str) -> JsonObject:
    m = locked["metrics"]
    adoption, depth = m["adoption"], m["depth"]
    reliability, connectors = m["reliability"], m["connectors"]
    skills, coverage = m["skills"], m["coverage"]
    freshness = m.get("freshness", {})

    block = _base(locked, "value", scope_tag)
    block.update({
        "verdict": [
            adoption.get("reader_note", "Adoption cannot be measured from this lake."),

            depth.get("reader_note",
                      "Depth of use cannot be measured: no per-person activity in this window."),

            # The bottom line gets three paragraphs and a reader with three
            # minutes. The full account of what this source cannot answer is
            # worth saying, but it is said once, in the watch grid below; pasting
            # it here too turned the one paragraph about friction into four
            # topics and buried the finding that actually moved.
            ((connectors.get("faded_note", "") if connectors.get("available")
              else "Nothing measurable is getting in the way this week.").strip()
             + " Whether a connection failed is not something this data can say, "
               "so no reliability figure appears anywhere in this report — which "
               "is not the same as nothing having failed.").strip()
            + " " + coverage.get("reader_note", ""),
        ],
        "stats": [
            {"value": adoption.get("active_people_described", "—"),
             "label": "Used Claude in the reported week",
             # The seat total is the operator's adjusted one, and says so. Both
             # reports read it from the same place, so they cannot disagree
             # about how large the organisation is.
             "sub": ((f"Out of {adoption['seats_total']:,} who hold a seat."
                      + (f" {adoption['seats_excluded']} account(s) recorded as holding "
                         "no active seat are excluded." if adoption.get("seats_excluded")
                         else ""))
                     if adoption.get("seats_total") else "Seat total not available.")},
            {"value": (depth.get("habitual_described", "—") if depth.get("available") else "—"),
             "label": "Use it on three days or more",
             "sub": "The group for whom this is part of the job rather than something "
                    "they tried once."},
            {"value": (f"{connectors['count_in_use']}" if connectors.get("available") else "—"),
             "label": "Data connections in use",
             "sub": (f"{connectors['unnamed_count']} appear under a bare identifier with no "
                     "owner attached." if connectors.get("available") else "")},
            # This card used to read "0% of connector calls failed" — computed
            # from a failure count that does not exist in any data source this
            # project reads, so it was zero everywhere and read as a clean bill
            # of health. What replaced it is measured from data that exists.
            {"value": (f"{len(connectors['faded'])}" if connectors.get("available") else "—"),
             "label": "Connections people stopped reaching for",
             # Zero findings is a result here as everywhere, but a bare "0" over
             # a definition reads as though the measurement had not run. Say
             # which of the two it is.
             "sub": (("Used half as often as their own earlier average, or less. Nothing "
                      "errors and nothing alerts; people go back to doing it by hand."
                      if connectors["faded"] else
                      "None this week: nothing in use dropped to half its own earlier "
                      "average or below.")
                     if connectors.get("available") else "")},
        ],
        "trend": _trend(m.get("trend")),
        "verifline": (
            "Every figure was recomputed from the lake by a second, independent pass. Each "
            "data connection carries the date it first appeared, and nothing younger than the "
            "comparison window is shown as growth — a connection that did not exist last month "
            "always looks like infinite growth, and that has been published as a finding before."
        ),
        "moves": _moves(locked),
        "moves_note": (
            "Ordered by how many people each one affects. These figures can still be revised "
            "for about a month as late data lands."
        ),
        "findings_count": _findings_count(locked),
        "findings_sub": (
            "Each is computed from activity the tools recorded, not from anyone's impression "
            "of how it is going."
        ),
        "findings": [
            {
                "headline": finding["headline"],
                "detail": finding["detail"],
                "status": finding["status"],
                "status_note": finding["status_note"],
            }
            for finding in locked["findings"]
        ],
        "scoreboard_sub": (
            "What people reached for, and whether they are still reaching for it. Anything "
            "used by fewer than five people is grouped rather than listed, because a row of "
            "one is a name. Whether a connection failed is not shown: this data source "
            "publishes no failure data, and a blank reliability column would read as good news."
        ),
        "scoreboard_note": connectors.get("unit_note", ""),
        "scoreboard": _scoreboard(connectors),
        "watch": _watch_value(m, skills, reliability, freshness),
        "reconciliation": ({
            "connector_sessions": {
                "total": sum(c["sessions"] for c in connectors["in_use"]),
                "parts": [{"label": label, "value": c["sessions"]}
                          for label, c in zip(_connector_labels(connectors),
                                              connectors["in_use"])],
                "explained": None,
            }
        } if connectors.get("available") else {}),
        "need_from_you": _need_from_you("value"),
        "how_to_read": _how_to_read("value", locked),
        "footer_note": FOOTER,
    })
    return block


def _connector_labels(connectors: JsonObject) -> list[str]:
    """What each connection is called in the report, in rank order.

    A connection that reports under a bare identifier cannot be named — the
    identifier is all the source publishes, and the privacy gate rightly keeps
    it out of a shareable report. But rendering every one of them as the same
    words leaves a reader with two rows reading "an unnamed connection", no way
    to tell which is which, and an action ("look it up in the admin console")
    they cannot carry out against either.

    So they are numbered when there is more than one. The number is a position
    in this week's table and nothing else — it identifies no account and
    survives no further than the page.
    """
    unnamed_total = sum(1 for c in connectors["in_use"] if c["unnamed"])
    labels, seen = [], 0
    for entry in connectors["in_use"]:
        if not entry["unnamed"]:
            labels.append(entry["name"])
            continue
        seen += 1
        labels.append("an unnamed connection" if unnamed_total == 1
                      else f"unnamed connection {seen}")
    return labels


def _scoreboard(connectors: JsonObject) -> JsonObject | None:
    """Per-connection usage, with a plain verdict per row.

    The verdict cell is "flag|text": the template splits on the pipe so the
    colour and the sentence travel together as one flat string.

    There used to be a reliability verdict here, drawn from an error rate. Every
    row rendered grey and read "too few calls to judge", because the error rate
    was computed from fields no data source publishes — so the column said
    nothing about ten connections an organisation had been running since April.
    The verdict now describes the thing the data can actually see: whether the
    connection is being used more, the same, or less than it used to be.
    """
    if not connectors.get("available"):
        return None

    rows = []
    for name, entry in zip(_connector_labels(connectors), connectors["in_use"]):
        change = entry["change_pct"]
        if change is None:
            # Two different absences, told apart. One string for both let a data
            # fault read as the birth-date guard doing its job on purpose.
            flag = "grey"
            verdict = ("New this period — too new to compare"
                       if entry.get("no_change_reason") == "new this period"
                       else "No comparable earlier data")
            change_cell = verdict.split(" — ")[0]
        elif change <= -50:
            flag, verdict, change_cell = "red", "Being abandoned", f"{change:+.0f}%"
        elif change <= -20:
            flag, verdict, change_cell = "amber", "Falling away", f"{change:+.0f}%"
        elif change >= 20:
            flag, verdict, change_cell = "green", "Growing", f"{change:+.0f}%"
        else:
            flag, verdict, change_cell = "green", "Steady", f"{change:+.0f}%"
        rows.append([
            name,
            f"{entry['sessions']:,}",
            entry["people_described"],
            change_cell,
            f"{flag}|{verdict}",
        ])
    return {"head": ["Connection", "Sessions", "People", "Change", "Verdict"], "rows": rows}


def _watch_value(m: JsonObject, skills: JsonObject, reliability: JsonObject,
                 freshness: JsonObject) -> list[JsonObject]:
    out = [
        {"label": "What people reached for",
         "detail": ((
             "The most-used capabilities this week were "
             + ", ".join(s["name"] for s in skills.get("in_use", [])[:4])
             + f", across {skills.get('sessions_total', 0):,} sessions. "
             + skills.get("unit_note", ""))
             if skills.get("available") and skills.get("in_use")
             else (skills.get("why")
                   or "No capability-level usage is recorded in this lake."))},
        # When the connector numbers cannot be computed at all, say so where the
        # numbers would have been. An empty section reads as "nothing to report";
        # a named absence reads as what it is.
        {"label": "Connections could not be measured",
         "detail": ("" if m["connectors"].get("available")
                    else (m["connectors"].get("why", "")
                          + ". Nothing about connections is shown below rather than "
                            "shown as zero: this is missing data, not an absence of use."))},
        {"label": "New connections",
         "detail": (m["connectors"].get("birth_note", ""))},
        {"label": "Quiet abandonment",
         "detail": m["connectors"].get("faded_note", "")},
        {"label": "Connections nobody can name",
         "detail": m["connectors"].get("unnamed_note", "")},
        # Stated rather than omitted. A reader who remembers a reliability
        # figure is owed the reason it is gone, and "no data" must never be
        # allowed to arrive looking like "no failures".
        {"label": "What this data cannot answer",
         "detail": reliability["reader_note"]},
        {"label": "How complete this week is",
         "detail": (freshness.get("reader_note", "") if freshness.get("any_short") else "")},
        {"label": "What can be read",
         "detail": m["coverage"]["reader_note"]},
    ]
    return [item for item in out if item["detail"]]


# --------------------------------------------------------------------------
# exposure
# --------------------------------------------------------------------------

def _exposure_headline(leads: JsonObject, survival: JsonObject,
                       judgement: JsonObject) -> str:
    """The bottom line, which changes shape entirely once anything is read."""
    if judgement.get("available") and judgement["statuses"]["confirmed"]:
        statuses = judgement["statuses"]
        return (
            f"{statuses['confirmed']:,} pieces of sensitive material were confirmed by "
            f"reading them: judged real by one reader, then independently re-read by a "
            f"second that agreed. A further {statuses['cleared']:,} flagged passages were "
            f"read and cleared as false alarms"
            + (f", and {statuses['disputed']:,} are disputed between the two readings"
               if statuses["disputed"] else "")
            + f". {leads['week']:,} passages matched a pattern in the reported week; "
              "matching is not the same as being real, which is what the reading settles."
        )
    if judgement.get("available"):
        return (
            f"{judgement['passages_read']:,} flagged passages were read and every one was "
            f"cleared as a false alarm. {leads['week']:,} passages matched a pattern in the "
            "reported week; none of what has been read so far turned out to be real. "
            + judgement.get("floor_note", "")
        )
    return (
        f"{leads['week']:,} passages in the reported week matched a sensitive-data pattern. "
        f"Between {survival['expected_findings_low']:,} and "
        f"{survival['expected_findings_high']:,} of those would be expected to survive being "
        "read. Nothing here has been read yet, so every one is a candidate and none is a "
        "confirmed finding."
    )


def _exposure_verifline(judgement: JsonObject) -> str:
    """Who read this, and what that means. Named, because it decides how much
    weight the page can carry."""
    base = (
        "A pattern match is a candidate; it becomes a finding only after it has been read "
        "in place and an independent second pass has agreed. In earlier audits about 44% "
        "of candidates survived that reading and some families survived none, so "
        "publishing match counts as results would overstate what is there by more than "
        "double."
    )
    if not judgement.get("available"):
        return "Nothing here has been read. " + base
    if not judgement.get("is_a_real_reading"):
        share = judgement.get("stand_in_share_pct", 100.0)
        return (
            f"{share:.0f}% of the verdicts behind this report were produced offline by a "
            "stand-in — not read by a person and not read by a model. They show the shape "
            "of the result and must not be taken as an actual reading. " + base
        )
    return (
        "Every confirmed item was read in place and re-read by a second, independent pass "
        "that was never shown the first pass's answer. "
        f"{judgement['read_share_pct']:.0f}% of flagged passages have been read, "
        f"strongest-first, and {judgement['measured_survival_pct']:.0f}% of what was read "
        "survived both passes. " + base
    )


def _exposure(locked: JsonObject, scope_tag: str) -> JsonObject:
    m = locked["metrics"]
    leads, arrival = m["leads"], m["arrival"]
    reach, survival = m["reach"], m["survival"]
    blind, truncation = m["blind_spots"], m["truncation"]
    judgement = m.get("judgement", {})

    block = _base(locked, "exposure", scope_tag)
    block.update({
        "verdict": [
            f"{leads['week']:,} passages in the reported week matched a sensitive-data pattern. "
            f"Between {survival['expected_findings_low']:,} and "
            f"{survival['expected_findings_high']:,} of those would be expected to survive being "
            "read. Nothing here has been read yet, so every one is a candidate and none is a "
            "confirmed finding.",

            arrival.get("reader_note", "") + " " + reach.get("floor_note", ""),

            blind.get("reader_note", "") + " " + truncation.get("reader_note", ""),
        ],
        "stats": [
            {"value": f"{leads['week']:,}",
             "label": "Passages matched in the reported week",
             "sub": f"Out of {leads['blocks_scanned']:,} pieces of conversation examined. "
                    "Candidates, not confirmed findings."},
            (
                {"value": f"{judgement['statuses']['confirmed']:,}",
                 "label": "Confirmed by reading them",
                 "sub": "Read, judged real, then independently re-read by a second pass "
                        "that agreed. These are findings, not candidates."}
                if judgement.get("available") and judgement["statuses"]["confirmed"] else
                {"value": f"{survival['expected_findings_low']:,}–"
                          f"{survival['expected_findings_high']:,}",
                 "label": "Expected to survive being read",
                 "sub": "From how each pattern has behaved in earlier audits. A prediction "
                        "about this week, not a result from it."}
            ),
            {"value": f"{arrival.get('machine_share_pct', 0):.0f}%",
             "label": "Arrived through tools, not typed",
             "sub": "Handed back inside tool results or sent into them. This share cannot be "
                    "fixed by briefing people."},
            {"value": reach.get("people_with_leads_described", "—"),
             "label": "People with at least one match",
             "sub": f"Out of {reach.get('people_active', 0):,} active in the week. At least this "
                    "many — the true number cannot be smaller."},
        ],
        "surfaces": {
            "title": "How the material got there",
            "sub": "Only one of these three is addressed by talking to people. "
                   "The other two are engineering.",
            "segments": [{"label": segment["english"], "value": round(segment["share_pct"])}
                         for segment in arrival.get("segments", [])],
        },
        "trend": _trend(m.get("trend")),
        "verifline": _exposure_verifline(judgement),
        "moves": _moves(locked),
        "moves_note": (
            "Ordered by how much exposure one action removes. No money figures: pricing these "
            "would need a loaded hourly rate this pipeline does not have."
        ),
        "findings_count": _findings_count(locked),
        "findings_sub": (
            "Each carries how serious it would be if confirmed and how certain we are today. "
            "'Measured' means arithmetic over the data. 'Candidate' means a pattern matched "
            "and nobody has read it yet."
        ),
        "findings": _findings(locked),
        "safeguards_sub": (
            "Four settings do most of the work here. This pipeline cannot read them — they live "
            "in the administration screens — so they are reported as last known, never as "
            "verified."
        ),
        "safeguards_note": "Not verified by this run",
        "safeguards": _safeguards(),
        "math_sub": "Where the headline count comes from, so it can be argued with.",
        "reconciliation": {
            "matches_by_kind": {
                "total": leads["week"],
                "parts": [{"label": info["title"], "value": info["count"]}
                          for info in leads["by_family"].values()],
                "explained": None,
            }
        },
        "math_notes": [
            survival["reader_note"],
            survival["method_note"],
            truncation["reader_note"],
            m["reading_this"],
        ],
        "need_from_you": _need_from_you("exposure"),
        "how_to_read": _how_to_read("exposure", locked),
        "footer_note": FOOTER,
    })
    return block


def _safeguards() -> list[JsonObject]:
    """The four settings, always reported as unread rather than assumed.

    This pipeline has no way to read them. Rendering them as "on" because
    nothing contradicted it would be the single most dangerous sentence in the
    report, so the only state it can report by itself is "not checked".
    """
    return [
        {"name": "Retention limit",
         "state": "unknown",
         "detail": "Conversation data is deleted automatically after a set time, so sensitive "
                   "material does not accumulate indefinitely."},
        {"name": "Content redaction",
         "state": "unknown",
         "detail": "Sensitive details are masked automatically before anything is stored."},
        {"name": "Sector controls",
         "state": "unknown",
         "detail": "Stricter handling rules for regulated categories such as health data."},
        {"name": "Single sign-on",
         "state": "unknown",
         "detail": "Everyone signs in through the company's own login, so access can be "
                   "granted and removed centrally."},
    ]


# --------------------------------------------------------------------------
# shared reader sections
# --------------------------------------------------------------------------

def _need_from_you(report: str) -> list[str]:
    shared = [
        "An administrator readout of the four safety settings, or a key that can read them, "
        "so they can be reported as verified rather than as last known.",
        "Which account this audit's own work ran under, so its usage can be taken out of "
        "the figures.",
    ]
    if report == "waste":
        return [
            "Your seat price and contract end date, so the seat figures can be turned into "
            "a renewal position rather than a weekly number.",
            "Confirmation of who is expected to hold a seat but not use it — a shared "
            "account, a service account, someone on leave.",
        ] + shared
    if report == "value":
        return [
            "Which teams matter most this quarter, so the numbers can be grouped the way "
            "you make decisions rather than the way the data arrives.",
            "Your average loaded hourly rate, if you want any of this in money terms.",
        ] + shared
    return [
        "Whether the company is formally covered by health-data privacy rules. It decides "
        "whether the medical items must be described in regulated terms or the plainer "
        "ones used here.",
        "Named reviewers for the categories that need a human decision, so confirmed items "
        "have somewhere to go.",
    ] + shared


def _how_to_read(report: str, locked: JsonObject) -> list[str]:
    window = locked["window"]
    lines = [
        f"\"The reported week\" means {window['week_start']} to {window['week_end']}. "
        f"Where something is compared, it is compared against {window['comparison_label']}.",

        window["note"],

        "Every finding says how it moved since the last run: new, still here, getting "
        "worse, or getting better. A finding that was here last time and is gone now is "
        "recorded as cleared rather than quietly dropped.",

        "Any group of fewer than five people is described as \"fewer than five\" and never "
        "as a digit. In a small company an exact count, filtered by a week and a team, is "
        "close enough to a name.",

        "These figures can still be revised for about a month as late data lands.",

        "This is the shareable edition: counts, categories and dates only — no names, no "
        "email addresses, no filenames, no conversation content.",
    ]

    if report == "exposure":
        lines[3:3] = [
            "A pattern match is a candidate, never a finding. It becomes a finding only "
            "once someone has read it in place. Every count here is a floor: at least this "
            "many, and the true number cannot be smaller.",

            "Four places were examined: what people typed, what was sent into tools, what "
            "tools handed back, and the files attached to conversations. Errors thrown by "
            "tools are examined too, counted as part of what the tool returned.",

            "Some tool results are cut off at about 10,000 characters at the source. "
            "Anything past the cut cannot be seen by any examination, which is one reason "
            "every count is a floor.",

            "The feed this report reads covers chat conversations only. Other products "
            "cannot be seen at all, so this report does not guess about them.",
        ]
    elif report == "value":
        lines[3:3] = [
            "Each data connection carries the date it first appeared. Nothing younger than "
            "the comparison window is shown as growth.",

            "Reading what people were actually doing is only possible for chat; other "
            "products cannot be seen, so anything about the work itself covers that share "
            "and no more.",
        ]
    else:
        lines[3:3] = [
            "Money figures are upper bounds on a saving — what it would be worth if the "
            "whole thing went away — rather than a promised saving.",

            "The cost side is complete for every product. Only chat can be read for what "
            "the money bought, so anything about the work itself covers that share.",
        ]
    return lines


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Compose a render block from the ledger.")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--report", required=True, choices=["waste", "value", "exposure"])
    ap.add_argument("--scope-tag", help="override the edition label in the masthead")
    ap.add_argument("--write-ledger", action="store_true",
                    help="persist finding statuses (the pipeline run does this once)")
    ap.add_argument("--out", help="write the render block here")
    args = ap.parse_args()

    block = compose(Path(args.data_dir), args.report,
                    scope_tag=args.scope_tag, write_ledger=args.write_ledger)
    payload = json.dumps(block, indent=2, ensure_ascii=False)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
