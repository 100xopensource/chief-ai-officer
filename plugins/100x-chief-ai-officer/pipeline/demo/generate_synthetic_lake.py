#!/usr/bin/env python3
"""Build a complete synthetic lake for a fictional company. No credentials needed.

Someone evaluating this tool should be able to see all three reports before
asking their admin for an API key — and a contributor should be able to run the
whole pipeline in CI without an Enterprise tenant. This module generates a lake
that is structurally identical to a real one: same datasets, same grains, same
column names, same quirks.

The company: **Northwind Analytics**, 112 seats across eight departments. Every person,
domain, and system in it is invented. Emails are `@example.com`, which the
scrubber recognises as synthetic, so anything generated here is safe to commit
and safe to publish.

Deliberately reproduced quirks
------------------------------
A demo lake that is too clean teaches the wrong lessons — every trap below cost
a real correction, and the reports are built to survive them. So the generator
plants them on purpose:

  · **Husk chats** — titled, zero messages. They inflate any count taken from
    titles rather than content.
  · **A connector born mid-window** — `northwind_bi` first appears partway
    through the history. Trend it naively and it shows an absurd growth rate;
    the correct reading checks a dimension's first appearance before comparing
    across it.
  · **Truncated tool payloads** — some tool results sit at the source cap, so
    anything past the cut is invisible and every derived count is a floor.
  · **Luhn-valid numbers that are not cards** — order and SKU identifiers in
    point-of-sale output. A naive card detector scores 0% precision on them.
  · **Placeholder credentials** — redacted and obviously-fake secret values. A
    secret scanner must not report these as live, and the scrubber must not
    treat the generator that plants them as a leak.
  · **One genuinely notable pattern** — a connector emitting signed session
    tokens into tool output, which is the shape of finding this tool exists to
    surface. It is synthetic, but it behaves like the real thing under a scan.
  · **Idle and never-used seats** — so the seat-reclaim arithmetic has something
    to find.
  · **A dormant tail and a concentrated top spender** — so cost findings and
    adoption segments both have real structure to describe.

Determinism
-----------
Seeded, so the same seed produces a byte-identical lake. CI can therefore assert
on exact numbers, and a bug reproduces on someone else's machine from the seed
alone.

Usage
-----
    python3 generate_synthetic_lake.py --out data-demo
    python3 generate_synthetic_lake.py --out data-demo --seed 20260819 --weeks 14
"""

from __future__ import annotations

# Runnable two ways: `python3 -m pipeline.demo.generate_synthetic_lake` and `python3 <path>/generate_synthetic_lake.py`.
# The second has no package context, so put the plugin root on the path first.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))


import argparse
import json
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.lake import datalake as lake
from pipeline.lake import raw as rawstore

JsonObject = dict[str, Any]

ORG_NAME = "Northwind Analytics"
ORG_UUID = "00000000-0000-4000-8000-000000000001"
EMAIL_DOMAIN = "example.com"

# 112 seats, the size at which a rollout is big enough to have real structure
# and small enough that a reader can hold the whole org in their head.
DEPARTMENTS = [
    ("Commercial", 24),
    ("Operations", 22),
    ("Finance", 16),
    ("Platform", 12),
    ("People", 10),
    ("Executive", 8),
    ("Legal", 6),
    ("Unassigned", 14),   # seats with no group: the reclaim candidates
]

FIRST_NAMES = [
    "Avery", "Blake", "Casey", "Devon", "Ellis", "Finley", "Gray", "Harper",
    "Indigo", "Jordan", "Kai", "Lane", "Marlow", "Noor", "Oakley", "Parker",
    "Quinn", "Reese", "Sage", "Tatum", "Umber", "Vale", "Wren", "Ximena",
    "Yuki", "Zephyr", "Ari", "Bryn", "Cove", "Drew", "Emery", "Frankie",
]
LAST_NAMES = [
    "Ashford", "Brennan", "Calloway", "Danforth", "Eastwood", "Fairbanks",
    "Gallagher", "Hollis", "Ingram", "Jessup", "Kirkland", "Lockhart",
    "Merrick", "Northcott", "Ollivander", "Prescott", "Quimby", "Radcliffe",
    "Sinclair", "Thorne", "Underhill", "Vandermeer", "Whitlock", "Yarrow",
]

MODELS = [
    ("claude-fable-5", 0.79, 0.06),
    ("claude-opus-5", 0.17, 0.30),
    ("claude-sonnet-5", 0.07, 0.52),
    ("claude-haiku-4-5", 0.0007, 0.12),
]
PRODUCTS = ["chat", "claude_code", "cowork", "office_agent", "claude_design"]

# (name, rate at which its tool calls come back as errors, week index it is born).
#
# The error rate does NOT reach the analytics tables — the analytics API
# publishes no failure data for connectors at all, and inventing some here is
# exactly the mistake this fixture once made. It drives `is_error` on tool
# blocks in the conversation content, which is a real field of a real source.
CONNECTORS = [
    ("northwind_bi", 0.34, 7),   # born in week index 7 — the birth-date trap
    ("warehouse_sql", 0.06, 0),
    ("microsoft_365", 0.03, 0),
    ("sharepoint", 0.11, 0),
    ("web_fetch", 0.02, 0),
    ("file_creation", 0.01, 0),
    ("crm_connect", 0.08, 2),
    ("a3f19c2e-7b40-4d81-9e55-2c6f0a1b8d33", 0.02, 5),  # unnamed: a shadow connector
    # The same connector, arriving under two identities. A real lake does this:
    # one connector reports by name for most of its life and by bare identifier
    # for a few days, and anything counting connections counts it twice. Only an
    # operator can say they are the same thing, which is what the alias map in
    # _reports/config/connector_aliases.json is for.
    ("6f616b42-0ed8-571e-823f-ee4aca6b7ce9", 0.06, 9),  # = warehouse_sql
]

SKILLS = [
    "weekly-recap", "job-description", "sop-writer", "board-pack",
    "invoice-triage", "market-scan", "policy-lookup",
]

CHAT_TOPICS = [
    "Weekly commercial recap", "Regional performance summary", "Vendor invoice reconciliation",
    "Job description draft", "Standard operating procedure", "Board deck outline",
    "Headcount plan review", "Customer churn analysis", "Inventory variance check",
    "Quarterly forecast rebuild", "Policy question", "Onboarding checklist",
    "Pricing scenario model", "Supplier contract summary", "Incident write-up",
]

USER_PROMPTS = [
    "Pull last week's numbers by region and summarise what moved.",
    "Draft a job description for a senior analyst on the operations team.",
    "Turn these notes into a standard operating procedure.",
    "What changed in the forecast since last month, and why?",
    "Summarise this contract's termination and renewal terms.",
    "Build a one-page summary of the quarter for the leadership meeting.",
    "Compare this month's spend against budget and flag anything unusual.",
    "Rewrite this policy section in plain language.",
    "Reconcile these two reports and tell me where they disagree.",
    "Help me plan the rollout schedule for the new process.",
]
TRIVIAL_ACKS = ["proceed", "yes", "thanks", "ok", "continue", "go ahead"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


class Generator:
    def __init__(self, out: Path, seed: int, weeks: int) -> None:
        self.root = out
        self.rng = random.Random(seed)
        self.weeks = weeks
        self.seed = seed
        self.end = date(2026, 7, 12)  # a Sunday: histories end on a complete week
        self.start = self.end - timedelta(weeks=weeks - 1, days=6)
        self.users: list[JsonObject] = []
        self.week_starts = [
            self.start + timedelta(weeks=i) for i in range(weeks)
        ]

    # -- people -----------------------------------------------------------

    def build_directory(self) -> None:
        used: set[str] = set()
        index = 0
        for dept, count in DEPARTMENTS:
            for _ in range(count):
                while True:
                    first = self.rng.choice(FIRST_NAMES)
                    last = self.rng.choice(LAST_NAMES)
                    email = f"{first.lower()}.{last.lower()}@{EMAIL_DOMAIN}"
                    if email not in used:
                        used.add(email)
                        break
                index += 1

                # Engagement shape: a small power tail, a broad middle, a real
                # dormant group. Reports need all three to have anything to say.
                roll = self.rng.random()
                if roll < 0.06:
                    tier, intensity = "power", self.rng.uniform(2.2, 4.0)
                elif roll < 0.42:
                    tier, intensity = "steady", self.rng.uniform(0.8, 1.6)
                elif roll < 0.77:
                    tier, intensity = "light", self.rng.uniform(0.2, 0.6)
                else:
                    tier, intensity = "dormant", 0.0

                self.users.append({
                    "user_id": f"user_{index:04d}",
                    "email": email,
                    "full_name": f"{first} {last}",
                    "organization_role": "owner" if index <= 2 else "user",
                    "org_uuid": ORG_UUID,
                    "org_name": ORG_NAME,
                    "department": dept,
                    "tier": tier,
                    "intensity": intensity,
                    "created_at": iso(datetime(2026, 1, 15, tzinfo=timezone.utc)
                                      + timedelta(days=self.rng.randint(0, 120))),
                    "snapshot_at": now_iso(),
                })

        # One account carries a disproportionate share of spend through the
        # coding surface — the concentration finding needs a subject.
        self.heavy = next(u for u in self.users if u["tier"] == "power")
        self.heavy["intensity"] = 6.0
        self.heavy["heavy_code"] = True

    def write_directory(self) -> None:
        lake.write_snapshot(self.root, "directory_users", [
            {k: v for k, v in u.items() if k not in {"tier", "intensity", "heavy_code", "department"}}
            for u in self.users
        ])

        groups = []
        members = []
        for dept, _ in DEPARTMENTS:
            if dept == "Unassigned":
                continue
            people = [u for u in self.users if u["department"] == dept]
            group_id = f"group_{dept.lower()}"
            groups.append({
                "group_id": group_id,
                "group_name": dept,
                "description": f"{dept} team",
                "source_type": "scim" if dept != "Executive" else "direct",
                "role_ids": [],
                "role_names": [],
                "member_count": len(people),
                "created_at": iso(datetime(2026, 1, 10, tzinfo=timezone.utc)),
                "updated_at": now_iso(),
                "snapshot_at": now_iso(),
            })
            for person in people:
                members.append({
                    "group_id": group_id,
                    "group_name": dept,
                    "source_type": "scim",
                    "user_id": person["user_id"],
                    "email": person["email"],
                    "full_name": person["full_name"],
                    "member_since": person["created_at"],
                    "snapshot_at": now_iso(),
                })

        lake.write_snapshot(self.root, "directory_groups", groups)
        lake.write_snapshot(self.root, "directory_group_members", members)
        lake.write_snapshot(self.root, "directory_roles", [{
            "org_uuid": ORG_UUID, "org_name": ORG_NAME,
            "id": "role_user", "name": "user", "snapshot_at": now_iso(),
        }])

        self._write_csv("user_groups.csv",
                        ["user_id", "email", "full_name", "organization_role", "org_name",
                         "group_count", "groups"],
                        [{
                            "user_id": u["user_id"], "email": u["email"], "full_name": u["full_name"],
                            "organization_role": u["organization_role"], "org_name": ORG_NAME,
                            "group_count": 0 if u["department"] == "Unassigned" else 1,
                            "groups": "" if u["department"] == "Unassigned" else u["department"],
                        } for u in self.users])

    # -- analytics --------------------------------------------------------

    def write_analytics(self) -> None:
        cost_rows, user_cost_rows = [], []
        usage_rows, user_usage_rows = [], []
        summary_rows, activity_rows = [], []
        connector_rows, skill_rows = [], []

        for week_index, week_start in enumerate(self.week_starts):
            # Adoption ramps over the history, which gives the growth
            # decomposition (more people vs more per person) something real to
            # separate.
            ramp = 0.18 + 0.82 * (week_index / max(self.weeks - 1, 1))

            for offset in range(7):
                day = week_start + timedelta(days=offset)
                if day > self.end:
                    continue
                day_str = day.isoformat()
                weekday = day.weekday() < 5
                day_factor = (1.0 if weekday else 0.22) * ramp

                active_today: list[str] = []
                day_cost_by_model: dict[str, float] = {}
                day_requests_by_model: dict[str, int] = {}

                for user in self.users:
                    if user["tier"] == "dormant":
                        continue
                    if self.rng.random() > min(0.95, user["intensity"] * day_factor * 0.55):
                        continue

                    active_today.append(user["user_id"])
                    requests = max(1, int(self.rng.gauss(18 * user["intensity"], 6)))

                    spend = 0.0
                    for model, price, share in MODELS:
                        if user.get("heavy_code") and model == "claude-fable-5":
                            share = 0.82
                        model_requests = int(requests * share)
                        if model_requests <= 0:
                            continue
                        model_spend = model_requests * price * self.rng.uniform(0.7, 1.3)
                        spend += model_spend
                        day_cost_by_model[model] = day_cost_by_model.get(model, 0.0) + model_spend
                        day_requests_by_model[model] = day_requests_by_model.get(model, 0) + model_requests

                    tokens = int(requests * self.rng.gauss(24_000, 6_000))
                    cache_read = int(tokens * self.rng.uniform(0.88, 0.95))
                    output = int(tokens * self.rng.uniform(0.02, 0.05))

                    user_cost_rows.append({
                        "day": day_str,
                        "starting_at": f"{day_str}T00:00:00Z", "ending_at": f"{day_str}T23:59:59Z",
                        "user_id": user["user_id"], "email": user["email"], "name": user["full_name"],
                        "deleted": False,
                        "amount_usd": round(spend, 4), "amount_cents": str(round(spend * 100, 2)),
                        "list_amount_usd": round(spend * 1.0004, 4),
                        "list_amount_cents": str(round(spend * 100.04, 2)),
                        "currency": "USD", "product": None, "model": None,
                        "cost_type": None, "token_type": None, "requests": requests,
                    })
                    user_usage_rows.append({
                        "day": day_str,
                        "starting_at": f"{day_str}T00:00:00Z", "ending_at": f"{day_str}T23:59:59Z",
                        "user_id": user["user_id"], "email": user["email"], "name": user["full_name"],
                        "deleted": False,
                        "uncached_input_tokens": tokens - cache_read - output,
                        "cache_read_input_tokens": cache_read,
                        "cache_creation_1h_input_tokens": int(tokens * 0.01),
                        "cache_creation_5m_input_tokens": int(tokens * 0.01),
                        "output_tokens": output, "web_search_requests": self.rng.randint(0, 4),
                        "requests": requests, "total_tokens": tokens,
                    })
                    activity_rows.append(self._activity_row(day_str, user))

                for model, spend in day_cost_by_model.items():
                    product = "claude_code" if model == "claude-fable-5" and self.rng.random() < 0.6 \
                        else self.rng.choice(PRODUCTS)
                    cost_rows.append({
                        "day": day_str,
                        "starting_at": f"{day_str}T00:00:00Z", "ending_at": f"{day_str}T23:59:59Z",
                        "amount_usd": round(spend, 4), "amount_cents": str(round(spend * 100, 2)),
                        "list_amount_usd": round(spend * 1.0004, 4),
                        "list_amount_cents": str(round(spend * 100.04, 2)),
                        "currency": "USD", "product": product, "model": model,
                        "cost_type": None, "token_type": None, "context_window": None,
                        "inference_geo": None, "speed": None,
                        "requests": day_requests_by_model.get(model, 0),
                    })
                    total = day_requests_by_model.get(model, 0) * 24_000
                    usage_rows.append({
                        "day": day_str,
                        "starting_at": f"{day_str}T00:00:00Z", "ending_at": f"{day_str}T23:59:59Z",
                        "product": product, "model": model,
                        "uncached_input_tokens": int(total * 0.07),
                        "cache_read_input_tokens": int(total * 0.90),
                        "cache_creation_1h_input_tokens": int(total * 0.01),
                        "cache_creation_5m_input_tokens": int(total * 0.01),
                        "output_tokens": int(total * 0.03),
                        "web_search_requests": self.rng.randint(0, 20),
                        "requests": day_requests_by_model.get(model, 0),
                        "context_window": None, "inference_geo": None, "speed": None,
                    })

                summary_rows.append(self._summary_row(day_str, len(active_today)))
                connector_rows.extend(self._connector_rows(day_str, week_index, len(active_today)))
                skill_rows.extend(self._skill_rows(day_str, len(active_today)))

        for dataset, rows in (
            ("analytics_cost", cost_rows), ("analytics_user_cost", user_cost_rows),
            ("analytics_usage", usage_rows), ("analytics_user_usage", user_usage_rows),
            ("analytics_summaries", summary_rows), ("analytics_users", activity_rows),
            ("analytics_connectors", connector_rows), ("analytics_skills", skill_rows),
        ):
            lake.write_rows(self.root, dataset, rows)

        self.total_spend = sum(r["amount_usd"] for r in cost_rows)

    def _activity_row(self, day: str, user: JsonObject) -> JsonObject:
        heavy = bool(user.get("heavy_code"))
        return {
            "day": day, "user_id": user["user_id"], "email": user["email"],
            "web_search_count": self.rng.randint(0, 5),
            "chat_message_count": max(0, int(self.rng.gauss(11 * user["intensity"], 4))),
            "chat_distinct_conversation_count": max(1, int(self.rng.gauss(3 * user["intensity"], 1))),
            "chat_thinking_message_count": self.rng.randint(0, 4),
            "cc_lines_added": self.rng.randint(180, 900) if heavy else 0,
            "cc_lines_removed": self.rng.randint(20, 240) if heavy else 0,
            "cc_commit_count": self.rng.randint(0, 5) if heavy else 0,
            "cc_pull_request_count": self.rng.randint(0, 2) if heavy else 0,
            "cc_distinct_session_count": self.rng.randint(1, 5) if heavy else 0,
            "cowork_message_count": self.rng.randint(0, 12) if user["tier"] == "power" else 0,
            "cowork_action_count": self.rng.randint(0, 30) if user["tier"] == "power" else 0,
            "cowork_dispatch_turn_count": 0,
            "cowork_distinct_session_count": self.rng.randint(0, 3) if user["tier"] == "power" else 0,
            "design_message_count": 0,
            "office_message_count": self.rng.randint(0, 6),
            "files_uploaded": self.rng.randint(0, 3),
            "artifacts_created": self.rng.randint(0, 2),
        }

    def _summary_row(self, day: str, dau: int) -> JsonObject:
        assigned = len(self.users)
        return {
            "day": day, "starting_at": f"{day}T00:00:00Z", "ending_at": f"{day}T23:59:59Z",
            "assigned_seat_count": assigned, "pending_invite_count": 3,
            "daily_active_user_count": dau,
            "weekly_active_user_count": min(assigned, int(dau * 2.4)),
            "monthly_active_user_count": min(assigned, int(dau * 3.1)),
            "daily_adoption_rate": round(dau / assigned * 100, 2),
            "weekly_adoption_rate": round(min(assigned, dau * 2.4) / assigned * 100, 2),
            "monthly_adoption_rate": round(min(assigned, dau * 3.1) / assigned * 100, 2),
            "cowork_daily_active_user_count": max(0, dau // 8),
            "cowork_weekly_active_user_count": max(0, dau // 4),
            "cowork_monthly_active_user_count": max(0, dau // 3),
        }

    # Both of the rows below are shaped to match `pipeline.fetch.analytics`'s
    # `flatten_connector` / `flatten_skill` exactly — field for field, including
    # the nulls that appear when a product reports nothing for that day.
    #
    # This fixture used to emit read_call_count / write_call_count /
    # error_call_count / invocation_count, none of which the live API returns.
    # The metric stages were written against it and read those fields in
    # production, where they were absent, so every connector summed to zero and
    # the report published "0% of connector calls failed" over a source that
    # publishes no failure data at all. A fixture that is easier to compute
    # against than the real thing is not a fixture, it is a second product.

    def _connector_rows(self, day: str, week_index: int, active: int) -> list[JsonObject]:
        rows = []
        for name, _error_rate, born_week in CONNECTORS:
            if week_index < born_week:
                continue  # the birth-date trap, planted deliberately
            reach = max(0, int(self.rng.gauss(active * 0.12, active * 0.05)))
            if reach == 0:
                continue
            chat = max(0, int(self.rng.gauss(reach * 1.4, 2)))
            cowork = max(0, reach // 4)
            rows.append({
                "day": day, "connector_name": name,
                "distinct_user_count": reach,
                "chat_distinct_conversation_used": chat,
                "cc_distinct_session_used": None,
                "cowork_distinct_session_used": cowork,
                "office_metrics": {
                    "excel": {"distinct_session_connector_used_count": max(0, reach // 8)},
                },
            })
        return rows

    def _skill_rows(self, day: str, active: int) -> list[JsonObject]:
        rows = []
        for skill in SKILLS:
            if self.rng.random() > 0.55:
                continue
            reach = max(1, int(self.rng.gauss(active * 0.08, 2)))
            rows.append({
                "day": day, "skill_name": skill,
                "distinct_user_count": reach,
                "chat_distinct_conversation_used": max(1, int(self.rng.gauss(reach * 1.6, 2))),
                "cc_distinct_session_used": None,
                "cowork_distinct_session_used": None,
                "office_metrics": {
                    "docx": {"distinct_session_skill_used_count": max(0, reach // 6)},
                },
            })
        return rows

    # -- conversation content (raw store) ---------------------------------

    def write_content(self, chat_count: int) -> dict[str, int]:
        """Write synthetic chats into the raw store, then parse them like real ones.

        Going through the raw store rather than writing the derived tables
        directly means the demo exercises the same parser a real pull does. If
        the parser breaks, the demo breaks, and CI catches it.
        """
        active = [u for u in self.users if u["tier"] != "dormant"]
        planted = {"husks": 0, "tokens": 0, "luhn": 0, "placeholders": 0, "truncated": 0}

        for index in range(chat_count):
            user = self.rng.choice(active)
            day = self.start + timedelta(days=self.rng.randint(0, (self.end - self.start).days))
            created = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(
                hours=self.rng.randint(7, 19), minutes=self.rng.randint(0, 59))
            chat_id = f"chat_{index:05d}"

            chat = {
                "uuid": chat_id,
                "name": self.rng.choice(CHAT_TOPICS),
                "model": self.rng.choice([m[0] for m in MODELS]),
                "account_uuid": user["user_id"],
                "account_email": user["email"],
                "organization_uuid": ORG_UUID,
                "organization_name": ORG_NAME,
                "created_at": iso(created),
                "updated_at": iso(created + timedelta(minutes=self.rng.randint(2, 200))),
                "project_uuid": None,
            }

            # Roughly one chat in twenty exports with a title and no messages.
            if self.rng.random() < 0.05:
                rawstore.write_chat(self.root, chat_id, chat, [])
                planted["husks"] += 1
                continue

            messages, stats = self._messages_for(chat, created, user)
            for key in planted:
                planted[key] += stats.get(key, 0)
            rawstore.write_chat(self.root, chat_id, chat, messages)

        return planted

    def _messages_for(self, chat: JsonObject, created: datetime,
                      user: JsonObject) -> tuple[list[JsonObject], dict[str, int]]:
        turns = self.rng.choice([2, 4, 4, 6, 8, 12, 18])
        messages: list[JsonObject] = []
        stats = {"husks": 0, "tokens": 0, "luhn": 0, "placeholders": 0, "truncated": 0}
        moment = created

        for turn in range(turns):
            moment += timedelta(minutes=self.rng.randint(1, 12))
            message_id = f"{chat['uuid']}_m{turn:03d}"

            if turn % 2 == 0:
                text = self.rng.choice(USER_PROMPTS) if turn == 0 else self.rng.choice(
                    USER_PROMPTS + TRIVIAL_ACKS)
                messages.append({
                    "uuid": message_id, "role": "user", "created_at": iso(moment),
                    "content": [{"type": "text", "text": text}],
                })
                continue

            content: list[JsonObject] = []

            # Assistant turns sometimes call a tool, which is where the
            # interesting material lives.
            if self.rng.random() < 0.45:
                connector, error_rate, _ = self.rng.choice(CONNECTORS)
                tool = f"{connector}_query" if not connector.count("-") else f"{connector}_call"
                content.append({
                    "type": "tool_use", "name": tool, "integration": connector,
                    "input": {"query": "SELECT ...", "period": "last_week", "region": "north"},
                })

                is_error = self.rng.random() < error_rate
                payload, kind = self._tool_payload(connector, is_error)
                if kind:
                    stats[kind] = stats.get(kind, 0) + 1

                truncated = len(payload) >= rawstore.SOURCE_TRUNCATION_CHARS
                if truncated:
                    stats["truncated"] += 1

                content.append({
                    "type": "tool_result", "tool_name": tool, "integration": connector,
                    "is_error": is_error,
                    "content": [{"type": "text", "text": payload}],
                })

            content.append({
                "type": "text",
                "text": "Here is the summary you asked for, with the figures broken out by region "
                        "and a note on what moved most since the prior period.",
            })

            message: JsonObject = {
                "uuid": message_id, "role": "assistant", "created_at": iso(moment),
                "content": content,
            }

            # Generated files: the "finished document" signal the value report counts.
            if self.rng.random() < 0.28:
                message["generated_files"] = [{
                    "file_name": self.rng.choice([
                        "weekly-recap.docx", "forecast-model.xlsx", "sop-draft.docx",
                        "board-summary.pptx", "variance-analysis.xlsx",
                    ]),
                    "file_type": "document", "file_size": self.rng.randint(8_000, 400_000),
                    "id": f"file_{self.rng.randint(100000, 999999)}",
                }]

            messages.append(message)

        return messages, stats

    def _tool_payload(self, connector: str, is_error: bool) -> tuple[str, str | None]:
        """A tool result. Some carry the patterns the scanners must handle correctly."""
        if is_error:
            if self.rng.random() < 0.25:
                # An error that echoes a placeholder credential. A scanner that
                # cannot tell this from a live secret will over-report wildly.
                return ("Connection failed: authentication rejected for "
                        "user=service_account password=REDACTED endpoint=internal-bi",  # scrubber:allow — synthetic placeholder, the point of the test
                        "placeholders")
            return ("Error: field-value search timed out after 30000ms. "
                    "The upstream service did not respond.", None)

        roll = self.rng.random()

        if connector == "northwind_bi" and roll < 0.30:
            # The finding this tool exists to surface: a connector putting a
            # signed session token into the result URL, in tool output.
            token = "".join(self.rng.choice("abcdef0123456789") for _ in range(48))
            return (f"Result set ready (2,481 rows).\n"
                    f"Download: https://bi.internal.example.com/export/"
                    f"r-{self.rng.randint(10000, 99999)}?token={token}&expires=3600", "tokens")

        if roll < 0.18:
            # Point-of-sale rows whose order numbers happen to pass a Luhn
            # check. They are not payment cards, and a card detector that says
            # they are scores zero precision.
            rows = "\n".join(
                f"order_id,4539{self.rng.randint(100000000, 999999999)},sku,{self.rng.randint(1000, 9999)},"
                f"qty,{self.rng.randint(1, 40)},total,{self.rng.uniform(10, 900):.2f}"
                for _ in range(self.rng.randint(6, 20))
            )
            return (f"order_id,card_ref,sku,sku_id,qty,units,total,amount\n{rows}", "luhn")

        if roll < 0.24:
            # A payload at the source cap: everything past it never left the
            # vendor, so any count over it is a floor.
            body = "region,period,units,revenue\n" + "\n".join(
                f"north,2026-W{self.rng.randint(10, 28):02d},{self.rng.randint(100, 9999)},"
                f"{self.rng.uniform(1000, 90000):.2f}" for _ in range(400)
            )
            return (body[:rawstore.SOURCE_TRUNCATION_CHARS], None)

        rows = "\n".join(
            f"{self.rng.choice(['north', 'south', 'east', 'west'])},"
            f"{self.rng.randint(100, 9999)},{self.rng.uniform(1000, 90000):.2f}"
            for _ in range(self.rng.randint(4, 30))
        )
        return (f"region,units,revenue\n{rows}", None)

    # -- summaries and state ----------------------------------------------

    def write_state(self) -> None:
        state = lake.read_state(self.root)
        for dataset in (
            "analytics_cost", "analytics_user_cost", "analytics_usage", "analytics_user_usage",
            "analytics_summaries", "analytics_users", "analytics_connectors", "analytics_skills",
        ):
            lake.set_dataset_state(
                state, dataset,
                watermark_day=self.end.isoformat(), last_run=now_iso(),
                data_refreshed_at=now_iso(),
                partitions_total=len(lake.list_partitions(self.root, dataset)),
            )
        state["synthetic"] = {
            "generator": "pipeline.demo.generate_synthetic_lake",
            "organization": ORG_NAME,
            "seed": self.seed,
            "note": "Every person, domain, figure, and system in this lake is invented. "
                    "It exists so the pipeline can be run and reviewed without a real "
                    "Claude Enterprise tenant. Do not present it as anyone's data.",
        }
        lake.write_state(self.root, state)

    def _write_csv(self, name: str, fields: list[str], rows: list[JsonObject]) -> None:
        import csv
        path = self.root / "_summary" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a synthetic lake for a fictional company.")
    ap.add_argument("--out", default="data-demo", help="lake root to create (default: data-demo)")
    ap.add_argument("--seed", type=int, default=20260819, help="RNG seed; same seed, same lake")
    ap.add_argument("--weeks", type=int, default=14, help="weeks of history (default: 14)")
    ap.add_argument("--chats", type=int, default=900, help="conversations to generate (default: 900)")
    ap.add_argument("--skip-content", action="store_true",
                    help="analytics and directory only; no conversation content")
    args = ap.parse_args()

    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)

    gen = Generator(root, args.seed, args.weeks)
    print(f"[demo] generating {ORG_NAME}: {args.weeks} weeks, seed {args.seed}", file=sys.stderr)

    gen.build_directory()
    gen.write_directory()
    print(f"[demo] directory: {len(gen.users)} seats", file=sys.stderr)

    gen.write_analytics()
    print(f"[demo] analytics: ${gen.total_spend:,.2f} across {args.weeks} weeks", file=sys.stderr)

    planted: dict[str, int] = {}
    parsed: dict[str, Any] = {}
    if not args.skip_content:
        planted = gen.write_content(args.chats)
        print(f"[demo] content: {args.chats} chats written to the raw store", file=sys.stderr)
        from pipeline.fetch import parse_raw
        parsed = parse_raw.parse_all(root, progress_every=0)

    gen.write_state()

    result = {
        "organization": ORG_NAME,
        "data_dir": str(root),
        "seed": args.seed,
        "seats": len(gen.users),
        "weeks": args.weeks,
        "total_spend_usd": round(gen.total_spend, 2),
        "planted_quirks": planted,
        "parsed": parsed,
    }
    print(json.dumps(result, indent=2))
    print(
        f"\nSynthetic lake ready at {root}.\n"
        f"Everything in it is invented. Run the reports against it with --data-dir {root}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
