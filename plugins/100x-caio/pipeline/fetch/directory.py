#!/usr/bin/env python3
"""Pull the Claude Enterprise org directory into normalized lake snapshots.

Reads the Compliance API directory endpoints and writes them into the same
data/ lake as every other puller, using datalake's snapshot primitive. The
directory is reference/dimension data (a roster, not a time series), so each
dataset is a single snapshot.jsonl that the run overwrites — no week partitions.
This shares the one-folder-per-dataset layout with the time-series datasets, so
pandas/DuckDB read it the same way.

All Compliance API plumbing (client, auth, pagination) is reused from
compliance_api.py — there is one ComplianceClient for the whole skill.

Endpoints (Compliance API; sent as `x-api-key`):
  GET /v1/compliance/organizations
  GET /v1/compliance/organizations/{org_uuid}/users    (read:compliance_user_data)
  GET /v1/compliance/organizations/{org_uuid}/roles    (read:compliance_org_data)
  GET /v1/compliance/groups                            (read:compliance_org_data)
  GET /v1/compliance/groups/{group_id}/members         (read:compliance_user_data)

Datasets (under --data-dir, default `data`):
  directory_users/snapshot.jsonl          one row per org member
  directory_roles/snapshot.jsonl          RBAC roles per org
  directory_groups/snapshot.jsonl         RBAC / SCIM groups (parent-wide)
  directory_group_members/snapshot.jsonl  group <-> user edges, enriched
  _summary/users.csv                      flat user table
  _summary/groups.csv                     groups with role names + member counts
  _summary/group_members.csv              membership edges
  _summary/user_groups.csv                JOIN-READY: one row per user -> groups
  _state.json                             per-dataset run metadata (snapshot_at)

`user_groups.csv` is the headline analytics table: join it to the analytics
datasets (analytics_user_cost / analytics_user_usage) on `user_id` (or `email`)
to slice cost and usage by group, role, or org.

Examples:
  python fetch_org_directory.py
  python -m pipeline.fetch.directory --org-uuid <your-org-uuid>
  python fetch_org_directory.py --data-dir data

Credentials (first match wins): CAIO_API_KEY, then
ANTHROPIC_COMPLIANCE_ACCESS_KEY, then ANTHROPIC_API_KEY. Must be a Compliance
Access Key (sk-ant-api01-...); an Admin key returns 403.
"""

from __future__ import annotations

# Runnable two ways: `python3 -m pipeline.fetch.directory` and `python3 <path>/directory.py`.
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
from pathlib import Path
from typing import Any

from pipeline.fetch import compliance_api as cc
from pipeline.lake import datalake as lake

PAGE_LIMIT = 1000

DATASET_USERS = "directory_users"
DATASET_ROLES = "directory_roles"
DATASET_GROUPS = "directory_groups"
DATASET_MEMBERS = "directory_group_members"

JsonObject = dict[str, Any]


def log(message: str) -> None:
    print(f"[org_directory] {message}", file=sys.stderr)


def write_csv(path: Path, fieldnames: list[str], rows: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def paginate(client: cc.ComplianceClient, url: str, errors: list[JsonObject], stage: str) -> list[JsonObject]:
    """Walk a `next_page`/`page` directory endpoint via the shared paginator."""
    items = cc.paginate_page_token(client, url, {"limit": PAGE_LIMIT}, errors, stage)
    return [item for item in items if isinstance(item, dict)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch the Claude Enterprise org directory into normalized lake snapshots.")
    parser.add_argument("--data-dir", default=lake.DEFAULT_DATA_DIR, help="Lake root directory (default: data).")
    parser.add_argument("--org-uuid", help="Restrict users/roles to one organization UUID. Default: all organizations.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = cc.now_iso()
    errors: list[JsonObject] = []
    data_gaps: list[str] = []

    api_key = (
        os.environ.get("CAIO_API_KEY")
        or os.environ.get("ANTHROPIC_COMPLIANCE_ACCESS_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    root = Path(args.data_dir).expanduser()
    summary = root / "_summary"
    root.mkdir(parents=True, exist_ok=True)

    if not api_key:
        errors.append({"stage": "env", "type": "missing_env", "detail": "CAIO_API_KEY, ANTHROPIC_COMPLIANCE_ACCESS_KEY, or ANTHROPIC_API_KEY not set"})
        log("ERROR: missing credentials")
        print(json.dumps({"errors": errors}, indent=2))
        return 1

    client = cc.make_client(api_key)

    orgs = cc.resolve_organizations(client, errors, data_gaps, args.org_uuid)
    log(f"organizations: {len(orgs)}")

    # Users + roles per organization.
    user_rows: list[JsonObject] = []
    role_rows: list[JsonObject] = []
    user_index: dict[str, JsonObject] = {}  # user_id -> enriched user row
    role_index: dict[str, str] = {}  # role_id -> role name
    for org in orgs:
        org_uuid = org.get("uuid")
        org_name = org.get("name")
        if not org_uuid:
            continue
        users = cc.fetch_users_for_org(client, str(org_uuid), errors)
        for u in users:
            row = {
                "snapshot_at": started_at,
                "user_id": u.get("id"),
                "email": u.get("email"),
                "full_name": u.get("full_name"),
                "organization_role": u.get("organization_role"),
                "org_uuid": org_uuid,
                "org_name": org_name,
                "created_at": u.get("created_at"),
            }
            user_rows.append(row)
            if row["user_id"]:
                user_index[row["user_id"]] = row
        roles = paginate(client, f"{cc.API_BASE}/v1/compliance/organizations/{org_uuid}/roles", errors, f"roles:{org_uuid}")
        for r in roles:
            role_rows.append({"snapshot_at": started_at, "org_uuid": org_uuid, "org_name": org_name, **r})
            if r.get("id"):
                role_index[r["id"]] = r.get("name")
        log(f"org {org_name or org_uuid}: {len(users)} users, {len(roles)} roles")

    # Groups (parent-wide) + members.
    groups = paginate(client, f"{cc.API_BASE}/v1/compliance/groups", errors, "groups")
    group_rows: list[JsonObject] = []
    member_rows: list[JsonObject] = []
    user_groups: dict[str, list[str]] = {}  # user_id -> [group_name]
    for grp in groups:
        gid = grp.get("id")
        gname = grp.get("name")
        role_ids = grp.get("roles") or []
        members = paginate(client, f"{cc.API_BASE}/v1/compliance/groups/{gid}/members", errors, f"members:{gid}") if gid else []
        for m in members:
            uid = m.get("user_id")
            enriched = user_index.get(uid or "", {})
            member_rows.append(
                {
                    "snapshot_at": started_at,
                    "group_id": gid,
                    "group_name": gname,
                    "source_type": grp.get("source_type"),
                    "user_id": uid,
                    "email": m.get("email") or enriched.get("email"),
                    "full_name": enriched.get("full_name"),
                    "member_since": m.get("created_at"),
                }
            )
            if uid:
                user_groups.setdefault(uid, []).append(gname)
        group_rows.append(
            {
                "snapshot_at": started_at,
                "group_id": gid,
                "group_name": gname,
                "description": grp.get("description"),
                "source_type": grp.get("source_type"),
                "role_ids": role_ids,
                "role_names": [role_index.get(rid, rid) for rid in role_ids],
                "member_count": len(members),
                "created_at": grp.get("created_at"),
                "updated_at": grp.get("updated_at"),
            }
        )
        log(f"group {gname or gid}: {len(members)} members")

    # JOIN-READY: one row per user with their group memberships.
    user_group_rows: list[JsonObject] = []
    for uid, urow in user_index.items():
        names = sorted(user_groups.get(uid, []))
        user_group_rows.append(
            {
                "user_id": uid,
                "email": urow.get("email"),
                "full_name": urow.get("full_name"),
                "organization_role": urow.get("organization_role"),
                "org_name": urow.get("org_name"),
                "group_count": len(names),
                "groups": "; ".join(n for n in names if n),
            }
        )
    user_group_rows.sort(key=lambda r: r.get("group_count", 0), reverse=True)

    # Normalized snapshot datasets (one folder per dataset, overwritten each run).
    lake.write_snapshot(root, DATASET_USERS, user_rows)
    lake.write_snapshot(root, DATASET_ROLES, role_rows)
    lake.write_snapshot(root, DATASET_GROUPS, group_rows)
    lake.write_snapshot(root, DATASET_MEMBERS, member_rows)

    # Ready-to-use CSVs.
    write_csv(summary / "users.csv", ["user_id", "email", "full_name", "organization_role", "org_name", "org_uuid", "created_at"], user_rows)
    write_csv(summary / "groups.csv", ["group_id", "group_name", "source_type", "member_count", "role_names", "description", "created_at"],
              [{**g, "role_names": "; ".join(n for n in g.get("role_names", []) if n)} for g in group_rows])
    write_csv(summary / "group_members.csv", ["group_id", "group_name", "source_type", "user_id", "email", "full_name", "member_since"], member_rows)
    write_csv(summary / "user_groups.csv", ["user_id", "email", "full_name", "organization_role", "org_name", "group_count", "groups"], user_group_rows)

    # Per-dataset run metadata in the shared _state.json.
    state = lake.read_state(root)
    counts = {DATASET_USERS: len(user_rows), DATASET_ROLES: len(role_rows), DATASET_GROUPS: len(group_rows), DATASET_MEMBERS: len(member_rows)}
    for dataset, count in counts.items():
        lake.set_dataset_state(state, dataset, last_run=cc.now_iso(), snapshot_at=started_at, rows=count)
    lake.write_state(root, state)

    result = {
        "started_at": started_at,
        "finished_at": cc.now_iso(),
        "data_dir": str(root),
        "org_uuid_filter": args.org_uuid,
        "counts": {"organizations": len(orgs), **counts},
        "data_gaps": data_gaps,
        "errors": errors,
    }
    print(json.dumps(result, indent=2, default=str))
    log(f"users={len(user_rows)} groups={len(group_rows)} members={len(member_rows)}")
    return 0 if not errors else 5


if __name__ == "__main__":
    sys.exit(main())
