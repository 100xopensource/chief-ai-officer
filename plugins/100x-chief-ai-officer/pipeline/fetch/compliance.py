#!/usr/bin/env python3
"""Pull conversation content from the Compliance API into the raw store, then parse.

    Compliance API ──▶ data/raw/compliance/<chat_id>.jsonl ──▶ derived tables

Two stages, deliberately separate. This module's only job is to get complete API
responses onto disk; `parse_raw.py` turns them into tables. Splitting them is
what makes a parsing bug cost a local re-parse instead of another full pull
against a shared, rate-limited API — and it is why the tool-call and attachment
blocks that an earlier flatten-on-the-fly version destroyed are now recoverable
by construction.

Consent
-------
This is the pull that acquires conversation content. It will not run without a
recorded consent decision for this lake (see `consent.py`). The cost and
adoption reports do not need it; only the Exposure Report does.

Rate limits
-----------
The Compliance API allows 600 requests/minute across the entire organization —
shared with anything else your org runs against it. This fetcher self-caps well
below that so a data pull can never be the reason someone else's job starts
failing. It also checkpoints as it goes, so an interrupted run resumes instead
of restarting.

Incremental behaviour
---------------------
Chats are selected by `updated_at`, so resuming an old conversation re-pulls it.
Its rows stay in their original creation week. Past weeks are therefore not
frozen, which matters to any week-over-week comparison — state whether you are
counting chats by creation or by activity.

Usage
-----
    python3 -m pipeline.fetch.compliance                       # incremental / first-run backfill
    python3 -m pipeline.fetch.compliance --since 2026-04-01    # bound the first backfill
    python3 -m pipeline.fetch.compliance --emails a@example.com
    python3 -m pipeline.fetch.compliance --skip-parse          # pull only, parse later
    python3 -m pipeline.fetch.compliance --parse-only          # re-parse, zero API calls
"""

from __future__ import annotations

# Runnable two ways: `python3 -m pipeline.fetch.compliance` and `python3 <path>/compliance.py`.
# The second has no package context, so put the plugin root on the path first.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))


import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.lake import datalake as lake
from pipeline.lake import raw as rawstore
from pipeline.fetch import compliance_api as cc
from pipeline.fetch import parse_raw
from pipeline.fetch.consent import ConsentDeclined, ensure_consent

JsonObject = dict[str, Any]

CHATS_URL = f"{cc.API_BASE}/v1/compliance/apps/chats"
DATASET = "dim_chat"  # the dataset whose watermark drives the incremental pull

# Well under the org-wide 600/min ceiling. A reporting tool has no business
# consuming the whole budget of an API other systems depend on.
DEFAULT_MAX_RPM = 240


def log(message: str) -> None:
    print(f"[compliance] {message}", file=sys.stderr)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def shift_back(timestamp: str, minutes: int) -> str:
    """Re-scan a short window before the watermark.

    Chats updated in the seconds around the last run's cutoff would otherwise
    fall in the gap between runs. Re-scanning is free of consequence because
    every write is an overwrite by chat id.
    """
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp
    return (moment - timedelta(minutes=minutes)).isoformat(timespec="seconds").replace("+00:00", "Z")


class RateLimiter:
    """Keep the request rate under a ceiling, measured over a sliding minute."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max(1, max_per_minute)
        self._times: list[float] = []

    def wait(self) -> None:
        now = time.monotonic()
        self._times = [t for t in self._times if now - t < 60.0]
        if len(self._times) >= self.max_per_minute:
            sleep_for = 60.0 - (now - self._times[0]) + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            self._times = [t for t in self._times if now - t < 60.0]
        self._times.append(now)


def select_users(users: list[JsonObject], emails: list[str]) -> list[JsonObject]:
    if not emails:
        return users
    wanted = {e.lower() for e in emails}
    return [u for u in users if cc.user_email(u).lower() in wanted]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Pull conversation content into the raw store, then parse it into tables.",
    )
    ap.add_argument("--data-dir", default="data", help="lake root (default: data)")
    ap.add_argument("--org-uuid", default=cc.DEFAULT_ORG_UUID,
                    help="restrict to one organization (default: every org the key can reach)")
    ap.add_argument("--emails", nargs="*", default=[], help="restrict to these users")
    ap.add_argument("--since", help="bound the first-run backfill (YYYY-MM-DD, on updated_at)")
    ap.add_argument("--overlap-minutes", type=int, default=30,
                    help="re-scan window before the watermark (default: 30)")
    ap.add_argument("--full-refresh", action="store_true", help="ignore the watermark and re-pull everything")
    ap.add_argument("--skip-messages", action="store_true",
                    help="index chats without bodies; leaves every content check unable to run")
    ap.add_argument("--skip-parse", action="store_true", help="pull to raw only; parse in a later run")
    ap.add_argument("--parse-only", action="store_true", help="re-parse the existing raw store; no API calls")
    ap.add_argument("--max-rpm", type=int, default=DEFAULT_MAX_RPM,
                    help=f"request ceiling per minute (default: {DEFAULT_MAX_RPM}; API allows 600 org-wide)")
    ap.add_argument("--yes", action="store_true",
                    help="use recorded consent non-interactively (needs CAIO_CONSENT_GRANTED_BY)")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.data_dir)
    started_at = now_iso()

    # --- parse-only: purely local, no consent prompt, no network -------------
    if args.parse_only:
        if rawstore.count_chats(root) == 0:
            log(f"no raw store at {rawstore.raw_dir(root)} — nothing to parse")
            return 2
        log(f"re-parsing {rawstore.count_chats(root)} chats from raw (no API calls)")
        print(json.dumps(parse_raw.parse_all(root), indent=2))
        return 0

    try:
        ensure_consent(root, assume_yes=args.yes)
    except ConsentDeclined as exc:
        log(str(exc))
        return 3

    api_key = cc.resolve_api_key() if hasattr(cc, "resolve_api_key") else _resolve_key()
    if not api_key:
        log("no API key. Set CAIO_API_KEY to a Compliance Access Key (sk-ant-api01-...).")
        return 4

    client = cc.make_client(api_key)
    limiter = RateLimiter(args.max_rpm)
    errors: list[JsonObject] = []

    state = lake.read_state(root)
    watermark = None if args.full_refresh else state.get("datasets", {}).get(DATASET, {}).get("updated_at_high")

    since_floor = f"{args.since}T00:00:00Z" if args.since else None
    floor = shift_back(watermark, args.overlap_minutes) if watermark else since_floor
    mode = "incremental" if watermark else ("bounded backfill" if since_floor else "full backfill")
    log(f"=== {mode} (updated_at.gte={floor}) · ceiling {args.max_rpm} req/min ===")

    limiter.wait()
    orgs = cc.resolve_organizations(client, errors, [], args.org_uuid)
    log(f"organizations: {len(orgs)}")

    pulled = 0
    skipped_seen = 0
    highest_updated = watermark or ""
    seen: set[str] = set()

    for org in orgs:
        org_uuid = str(org.get("uuid") or "")
        if not org_uuid:
            continue

        limiter.wait()
        users = select_users(cc.fetch_users_for_org(client, org_uuid, errors), args.emails)
        log(f"org {org.get('name') or org_uuid}: {len(users)} users")

        for user in users:
            uid = cc.user_id(user)
            if not uid:
                continue

            params: list[tuple[str, Any]] = [
                ("user_ids[]", uid),
                ("organization_ids[]", org_uuid),
                ("limit", cc.CHATS_LIMIT),
            ]
            if floor:
                params.append(("updated_at.gte", floor))

            limiter.wait()
            chats = cc.paginate_id_cursor(client, CHATS_URL, params, errors, f"chats:{uid}")

            for chat in chats:
                chat_id = cc.chat_id(chat)
                if not chat_id or chat_id in seen:
                    skipped_seen += 1
                    continue
                seen.add(chat_id)

                updated = str(chat.get("updated_at") or "")
                if updated > highest_updated:
                    highest_updated = updated

                messages: list[JsonObject] = []
                if not args.skip_messages:
                    limiter.wait()
                    payload = cc.fetch_messages_for_chat(client, chat, errors)
                    # Messages paginate under `chat_messages`, not the `data`
                    # key every other endpoint uses. Reading `data` here returns
                    # an empty list and looks exactly like an empty chat.
                    raw_messages = cc.response_items(payload, item_key="chat_messages") if payload else []
                    messages = [m for m in raw_messages if isinstance(m, dict)]

                # Persist before parsing. Always. This is the whole point.
                rawstore.write_chat(root, chat_id, chat, messages)
                pulled += 1

                if pulled % 100 == 0:
                    log(f"  {pulled} chats persisted")
                    _checkpoint(root, state, highest_updated, pulled)

    _checkpoint(root, state, highest_updated, pulled)
    log(f"pull complete: {pulled} chats persisted to {rawstore.raw_dir(root)}")

    parse_result: dict[str, Any] | None = None
    if args.skip_messages:
        log("--skip-messages: no bodies were pulled, so parsing is skipped. "
            "Every content-based check will be unavailable until a full pull runs.")
    elif args.skip_parse:
        log("--skip-parse: raw is on disk. Run with --parse-only to build the tables.")
    else:
        parse_result = parse_raw.parse_all(root)

    result = {
        "started_at": started_at,
        "finished_at": now_iso(),
        "data_dir": str(root),
        "mode": mode,
        "chats_pulled": pulled,
        "duplicates_skipped": skipped_seen,
        "raw_store": {
            "path": str(rawstore.raw_dir(root)),
            "chats": rawstore.count_chats(root),
            "bytes": rawstore.store_size_bytes(root),
        },
        "watermark_updated_at": highest_updated or None,
        "parse": parse_result,
        "errors": errors,
    }
    print(json.dumps(result, indent=2, default=str))
    return 0 if not errors else 5


def _checkpoint(root: Path, state: JsonObject, watermark: str, pulled: int) -> None:
    """Persist progress mid-run so an interruption resumes instead of restarting."""
    lake.set_dataset_state(
        state, DATASET,
        last_run=now_iso(),
        updated_at_high=watermark or None,
        rows_last_run=pulled,
        raw_chats=rawstore.count_chats(root),
    )
    lake.write_state(root, state)


def _resolve_key() -> str | None:
    import os
    for var in ("CAIO_API_KEY", "ANTHROPIC_COMPLIANCE_ACCESS_KEY", "ANTHROPIC_API_KEY"):
        value = os.environ.get(var)
        if value:
            return value.strip()
    return None


if __name__ == "__main__":
    sys.exit(main())
