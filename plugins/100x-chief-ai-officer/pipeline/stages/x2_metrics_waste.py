#!/usr/bin/env python3
"""x2 — the Waste Ledger's numbers. Deterministic; no model reads anything.

The question this answers, in the reader's words: what did Claude cost, where
is money going that buys nothing, and what does that give us at renewal?

Every figure here is computed by arithmetic over the lake. No model is
consulted, so re-running on the same lake gives the same answer, and a
disagreement about a number is settled by reading this file rather than by
asking what a model was thinking.

The five things it measures
---------------------------
  spend           total, by product, by model, week over week
  idle seats      people who hold a seat and cost nothing because they do
                  nothing. The single most common renewal lever, and the one
                  most often overstated — see the floor note below.
  concentration   how much of the bill a handful of people account for. High
                  concentration is not waste; it is a fact about who to talk to
                  before renewal.
  cache           how much of the input was served from cache. Cheap to improve
                  and invisible unless somebody looks.
  price mix       spend by model. A cheaper model doing the same work is the
                  most reversible saving available.

Why idle seats are reported as a range, not a number
-----------------------------------------------------
A seat with no recorded spend in the window is not proven idle. The person may
have been on leave, may have joined last week, or may use a product this lake
cannot see. So this stage reports two figures: seats with no spend in the
window, and seats with no spend in the window that also existed before it
started. The second is the defensible one, and it is the one the report leads
with. A predecessor published the first as a headline and had to retract it.

Usage
-----
    python3 -m pipeline.stages.x2_metrics_waste --data-dir data
    python3 -m pipeline.stages.x2_metrics_waste --data-dir data --as-of 2026-07-20
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.lake import datalake as lake
from pipeline.stages import _window as W
from pipeline.stages.x0_gapcheck import resolve_window

JsonObject = dict[str, Any]

REPORT = "waste"


def compute(root: Path, window: JsonObject) -> JsonObject:
    start, end = window["week_start"], window["week_end"]
    prior_start, prior_end = window["comparison_start"], window["comparison_end"]

    cost_rows = list(W.rows_in_window(root, "analytics_cost", start, end))
    prior_rows = list(W.rows_in_window(root, "analytics_cost", prior_start, prior_end))

    week_total = W.total(cost_rows, "amount_usd")
    prior_total = W.total(prior_rows, "amount_usd")
    prior_weekly_average = prior_total / 4.0 if prior_rows else 0.0

    by_product = {k: W.round_money(v) for k, v in W.group_sum(cost_rows, "product", "amount_usd").items()}
    by_model = {k: W.round_money(v) for k, v in W.group_sum(cost_rows, "model", "amount_usd").items()}

    return {
        "stage": "x2_metrics_waste",
        "report": REPORT,
        "generated_at": _now(),
        "window": window,
        "spend": {
            "week_usd": W.round_money(week_total),
            "prior_weekly_average_usd": W.round_money(prior_weekly_average),
            "change_pct": W.safe_share(week_total - prior_weekly_average, prior_weekly_average)
            if prior_weekly_average else None,
            "by_product": dict(sorted(by_product.items(), key=lambda kv: -kv[1])),
            "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1])),
            "requests": int(W.total(cost_rows, "requests")),
            "annualised_run_rate_usd": W.round_money(week_total * 52),
            "run_rate_note": (
                "One week multiplied by fifty-two. It is a run rate, not a forecast: "
                "it assumes next week looks like this one, which no week ever quite does."
            ),
        },
        "trend": _weekly_trend(root, window),
        "seats": _seats(root, start, end),
        "concentration": _concentration(root, start, end),
        "cache": _cache(root, start, end),
        "coverage": _coverage(root, cost_rows),
    }


# --------------------------------------------------------------------------

def _weekly_trend(root: Path, window: JsonObject, span: int = 10) -> JsonObject:
    """Spend per week for the run-up to the reported week.

    The last point is the reported week; anything after it would be a partial
    week and is not plotted as if it were complete.
    """
    points = []
    for start, end in W.weeks_back(window["week_start"], span):
        rows = list(W.rows_in_window(root, "analytics_cost", start, end))
        points.append({
            "label": _short_label(start),
            "week_start": start,
            "value": W.round_money(W.total(rows, "amount_usd")),
            "current": start == window["week_start"],
        })
    return {
        "title": "What Claude cost, by week",
        "unit": "usd",
        "weeks": points,
        "caption": (
            "Each point is one complete Monday-to-Sunday week of billed spend. "
            "The marked point is the week this report covers. Recent weeks can "
            "still be revised for about a month as late usage lands."
        ),
    }


def _seats(root: Path, start: str, end: str) -> JsonObject:
    """Who holds a seat, who spent, and what the difference defensibly is."""
    if not W.dataset_exists(root, "directory_users"):
        return {"available": False,
                "why": "no directory_users snapshot — seat counts need the directory pull"}

    directory = list(lake.iter_snapshot(root, "directory_users"))
    seats = {str(u["user_id"]): u for u in directory if u.get("user_id")}

    spenders: set[str] = set()
    spend_by_user: dict[str, float] = {}
    if W.dataset_exists(root, "analytics_user_cost"):
        for row in W.rows_in_window(root, "analytics_user_cost", start, end):
            user = str(row.get("user_id") or "")
            amount = row.get("amount_usd")
            if user and isinstance(amount, (int, float)):
                spend_by_user[user] = spend_by_user.get(user, 0.0) + float(amount)
                if amount > 0:
                    spenders.add(user)

    silent = sorted(set(seats) - spenders)
    # Only seats that already existed when the window opened can be called idle.
    established = [u for u in silent
                   if str(seats[u].get("created_at") or "")[:10] < start]

    per_seat = (W.round_money(sum(spend_by_user.values()) / len(spenders))
                if spenders else 0.0)

    return {
        "available": True,
        "seats_total": len(seats),
        "seats_with_spend": len(spenders),
        "seats_silent": len(silent),
        "seats_silent_and_established": len(established),
        "silent_established_described": W.describe_people(len(established)),
        "average_spend_per_active_seat_usd": per_seat,
        "recoverable_if_removed_usd": W.round_money(per_seat * len(established)),
        "floor_note": (
            f"{len(silent)} seats recorded no spend in this week. Only "
            f"{len(established)} of those existed before the week began, and only "
            "those can fairly be called idle — the rest may simply be new. The "
            "recoverable figure prices the established group at what an active "
            "seat costs, which is an upper bound on the saving, not a promise."
        ),
    }


def _concentration(root: Path, start: str, end: str) -> JsonObject:
    """How much of the bill sits with how few people."""
    if not W.dataset_exists(root, "analytics_user_cost"):
        return {"available": False, "why": "no analytics_user_cost dataset"}

    spend: dict[str, float] = {}
    for row in W.rows_in_window(root, "analytics_user_cost", start, end):
        user = str(row.get("user_id") or "")
        amount = row.get("amount_usd")
        if user and isinstance(amount, (int, float)):
            spend[user] = spend.get(user, 0.0) + float(amount)

    if not spend:
        return {"available": False, "why": "no per-person spend in this window"}

    ranked = sorted(spend.values(), reverse=True)
    grand = sum(ranked)
    top_decile = max(W.SMALL_GROUP_FLOOR, len(ranked) // 10)
    head = sum(ranked[:top_decile])

    return {
        "available": True,
        "people_with_spend": len(ranked),
        "top_group_size": top_decile,
        "top_group_share_pct": W.safe_share(head, grand),
        "top_group_described": W.describe_people(top_decile),
        "reader_note": (
            f"The heaviest {W.describe_people(top_decile)} account for "
            f"{W.safe_share(head, grand):.0f}% of the bill. That is not waste — it is "
            "who to talk to first, both about what is working and about renewal."
        ),
    }


def _cache(root: Path, start: str, end: str) -> JsonObject:
    """Cache hit rate on input. The cheapest saving available, and easy to miss."""
    if not W.dataset_exists(root, "analytics_usage"):
        return {"available": False, "why": "no analytics_usage dataset"}

    rows = list(W.rows_in_window(root, "analytics_usage", start, end))
    cached = W.total(rows, "cache_read_input_tokens")
    uncached = W.total(rows, "uncached_input_tokens")
    created = (W.total(rows, "cache_creation_5m_input_tokens")
               + W.total(rows, "cache_creation_1h_input_tokens"))
    served = cached + uncached

    return {
        "available": True,
        "cache_read_share_pct": W.safe_share(cached, served),
        "input_tokens_total": int(served),
        "cache_creation_tokens": int(created),
        "reader_note": (
            f"{W.safe_share(cached, served):.0f}% of everything sent to the model was "
            "served from cache rather than charged at the full input rate. Cache is "
            "billed at a fraction of fresh input, so this share is the difference "
            "between the bill and a much larger one."
        ),
    }


def _coverage(root: Path, cost_rows: list[JsonObject]) -> JsonObject:
    """What share of spend runs where the conversation feed cannot see.

    Every report in this family states its own blind spot. Cost is visible for
    every product; content is not, and a reader comparing the two deserves to
    know which numbers rest on which.
    """
    by_product = W.group_sum(cost_rows, "product", "amount_usd")
    grand = sum(by_product.values())
    visible = by_product.get("chat", 0.0)
    return {
        "spend_by_product_usd": {k: W.round_money(v) for k, v in
                                 sorted(by_product.items(), key=lambda kv: -kv[1])},
        "content_visible_share_pct": W.safe_share(visible, grand),
        "content_invisible_share_pct": W.safe_share(grand - visible, grand),
        "reader_note": (
            f"Cost is complete: every product bills through the same place. Conversation "
            f"content is not — only the {W.safe_share(visible, grand):.0f}% of spend that "
            "is chat can be read. Where this report describes what people were doing, it "
            "is describing that share and says so."
        ),
    }


def _short_label(day: str) -> str:
    parsed = date.fromisoformat(day)
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{months[parsed.month - 1]} {parsed.day}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute the Waste Ledger's numbers.")
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
