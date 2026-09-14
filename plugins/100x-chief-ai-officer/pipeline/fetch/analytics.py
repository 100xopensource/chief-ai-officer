#!/usr/bin/env python3
"""Incrementally fetch Enterprise Claude cost/usage analytics into a local lake.

Targets the Claude Enterprise Analytics API (claude.ai org usage), NOT the
API-platform usage/cost endpoints:
  - analytics/cost_report        org cost over time (USD)        -> analytics_cost
  - analytics/user_cost_report   per-user cost                   -> analytics_user_cost
  - analytics/usage_report       org token usage over time       -> analytics_usage
  - analytics/user_usage_report  per-user token usage            -> analytics_user_usage
  - analytics/summaries          daily adoption / seats / DAU    -> analytics_summaries
  - analytics/users              per-user daily activity          -> analytics_users
  - analytics/skills             per-skill daily usage            -> analytics_skills
  - analytics/connectors         per-connector daily usage        -> analytics_connectors
  - analytics/apps/chat/projects per-project daily activity        -> analytics_chat_projects

Each dataset is stored as ISO-week-partitioned JSONL (see datalake.py); roll-up
CSV summaries are recomputed from the full lake into <data-dir>/_summary/.

The five cost/usage/summary reports are time-bucketed (range params); the four
activity reports (users, skills, connectors, projects) are date-scoped and
cursor-paginated, fetched day by day (3-day finalization lag). Skip the heavier
activity pulls with --skip-activity (or per-report --skip-users etc.).

API constraints honored here:
  - Requires an API key with the `read:analytics` scope; sent as the
    `x-api-key` header (use --auth-header bearer for an OAuth token).
  - Data starts 2026-01-01; no earlier range is accepted.
  - Time-bucketed reports span at most 31 days per request, so backfills are
    windowed into <=31-day chunks and paginated within each window.
  - `amount` / `list_amount` are fractional cents (amount / 100 == USD).
  - Responses carry `data_refreshed_at`; buckets past it are incomplete and data
    is not final for ~30 days. Summaries finalize with a 3-day lag.

Incremental: each ISO week is an immutable partition. The first run backfills
from --since (default 2026-01-01); later runs re-fetch a trailing window (default 7 days) from the
watermark — snapped back to the week's Monday so whole weeks overwrite cleanly —
plus everything new.

Examples:
  python3 -m pipeline.fetch.analytics                          # first run: backfill all; later: incremental
  python3 -m pipeline.fetch.analytics --since 2026-04-01       # bound the first-run backfill
  python3 -m pipeline.fetch.analytics --trailing-days 30       # match the ~30-day finalization lag
  python3 -m pipeline.fetch.analytics --products claude_code --products chat
  python3 -m pipeline.fetch.analytics --full-refresh
  python3 -m pipeline.fetch.analytics --auth-header bearer     # if using an OAuth token instead of an API key

Read it back with pandas:
  import pandas as pd, glob
  cost = pd.concat(pd.read_json(p, lines=True)
                   for p in glob.glob("data/analytics_cost/week=*/part.jsonl"))

Credentials (first match wins):
  CAIO_API_KEY, then ANTHROPIC_ADMIN_KEY, then ANTHROPIC_API_KEY.
"""

from __future__ import annotations

# Runnable two ways: `python3 -m pipeline.fetch.analytics` and `python3 <path>/analytics.py`.
# The second has no package context, so put the plugin root on the path first.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))


import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Union

from pipeline.fetch._requests import require_requests
from pipeline.lake import datalake as lake

requests = require_requests()

# Same override the compliance fetcher honours, so a proxy or gateway is set
# once and both network modules follow it.
API_BASE = os.environ.get("CAIO_API_BASE", "https://api.anthropic.com")
ANALYTICS = f"{API_BASE}/v1/organizations/analytics"
DEFAULT_EARLIEST = "2026-01-01"  # claude.ai analytics data starts here; --since overrides
DEFAULT_TRAILING_DAYS = 7  # data isn't final for ~30 days; raise for stricter accuracy
WINDOW_DAYS = 31  # max span per time-bucketed request
BUCKET_LIMIT = 31  # max 1d buckets per page
ROW_LIMIT = 1000  # max rows per page for per-user reports
ACTIVITY_LIMIT = 1000  # max rows per page for daily activity endpoints
DAILY_LAG_DAYS = 3  # summaries + daily activity finalize 3 days back
TIMEOUT = 60

JsonObject = dict[str, Any]
Params = list[tuple[str, Any]]


class AnalyticsClient:
    def __init__(self, api_key: str, auth_header: str) -> None:
        self.session = requests.Session()
        headers = {"anthropic-version": "2023-06-01", "accept": "application/json"}
        if auth_header == "x-api-key":
            headers["x-api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
        self.session.headers.update(headers)

    def get(self, url: str, params: Params) -> requests.Response:
        return self.session.get(url, params=[(k, v) for k, v in params if v is not None], timeout=TIMEOUT)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"[claude_cost] {message}", file=sys.stderr)


def resolve_start(state: JsonObject, dataset: str, args: argparse.Namespace, today: "Any") -> tuple[Any, Any]:
    """First day to fetch, and the earliest-allowed anchor, for `dataset`.

    The earliest day is --since (default DEFAULT_EARLIEST), used for both the
    first-run backfill and --full-refresh — so the start date is a flag, not a
    hardcoded constant. Returns (start, anchor); callers snap/clamp as needed.
    """
    earliest = args.since or DEFAULT_EARLIEST
    anchor = lake.parse_day(earliest)
    if args.full_refresh:
        start = anchor
    else:
        start = lake.incremental_start(state, dataset, args.trailing_days, today, earliest=earliest, lookback_days=args.lookback_days)
    start = max(lake.week_start(max(start, anchor)), anchor)  # snap to Monday so weeks overwrite cleanly
    return start, anchor


def cents_to_usd(value: Any) -> float | None:
    try:
        return round(float(value) / 100.0, 6)
    except (TypeError, ValueError):
        return None


def write_csv(path: Path, fieldnames: list[str], rows: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def params_with_page(params: Params, page: str) -> Params:
    return [(k, v) for k, v in params if k != "page"] + [("page", page)]


def get_json(client: AnalyticsClient, url: str, params: Params, errors: list[JsonObject], stage: str) -> JsonObject | None:
    try:
        response = client.get(url, params)
    except requests.exceptions.RequestException as exc:
        errors.append({"stage": stage, "type": "request_exception", "detail": str(exc), "url": url})
        log(f"ERROR {stage}: request exception: {exc}")
        return None

    if response.status_code != 200:
        detail = response.text[:2000]
        errors.append({"stage": stage, "type": "http_error", "status_code": response.status_code, "detail": detail, "url": url})
        log(f"ERROR {stage}: HTTP {response.status_code}: {detail}")
        return None

    try:
        payload = response.json()
    except ValueError as exc:
        errors.append({"stage": stage, "type": "json_decode_error", "detail": str(exc), "url": url})
        log(f"ERROR {stage}: JSON decode error: {exc}")
        return None

    return payload if isinstance(payload, dict) else None


def windows(start_day, end_excl, span: int = WINDOW_DAYS) -> list[tuple[str, str]]:
    """Split [start_day, end_excl) into <=span-day (start, end) ISO-date pairs."""
    out: list[tuple[str, str]] = []
    cursor = start_day
    while cursor < end_excl:
        chunk_end = min(cursor + timedelta(days=span), end_excl)
        out.append((lake.day_str(cursor), lake.day_str(chunk_end)))
        cursor = chunk_end
    return out


def fetch_windowed(
    client: AnalyticsClient,
    url: str,
    start_day,
    end_excl,
    base_params: Params,
    limit: int,
    errors: list[JsonObject],
    stage: str,
) -> tuple[list[Any], str | None]:
    """Fetch a time-bucketed report across 31-day windows, paginating each."""
    collected: list[Any] = []
    refreshed: str | None = None
    win = windows(start_day, end_excl)
    log(f"{stage}: fetching {win[0][0]}..{win[-1][1]} in {len(win)} window(s)")
    for index, (ws, we) in enumerate(win, 1):
        window_params: Params = [
            ("starting_at", f"{ws}T00:00:00Z"),
            ("ending_at", f"{we}T00:00:00Z"),
            ("bucket_width", "1d"),
            ("limit", limit),
            *base_params,
        ]
        page: str | None = None
        guard = 0
        window_rows = 0
        while True:
            guard += 1
            params = window_params if page is None else params_with_page(window_params, page)
            payload = get_json(client, url, params, errors, stage)
            if payload is None:
                break
            data = payload.get("data")
            if isinstance(data, list):
                collected.extend(data)
                window_rows += len(data)
            refreshed = payload.get("data_refreshed_at") or refreshed
            log(f"{stage}: window {index}/{len(win)} {ws}..{we} page {guard} -> {len(data) if isinstance(data, list) else 0} rows (window {window_rows}, total {len(collected)})")
            if not payload.get("has_more") or not payload.get("next_page") or guard > 500:
                break
            page = payload["next_page"]
    log(f"{stage}: collected {len(collected)} rows; data_refreshed_at={refreshed}")
    return collected, refreshed


# --- flatteners --------------------------------------------------------------

def flatten_org_cost(data: list[Any]) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for bucket in data:
        if not isinstance(bucket, dict):
            continue
        starting_at, ending_at = bucket.get("starting_at"), bucket.get("ending_at")
        for r in bucket.get("results", []) or []:
            rows.append(
                {
                    "day": (starting_at or "")[:10],
                    "starting_at": starting_at,
                    "ending_at": ending_at,
                    "amount_cents": r.get("amount"),
                    "amount_usd": cents_to_usd(r.get("amount")),
                    "list_amount_cents": r.get("list_amount"),
                    "list_amount_usd": cents_to_usd(r.get("list_amount")),
                    "currency": r.get("currency"),
                    "product": r.get("product"),
                    "model": r.get("model"),
                    "cost_type": r.get("cost_type"),
                    "token_type": r.get("token_type"),
                    "context_window": r.get("context_window"),
                    "inference_geo": r.get("inference_geo"),
                    "speed": r.get("speed"),
                    "requests": r.get("requests"),
                }
            )
    return rows


def flatten_org_usage(data: list[Any]) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for bucket in data:
        if not isinstance(bucket, dict):
            continue
        starting_at, ending_at = bucket.get("starting_at"), bucket.get("ending_at")
        for r in bucket.get("results", []) or []:
            cache = r.get("cache_creation") or {}
            tools = r.get("server_tool_use") or {}
            rows.append(
                {
                    "day": (starting_at or "")[:10],
                    "starting_at": starting_at,
                    "ending_at": ending_at,
                    "product": r.get("product"),
                    "model": r.get("model"),
                    "context_window": r.get("context_window"),
                    "inference_geo": r.get("inference_geo"),
                    "speed": r.get("speed"),
                    "uncached_input_tokens": r.get("uncached_input_tokens") or 0,
                    "cache_read_input_tokens": r.get("cache_read_input_tokens") or 0,
                    "cache_creation_1h_input_tokens": cache.get("ephemeral_1h_input_tokens") or 0,
                    "cache_creation_5m_input_tokens": cache.get("ephemeral_5m_input_tokens") or 0,
                    "output_tokens": r.get("output_tokens") or 0,
                    "web_search_requests": tools.get("web_search_requests") or 0,
                    "requests": r.get("requests") or 0,
                }
            )
    return rows


def _actor(row: JsonObject) -> JsonObject:
    actor = row.get("actor") or {}
    return {
        "user_id": actor.get("user_id"),
        "email": actor.get("email"),
        "name": actor.get("name"),
        "deleted": actor.get("deleted"),
    }


def flatten_user_cost(data: list[Any]) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        starting_at = r.get("starting_at")
        rows.append(
            {
                "day": (starting_at or "")[:10],
                "starting_at": starting_at,
                "ending_at": r.get("ending_at"),
                **_actor(r),
                "amount_cents": r.get("amount"),
                "amount_usd": cents_to_usd(r.get("amount")),
                "list_amount_cents": r.get("list_amount"),
                "list_amount_usd": cents_to_usd(r.get("list_amount")),
                "currency": r.get("currency"),
                "product": r.get("product"),
                "model": r.get("model"),
                "cost_type": r.get("cost_type"),
                "token_type": r.get("token_type"),
                "requests": r.get("requests"),
            }
        )
    return rows


def flatten_user_usage(data: list[Any]) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        starting_at = r.get("starting_at")
        cache = r.get("cache_creation") or {}
        tools = r.get("server_tool_use") or {}
        rows.append(
            {
                "day": (starting_at or "")[:10],
                "starting_at": starting_at,
                "ending_at": r.get("ending_at"),
                **_actor(r),
                "total_tokens": r.get("total_tokens") or 0,
                "uncached_input_tokens": r.get("uncached_input_tokens") or 0,
                "cache_read_input_tokens": r.get("cache_read_input_tokens") or 0,
                "cache_creation_1h_input_tokens": cache.get("ephemeral_1h_input_tokens") or 0,
                "cache_creation_5m_input_tokens": cache.get("ephemeral_5m_input_tokens") or 0,
                "output_tokens": r.get("output_tokens") or 0,
                "web_search_requests": tools.get("web_search_requests") or 0,
                "product": r.get("product"),
                "model": r.get("model"),
                "requests": r.get("requests"),
            }
        )
    return rows


def flatten_summary(summary: JsonObject) -> JsonObject:
    starting_at = summary.get("starting_at")
    row = {"day": (starting_at or "")[:10]}
    row.update(summary)
    return row


# --- daily activity flatteners (the date-scoped, cursor-paginated endpoints) --
# These endpoints return one `data` array per `date` with no timestamp in the
# rows, so the caller passes the requested `day`. Headline scalar metrics are
# lifted to top-level columns; the full nested metric objects are preserved so
# nothing is lost (pandas reads them as dicts; json_normalize for detail).

OFFICE_PRODUCTS = ("excel", "outlook", "powerpoint", "word")


def _office_sum(office: JsonObject, field: str) -> int:
    return sum(int((office.get(p) or {}).get(field) or 0) for p in OFFICE_PRODUCTS)


def flatten_user_activity(r: JsonObject, day: str) -> JsonObject:
    user = r.get("user") or {}
    chat = r.get("chat_metrics") or {}
    cc = r.get("claude_code_metrics") or {}
    core = cc.get("core_metrics") or {}
    loc = core.get("lines_of_code") or {}
    cw = r.get("cowork_metrics") or {}
    design = r.get("design_metrics") or {}
    office = r.get("office_metrics") or {}
    return {
        "day": day,
        "user_id": user.get("id"),
        "email": user.get("email_address"),
        "web_search_count": r.get("web_search_count") or 0,
        "chat_message_count": chat.get("message_count") or 0,
        "chat_distinct_conversation_count": chat.get("distinct_conversation_count"),
        "chat_thinking_message_count": chat.get("thinking_message_count") or 0,
        "cc_lines_added": loc.get("added_count") or 0,
        "cc_lines_removed": loc.get("removed_count") or 0,
        "cc_commit_count": core.get("commit_count") or 0,
        "cc_pull_request_count": core.get("pull_request_count") or 0,
        "cc_distinct_session_count": core.get("distinct_session_count"),
        "cowork_message_count": cw.get("message_count") or 0,
        "cowork_action_count": cw.get("action_count") or 0,
        "cowork_dispatch_turn_count": cw.get("dispatch_turn_count") or 0,
        "cowork_distinct_session_count": cw.get("distinct_session_count"),
        "design_message_count": design.get("message_count") or 0,
        "office_message_count": _office_sum(office, "message_count"),
        # full nested blobs preserved for deep analysis
        "chat_metrics": chat,
        "claude_code_metrics": cc,
        "cowork_metrics": cw,
        "design_metrics": design,
        "office_metrics": office,
    }


def flatten_skill(r: JsonObject, day: str) -> JsonObject:
    return {
        "day": day,
        "skill_name": r.get("skill_name"),
        "distinct_user_count": r.get("distinct_user_count") or 0,
        "chat_distinct_conversation_used": (r.get("chat_metrics") or {}).get("distinct_conversation_skill_used_count"),
        "cc_distinct_session_used": (r.get("claude_code_metrics") or {}).get("distinct_session_skill_used_count"),
        "cowork_distinct_session_used": (r.get("cowork_metrics") or {}).get("distinct_session_skill_used_count"),
        "office_metrics": r.get("office_metrics") or {},
    }


def flatten_connector(r: JsonObject, day: str) -> JsonObject:
    return {
        "day": day,
        "connector_name": r.get("connector_name"),
        "distinct_user_count": r.get("distinct_user_count") or 0,
        "chat_distinct_conversation_used": (r.get("chat_metrics") or {}).get("distinct_conversation_connector_used_count"),
        "cc_distinct_session_used": (r.get("claude_code_metrics") or {}).get("distinct_session_connector_used_count"),
        "cowork_distinct_session_used": (r.get("cowork_metrics") or {}).get("distinct_session_connector_used_count"),
        "office_metrics": r.get("office_metrics") or {},
    }


def flatten_chat_project(r: JsonObject, day: str) -> JsonObject:
    created_by = r.get("created_by") or {}
    return {
        "day": day,
        "project_id": r.get("project_id"),
        "project_name": r.get("project_name"),
        "distinct_conversation_count": r.get("distinct_conversation_count") or 0,
        "distinct_user_count": r.get("distinct_user_count") or 0,
        "message_count": r.get("message_count") or 0,
        "created_at": r.get("created_at"),
        "created_by_id": created_by.get("id"),
        "created_by_email": created_by.get("email_address"),
    }


def fetch_daily(
    client: AnalyticsClient,
    root: Path,
    dataset: str,
    path: str,
    flatten_one: Callable[[JsonObject, str], JsonObject],
    state: JsonObject,
    args: argparse.Namespace,
    errors: list[JsonObject],
) -> JsonObject:
    """Fetch a date-scoped, cursor-paginated activity report day by day."""
    today = lake.today_utc()
    latest = today - timedelta(days=DAILY_LAG_DAYS)  # 3-day finalization lag
    start, anchor = resolve_start(state, dataset, args, today)

    mode = "full refresh" if args.full_refresh else ("incremental" if lake.get_watermark(state, dataset) else "first-run backfill")
    log(f"=== {dataset}: {mode} ===")

    days: list[str] = lake.day_range(start, latest)
    if not days:
        log(f"{dataset}: nothing to fetch (start {lake.day_str(start)} > latest {lake.day_str(latest)})")
        return {"rows_last_run": 0, "days_written": 0, "watermark_day": lake.get_watermark(state, dataset)}

    url = f"{ANALYTICS}{path}"
    rows: list[JsonObject] = []
    log(f"{dataset}: fetching {days[0]}..{days[-1]} ({len(days)} day(s), cursor-paginated)")
    for index, day in enumerate(days, 1):
        page: str | None = None
        guard = 0
        while True:
            guard += 1
            params: Params = [("date", day), ("limit", ACTIVITY_LIMIT)]
            if page:
                params = params_with_page(params, page)
            payload = get_json(client, url, params, errors, dataset)
            if payload is None:
                break
            for item in payload.get("data", []) or []:
                if isinstance(item, dict):
                    rows.append(flatten_one(item, day))
            page = payload.get("next_page")
            if not page or guard > 2000:
                break
        if index % 10 == 0 or index == len(days):
            log(f"{dataset}: {index}/{len(days)} days -> {len(rows)} rows so far")

    written = lake.write_rows(root, dataset, rows)
    watermark = lake.advance_watermark(state, dataset, lake.day_str(latest))
    lake.set_dataset_state(
        state, dataset,
        last_run=now_iso(), last_fetch_start=lake.day_str(start),
        rows_last_run=len(rows), partitions_total=len(lake.list_partitions(root, dataset)),
    )
    log(f"{dataset}: wrote {len(rows)} rows across {len(written)} week partition(s); watermark={watermark}")
    return {"rows_last_run": len(rows), "days_written": len(written), "watermark_day": watermark}


# --- summaries ---------------------------------------------------------------

SUMMARY_NUMERIC = [
    "assigned_seat_count", "pending_invite_count",
    "daily_active_user_count", "weekly_active_user_count", "monthly_active_user_count",
    "daily_adoption_rate", "weekly_adoption_rate", "monthly_adoption_rate",
    "cowork_daily_active_user_count", "cowork_weekly_active_user_count", "cowork_monthly_active_user_count",
]


def fetch_summaries(client: AnalyticsClient, root: Path, state: JsonObject, args: argparse.Namespace, errors: list[JsonObject]) -> JsonObject:
    dataset = "analytics_summaries"
    today = lake.today_utc()
    latest = today - timedelta(days=DAILY_LAG_DAYS)  # summaries finalize 3 days back
    start, anchor = resolve_start(state, dataset, args, today)
    if start > latest:
        start = latest

    mode = "full refresh" if args.full_refresh else ("incremental" if lake.get_watermark(state, dataset) else "first-run backfill")
    log(f"=== {dataset}: {mode} ===")
    # Summaries take YYYY-MM-DD dates and span up to 366 days; window to be safe.
    rows: list[JsonObject] = []
    win = windows(start, latest + timedelta(days=1), span=366)
    for index, (ws, we) in enumerate(win, 1):
        params: Params = [("starting_date", ws), ("ending_date", we)]
        payload = get_json(client, f"{ANALYTICS}/summaries", params, errors, dataset)
        if payload:
            new = [flatten_summary(s) for s in payload.get("summaries", []) if isinstance(s, dict)]
            rows.extend(new)
            log(f"{dataset}: window {index}/{len(win)} {ws}..{we} -> {len(new)} day rows (total {len(rows)})")

    written = lake.write_rows(root, dataset, rows)
    watermark = lake.advance_watermark(state, dataset, lake.day_str(latest))
    lake.set_dataset_state(state, dataset, last_run=now_iso(), rows_last_run=len(rows), partitions_total=len(lake.list_partitions(root, dataset)))
    log(f"{dataset}: {len(rows)} day rows; watermark={watermark}")
    return {"rows_last_run": len(rows), "days_written": len(written), "watermark_day": watermark}


def fetch_report(
    client: AnalyticsClient,
    root: Path,
    dataset: str,
    path: str,
    base_params: Params,
    limit: int,
    flatten: Callable[[list[Any]], list[JsonObject]],
    state: JsonObject,
    args: argparse.Namespace,
    errors: list[JsonObject],
) -> JsonObject:
    today = lake.today_utc()
    start, anchor = resolve_start(state, dataset, args, today)
    end_excl = today + timedelta(days=1)
    if start >= end_excl:
        start = today

    mode = "full refresh" if args.full_refresh else ("incremental" if lake.get_watermark(state, dataset) else "first-run backfill")
    log(f"=== {dataset}: {mode} ===")
    data, refreshed = fetch_windowed(client, f"{ANALYTICS}{path}", start, end_excl, base_params, limit, errors, dataset)
    rows = flatten(data)
    written = lake.write_rows(root, dataset, rows)
    log(f"{dataset}: wrote {len(rows)} rows across {len(written)} week partition(s)")

    # Watermark: last complete day = min(yesterday, data_refreshed_at day).
    complete = today - timedelta(days=1)
    if refreshed:
        try:
            refreshed_day = lake.parse_day(refreshed[:10])
            complete = min(complete, refreshed_day)
        except ValueError:
            pass
    watermark = lake.advance_watermark(state, dataset, lake.day_str(complete))
    lake.set_dataset_state(
        state, dataset,
        last_run=now_iso(), last_fetch_start=lake.day_str(start),
        data_refreshed_at=refreshed, rows_last_run=len(rows),
        partitions_total=len(lake.list_partitions(root, dataset)),
    )
    log(f"{dataset}: {len(rows)} rows from {lake.day_str(start)}; watermark={watermark}")
    return {"rows_last_run": len(rows), "days_written": len(written), "watermark_day": watermark, "data_refreshed_at": refreshed}


# --- summaries CSVs ----------------------------------------------------------

def summarize_cost(rows: list[JsonObject], dimension: str) -> list[JsonObject]:
    totals: dict[Any, dict[str, float]] = defaultdict(lambda: {"amount_usd": 0.0, "list_amount_usd": 0.0})
    for row in rows:
        bucket = totals[row.get(dimension)]
        bucket["amount_usd"] += row.get("amount_usd") or 0.0
        bucket["list_amount_usd"] += row.get("list_amount_usd") or 0.0
    out = [{dimension: k, "amount_usd": round(v["amount_usd"], 6), "list_amount_usd": round(v["list_amount_usd"], 6)} for k, v in totals.items()]
    out.sort(key=lambda item: item["amount_usd"], reverse=True)
    return out


USAGE_FIELDS = [
    "uncached_input_tokens", "cache_read_input_tokens",
    "cache_creation_1h_input_tokens", "cache_creation_5m_input_tokens",
    "output_tokens", "web_search_requests",
]


def summarize_usage(rows: list[JsonObject], dimension: str) -> list[JsonObject]:
    totals: dict[Any, dict[str, int]] = defaultdict(lambda: {f: 0 for f in USAGE_FIELDS})
    for row in rows:
        bucket = totals[row.get(dimension)]
        for field in USAGE_FIELDS:
            bucket[field] += int(row.get(field) or 0)
    out = [{dimension: k, **v} for k, v in totals.items()]
    out.sort(key=lambda item: item["output_tokens"], reverse=True)
    return out


def summarize_sum(rows: list[JsonObject], dimension: str, fields: list[str], label_fields: dict[str, str] | None = None) -> list[JsonObject]:
    totals: dict[Any, dict[str, int]] = defaultdict(lambda: {f: 0 for f in fields})
    labels: dict[Any, dict[str, Any]] = {}
    for row in rows:
        key = row.get(dimension)
        bucket = totals[key]
        for field in fields:
            bucket[field] += int(row.get(field) or 0)
        if label_fields and key not in labels:
            labels[key] = {out: row.get(src) for out, src in label_fields.items()}
    out = [{dimension: k, **(labels.get(k, {})), **v} for k, v in totals.items()]
    out.sort(key=lambda item: item.get(fields[0], 0), reverse=True)
    return out


def write_summaries(root: Path, args: argparse.Namespace, run: JsonObject) -> None:
    sd = root / "_summary"
    log("=== writing roll-up CSVs to _summary/ ===")

    if not args.skip_cost:
        cost = list(lake.iter_dataset(root, "analytics_cost"))
        total = round(sum(r["amount_usd"] for r in cost if r.get("amount_usd")), 6)
        write_csv(sd / "cost_by_day.csv", ["day", "amount_usd", "list_amount_usd"], summarize_cost(cost, "day"))
        write_csv(sd / "cost_by_model.csv", ["model", "amount_usd", "list_amount_usd"], summarize_cost(cost, "model"))
        write_csv(sd / "cost_by_product.csv", ["product", "amount_usd", "list_amount_usd"], summarize_cost(cost, "product"))
        run["lake_total_cost_usd"] = total
        log(f"lake total cost: ${total:.2f} across {len(cost)} cost rows")

        user_cost = list(lake.iter_dataset(root, "analytics_user_cost"))
        write_csv(sd / "cost_by_user.csv", ["email", "amount_usd", "list_amount_usd"], summarize_cost(user_cost, "email"))

    if not args.skip_usage:
        usage = list(lake.iter_dataset(root, "analytics_usage"))
        write_csv(sd / "usage_by_model.csv", ["model", *USAGE_FIELDS], summarize_usage(usage, "model"))
        write_csv(sd / "usage_by_product.csv", ["product", *USAGE_FIELDS], summarize_usage(usage, "product"))
        write_csv(sd / "usage_by_day.csv", ["day", *USAGE_FIELDS], summarize_usage(usage, "day"))
        user_usage = list(lake.iter_dataset(root, "analytics_user_usage"))
        write_csv(sd / "usage_by_user.csv", ["email", *USAGE_FIELDS], summarize_usage(user_usage, "email"))

    if not args.skip_summaries:
        summaries = list(lake.iter_dataset(root, "analytics_summaries"))
        summaries.sort(key=lambda r: r.get("day") or "")
        write_csv(sd / "adoption_by_day.csv", ["day", *SUMMARY_NUMERIC], summaries)

    if not (args.skip_activity or args.skip_users):
        users = list(lake.iter_dataset(root, "analytics_users"))
        write_csv(
            sd / "activity_by_user.csv",
            ["email", "chat_message_count", "cowork_message_count", "cc_lines_added", "cc_lines_removed", "cc_commit_count", "cc_pull_request_count", "web_search_count"],
            summarize_sum(users, "email", ["chat_message_count", "cowork_message_count", "cc_lines_added", "cc_lines_removed", "cc_commit_count", "cc_pull_request_count", "web_search_count"]),
        )
    if not (args.skip_activity or args.skip_skills):
        skills = list(lake.iter_dataset(root, "analytics_skills"))
        write_csv(sd / "skills_by_name.csv", ["skill_name", "distinct_user_count"], summarize_sum(skills, "skill_name", ["distinct_user_count"]))
    if not (args.skip_activity or args.skip_connectors):
        connectors = list(lake.iter_dataset(root, "analytics_connectors"))
        write_csv(sd / "connectors_by_name.csv", ["connector_name", "distinct_user_count"], summarize_sum(connectors, "connector_name", ["distinct_user_count"]))
    if not (args.skip_activity or args.skip_projects):
        projects = list(lake.iter_dataset(root, "analytics_chat_projects"))
        write_csv(
            sd / "projects_by_name.csv",
            ["project_name", "project_id", "message_count", "distinct_conversation_count", "distinct_user_count"],
            summarize_sum(projects, "project_name", ["message_count", "distinct_conversation_count", "distinct_user_count"], {"project_id": "project_id"}),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incrementally fetch Enterprise Claude cost/usage analytics into a lake.")
    parser.add_argument("--data-dir", default=lake.DEFAULT_DATA_DIR, help="Lake root directory (default: data).")
    parser.add_argument("--trailing-days", type=int, default=DEFAULT_TRAILING_DAYS, help="Days to re-fetch back from the watermark (data finalizes ~30d).")
    parser.add_argument("--since", help=f"Earliest day (YYYY-MM-DD) to backfill on first run / --full-refresh. Default: {DEFAULT_EARLIEST} (when claude.ai analytics data begins).")
    parser.add_argument("--lookback-days", type=int, help="Bound the first run / --full-refresh to N days before today.")
    parser.add_argument("--full-refresh", action="store_true", help="Ignore the watermark and re-fetch from --since (or all).")
    parser.add_argument("--auth-header", choices=("x-api-key", "bearer"), default="x-api-key", help="How to send the API key (default: x-api-key; use bearer for an OAuth token).")
    parser.add_argument("--cost-group-by", action="append", choices=("product", "model", "context_window", "inference_geo", "speed", "cost_type", "token_type"), help="Org cost report group_by. Default: product + model.")
    parser.add_argument("--usage-group-by", action="append", choices=("product", "model", "context_window", "inference_geo", "speed"), help="Org usage report group_by. Default: product + model.")
    parser.add_argument("--products", action="append", help="Filter to product surface(s): chat, claude_code, cowork, office_agent, claude_in_chrome, claude_design.")
    parser.add_argument("--models", action="append", help="Filter to model(s).")
    parser.add_argument("--user-ids", action="append", help="Filter to user(s) by tagged user ID.")
    parser.add_argument("--skip-cost", action="store_true", help="Skip the cost + user_cost reports.")
    parser.add_argument("--skip-usage", action="store_true", help="Skip the usage + user_usage reports.")
    parser.add_argument("--skip-summaries", action="store_true", help="Skip the daily adoption summaries.")
    parser.add_argument("--skip-activity", action="store_true", help="Skip ALL daily activity reports (users, skills, connectors, projects).")
    parser.add_argument("--skip-users", action="store_true", help="Skip the per-user daily activity report.")
    parser.add_argument("--skip-skills", action="store_true", help="Skip the per-skill daily usage report.")
    parser.add_argument("--skip-connectors", action="store_true", help="Skip the per-connector daily usage report.")
    parser.add_argument("--skip-projects", action="store_true", help="Skip the per-chat-project daily activity report.")

    args = parser.parse_args()
    if not args.cost_group_by:
        args.cost_group_by = ["product", "model"]
    if not args.usage_group_by:
        args.usage_group_by = ["product", "model"]
    if args.trailing_days < 0:
        parser.error("--trailing-days must be >= 0.")
    if args.lookback_days is not None and args.lookback_days < 0:
        parser.error("--lookback-days must be >= 0.")
    if args.since:
        try:
            lake.parse_day(args.since)
        except ValueError:
            parser.error("--since must be YYYY-MM-DD.")
    return args


def common_filters(args: argparse.Namespace) -> Params:
    return (
        [("products[]", p) for p in args.products or []]
        + [("models[]", m) for m in args.models or []]
        + [("user_ids[]", u) for u in args.user_ids or []]
    )


def main() -> int:
    args = parse_args()
    errors: list[JsonObject] = []

    api_key = os.environ.get("CAIO_API_KEY") or os.environ.get("ANTHROPIC_ADMIN_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    root = Path(args.data_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)

    if not api_key:
        errors.append({"stage": "env", "type": "missing_env", "detail": "CAIO_API_KEY, ANTHROPIC_ADMIN_KEY, or ANTHROPIC_API_KEY not set"})
        log("ERROR: missing credentials")
        print(json.dumps({"errors": errors}, indent=2))
        return 1

    client = AnalyticsClient(api_key, args.auth_header)
    state = lake.read_state(root)
    run: JsonObject = {"started_at": now_iso(), "data_dir": str(root), "datasets": {}, "errors": errors}
    filters = common_filters(args)

    if not args.skip_cost:
        run["datasets"]["analytics_cost"] = fetch_report(
            client, root, "analytics_cost", "/cost_report",
            [("group_by[]", g) for g in args.cost_group_by] + filters,
            BUCKET_LIMIT, flatten_org_cost, state, args, errors,
        )
        run["datasets"]["analytics_user_cost"] = fetch_report(
            client, root, "analytics_user_cost", "/user_cost_report",
            filters, ROW_LIMIT, flatten_user_cost, state, args, errors,
        )

    if not args.skip_usage:
        run["datasets"]["analytics_usage"] = fetch_report(
            client, root, "analytics_usage", "/usage_report",
            [("group_by[]", g) for g in args.usage_group_by] + filters,
            BUCKET_LIMIT, flatten_org_usage, state, args, errors,
        )
        run["datasets"]["analytics_user_usage"] = fetch_report(
            client, root, "analytics_user_usage", "/user_usage_report",
            filters, ROW_LIMIT, flatten_user_usage, state, args, errors,
        )

    if not args.skip_summaries:
        run["datasets"]["analytics_summaries"] = fetch_summaries(client, root, state, args, errors)

    activity = [
        ("analytics_users", "/users", flatten_user_activity, args.skip_users),
        ("analytics_skills", "/skills", flatten_skill, args.skip_skills),
        ("analytics_connectors", "/connectors", flatten_connector, args.skip_connectors),
        ("analytics_chat_projects", "/apps/chat/projects", flatten_chat_project, args.skip_projects),
    ]
    for dataset, path, flatten_one, skip in activity:
        if args.skip_activity or skip:
            continue
        run["datasets"][dataset] = fetch_daily(client, root, dataset, path, flatten_one, state, args, errors)

    lake.write_state(root, state)
    write_summaries(root, args, run)

    run["finished_at"] = now_iso()
    print(json.dumps(run, indent=2, default=str))
    return 0 if not errors else 5


if __name__ == "__main__":
    sys.exit(main())
