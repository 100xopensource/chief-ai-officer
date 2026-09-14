#!/usr/bin/env python3
"""Shared Claude Compliance API client + helpers.

Import-only library used by the compliance fetchers (fetch_compliance_content.py,
fetch_org_directory.py). One place owns the client, auth (x-api-key), pagination
(next_page / id-cursor), and the small payload helpers. No CLI / main here.

Credentials are the caller's responsibility — pass a Compliance Access Key
(sk-ant-api01-...) to ComplianceClient / make_client.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Union

from pipeline.fetch._requests import require_requests

requests = require_requests()

API_BASE = os.environ.get("CAIO_API_BASE", "https://api.anthropic.com")

# No organization is baked in. An earlier internal version carried one
# deployment's org UUID as a constant, which is both an identifier belonging to
# that customer and a silent default that would point another operator's pull at
# the wrong place. Callers pass --org-uuid, or set CAIO_ORG_UUID, or let the API
# enumerate whatever the key can reach.
DEFAULT_ORG_UUID = os.environ.get("CAIO_ORG_UUID") or None
USERS_LIMIT = 500
CHATS_LIMIT = 100
TIMEOUT = 60

JsonObject = dict[str, Any]
Params = Union[dict[str, Any], list[tuple[str, Any]]]


class ComplianceClient:
    def __init__(self, api_key: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "accept": "application/json",
            }
        )

    def get(self, url: str, params: Params | None = None) -> requests.Response:
        return self.session.get(url, params=normalize_params(params), timeout=TIMEOUT)


def make_client(api_key: str) -> ComplianceClient:
    return ComplianceClient(api_key)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"[compliance_api] {message}", file=sys.stderr)


def normalize_params(params: Params | None) -> Params:
    if params is None:
        return {}
    if isinstance(params, list):
        return [(key, value) for key, value in params if value is not None]
    return {key: value for key, value in params.items() if value is not None}


def params_with(params: Params | None, key: str, value: Any) -> Params:
    if isinstance(params, list):
        next_params = [(k, v) for k, v in params if k != key]
        if value is not None:
            next_params.append((key, value))
        return next_params

    next_params = dict(params or {})
    if value is None:
        next_params.pop(key, None)
    else:
        next_params[key] = value
    return next_params


def get_json(
    client: ComplianceClient,
    url: str,
    params: Params | None,
    errors: list[JsonObject],
    stage: str,
) -> tuple[int | None, JsonObject | list[Any] | None]:
    request_params = normalize_params(params)
    try:
        response = client.get(url, request_params)
    except requests.exceptions.RequestException as exc:
        errors.append({"stage": stage, "type": "request_exception", "detail": str(exc), "url": url})
        log(f"ERROR {stage}: request exception: {exc}")
        return None, None

    status_code = response.status_code
    if status_code != 200:
        body = response.text[:2000]
        errors.append(
            {
                "stage": stage,
                "type": "http_error",
                "status_code": status_code,
                "detail": body,
                "url": url,
                "params": request_params,
            }
        )
        log(f"ERROR {stage}: HTTP {status_code}: {body}")
        return status_code, None

    try:
        return status_code, response.json()
    except ValueError as exc:
        errors.append({"stage": stage, "type": "json_decode_error", "detail": str(exc), "url": url})
        log(f"ERROR {stage}: JSON decode error: {exc}")
        return status_code, None


def response_items(payload: Any, item_key: str = "data") -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get(item_key)
        if items is None and item_key != "data":
            items = payload.get("data")
        return items if isinstance(items, list) else []
    return []


def content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n\n".join(parts)


def paginate_page_token(
    client: ComplianceClient,
    url: str,
    params: Params | None,
    errors: list[JsonObject],
    stage: str,
    item_key: str = "data",
) -> list[Any]:
    """Paginate endpoints that return next_page and expect it as page."""
    collected: list[Any] = []
    request_params = normalize_params(params)
    pages = 0

    while True:
        pages += 1
        _, payload = get_json(client, url, request_params, errors, stage)
        if payload is None:
            break

        items = response_items(payload, item_key=item_key)
        collected.extend(items)

        has_more = bool(payload.get("has_more")) if isinstance(payload, dict) else False
        next_page = payload.get("next_page") if isinstance(payload, dict) else None
        log(f"{stage}: page {pages} -> {len(items)} items (total {len(collected)}) has_more={has_more}")

        if not has_more or not next_page:
            break
        request_params = params_with(request_params, "page", next_page)

        if pages > 500:
            errors.append({"stage": stage, "type": "pagination_guard", "detail": "exceeded 500 pages"})
            log(f"WARN {stage}: pagination guard tripped")
            break

    return collected


def paginate_id_cursor(
    client: ComplianceClient,
    url: str,
    params: Params | None,
    errors: list[JsonObject],
    stage: str,
    item_key: str = "data",
) -> list[Any]:
    """Paginate endpoints that return last_id and expect it as after_id."""
    collected: list[Any] = []
    request_params = normalize_params(params)
    pages = 0

    while True:
        pages += 1
        _, payload = get_json(client, url, request_params, errors, stage)
        if payload is None:
            break

        items = response_items(payload, item_key=item_key)
        collected.extend(items)

        has_more = bool(payload.get("has_more")) if isinstance(payload, dict) else False
        last_id = payload.get("last_id") if isinstance(payload, dict) else None
        log(f"{stage}: page {pages} -> {len(items)} items (total {len(collected)}) has_more={has_more}")

        if not has_more or not last_id:
            break
        request_params = params_with(request_params, "after_id", last_id)

        if pages > 500:
            errors.append({"stage": stage, "type": "pagination_guard", "detail": "exceeded 500 pages"})
            log(f"WARN {stage}: pagination guard tripped")
            break

    return collected


def resolve_organizations(
    client: ComplianceClient,
    errors: list[JsonObject],
    data_gaps: list[str],
    preferred_org_uuid: str | None,
) -> list[JsonObject]:
    status, payload = get_json(client, f"{API_BASE}/v1/compliance/organizations", {}, errors, "organizations")
    orgs = response_items(payload)

    if orgs:
        if preferred_org_uuid:
            preferred = [org for org in orgs if isinstance(org, dict) and org.get("uuid") == preferred_org_uuid]
            if preferred:
                return preferred
            data_gaps.append(
                f"Preferred organization {preferred_org_uuid} was not returned; using the first listed organization."
            )
            first_org = next((org for org in orgs if isinstance(org, dict)), None)
            return [first_org] if first_org else []
        return [org for org in orgs if isinstance(org, dict)]

    if preferred_org_uuid and status in (403, 404):
        data_gaps.append(
            "Could not list organizations; falling back to the configured org UUID for user enumeration."
        )
        return [{"uuid": preferred_org_uuid, "name": None, "created_at": None}]

    return []


def fetch_users_for_org(
    client: ComplianceClient,
    org_uuid: str,
    errors: list[JsonObject],
) -> list[JsonObject]:
    users = paginate_page_token(
        client,
        f"{API_BASE}/v1/compliance/organizations/{org_uuid}/users",
        {"limit": USERS_LIMIT},
        errors,
        f"users:{org_uuid}",
    )
    return [user for user in users if isinstance(user, dict)]


def user_email(user: JsonObject) -> str:
    return str(user.get("email") or user.get("email_address") or "").strip().lower()


def user_id(user: JsonObject) -> str | None:
    value = user.get("id") or user.get("user_id") or user.get("uuid")
    return str(value) if value else None


def chat_id(chat: JsonObject) -> str | None:
    value = chat.get("id") or chat.get("chat_id")
    return str(value) if value else None


def fetch_messages_for_chat(
    client: ComplianceClient,
    chat: JsonObject,
    errors: list[JsonObject],
) -> JsonObject | None:
    cid = chat_id(chat)
    if not cid:
        return None

    url = f"{API_BASE}/v1/compliance/apps/chats/{cid}/messages"
    status, payload = get_json(client, url, {}, errors, f"messages:{cid}")
    if status != 200 or not isinstance(payload, dict):
        return None

    messages = response_items(payload, item_key="chat_messages")
    page_payload = payload
    pages = 1

    while page_payload.get("has_more") and page_payload.get("last_id"):
        pages += 1
        _, next_payload = get_json(
            client,
            url,
            {"after_id": page_payload["last_id"]},
            errors,
            f"messages:{cid}",
        )
        if not isinstance(next_payload, dict):
            break
        messages.extend(response_items(next_payload, item_key="chat_messages"))
        page_payload = next_payload

        if pages > 500:
            errors.append({"stage": f"messages:{cid}", "type": "pagination_guard", "detail": "exceeded 500 pages"})
            log(f"WARN messages:{cid}: pagination guard tripped")
            break

    payload["chat_messages"] = messages
    payload["has_more"] = bool(page_payload.get("has_more"))
    payload["first_id"] = payload.get("first_id")
    payload["last_id"] = page_payload.get("last_id", payload.get("last_id"))

    log(f"messages:{cid}: {len(messages)} messages across {pages} page(s)")
    return payload
