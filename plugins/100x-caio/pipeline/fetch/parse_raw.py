#!/usr/bin/env python3
"""Parse the raw compliance store into the derived content tables. Zero API calls.

    data/raw/compliance/*.jsonl
        │
        ├─▶ dim_chat          one row per chat      (week by created_at)
        ├─▶ fact_message      one row per message   (week by created_at)
        ├─▶ fact_block        one row per content block within a message
        ├─▶ fact_attachment   one row per uploaded file, generated file, artifact
        └─▶ _reports/payload_index.csv   the address book every scan joins against

This runs entirely against local files, so it is cheap to re-run and is the
correct response to any parsing bug: fix the code, re-parse, done. No
pagination, no rate limit, no waiting.

The four scan surfaces
----------------------
Everything downstream attributes findings to one of these. They are not equally
interesting, and the asymmetry is the single most useful thing this project has
learned:

    typed_prompt   what a person typed.            ~4% of confirmed findings.
    tool_input     what a tool was asked.          ~18%.
    tool_output    what a tool returned.           ~77%.
    attachment     file and artifact names only.   bodies are not retrievable.

Sensitive data is overwhelmingly *returned by systems*, not typed by people. A
pipeline that scans prompts alone — the intuitive design — misses roughly
nineteen findings in twenty. That is why raw must be parsed at block level and
why `content_text`-only extraction was abandoned.

The error channel is deliberately **not** a fifth surface. An error is the
`is_error` flag on a `tool_output` row. Modelling it as its own class double
counts it and makes the surface percentages fail to sum.

What is retained, and what is not
---------------------------------
Block *text* is retained for text and tool_result blocks, because the scanners
must read it. For `tool_use` blocks only the input **key names** are retained,
never the values: knowing a tool was called with `patient_id` and `dob` is the
signal, and the values themselves add risk without adding information. The
scanners that need tool-input values read them from the raw store directly under
a read budget, rather than having them duplicated into a table that every later
stage would carry around.
"""

from __future__ import annotations

# Runnable two ways: `python3 -m pipeline.fetch.parse_raw` and `python3 <path>/parse_raw.py`.
# The second has no package context, so put the plugin root on the path first.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))


import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pipeline.lake import datalake as lake
from pipeline.lake import raw as rawstore

JsonObject = dict[str, Any]

DATASET_CHATS = "dim_chat"
DATASET_MESSAGES = "fact_message"
DATASET_BLOCKS = "fact_block"
DATASET_ATTACHMENTS = "fact_attachment"

PAYLOAD_INDEX = "payload_index.csv"

SURFACE_TYPED = "typed_prompt"
SURFACE_TOOL_INPUT = "tool_input"
SURFACE_TOOL_OUTPUT = "tool_output"
SURFACE_ATTACHMENT = "attachment"

CHARS_PER_TOKEN = 4  # rough proxy; never mixed with billed dollars

_CODE_FENCE = re.compile(r"```")
_MODEL_FAMILIES = ("opus", "sonnet", "haiku", "fable")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def model_family(model: Any) -> str | None:
    if not isinstance(model, str) or not model:
        return None
    lowered = model.lower()
    for family in _MODEL_FAMILIES:
        if family in lowered:
            return family
    return "other"


def est_tokens(char_count: int) -> int:
    return int(char_count // CHARS_PER_TOKEN)


def day_of(ts: Any) -> str | None:
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    return ts[:10]


def _text_of(block: JsonObject) -> str:
    """Text carried by a block, whatever shape the block uses.

    A `tool_result` nests its text one level down, inside a content list of
    `{type, text}` parts, rather than in a top-level field. Missing that nesting
    is how an early version concluded error payloads were empty.
    """
    if isinstance(block.get("text"), str):
        return block["text"]

    inner = block.get("content")
    if isinstance(inner, str):
        return inner
    if isinstance(inner, list):
        parts = [p.get("text", "") for p in inner if isinstance(p, dict) and isinstance(p.get("text"), str)]
        return "\n\n".join(parts)
    return ""


def _tool_input_keys(block: JsonObject) -> list[str]:
    """Key NAMES from a tool_use input. Never the values — see module docstring."""
    payload = block.get("input")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    return sorted(str(k) for k in payload) if isinstance(payload, dict) else []


def _integration_of(block: JsonObject, tool_name: str | None) -> str | None:
    """Which connector a tool call belongs to.

    Prefer whatever the vendor labelled it. Otherwise derive it from an MCP
    tool-name prefix (`qlik_get_fields` -> `qlik`), which is how most connectors
    namespace their tools.
    """
    for key in ("integration", "integration_name", "server_name", "mcp_server_name"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if tool_name and "_" in tool_name:
        prefix = tool_name.split("_", 1)[0]
        if len(prefix) > 1:
            return prefix.lower()
    return None


def _is_truncated(text: str, block: JsonObject) -> bool:
    """Whether the source cut this payload.

    Trust an explicit flag if the vendor sets one; otherwise infer from the
    known cap. Either way the consequence is the same and must travel with every
    count derived from it: content past the cut never left the vendor, so every
    confirmed count is a floor.
    """
    if isinstance(block.get("truncated"), bool):
        return block["truncated"]
    return len(text) >= rawstore.SOURCE_TRUNCATION_CHARS


# --------------------------------------------------------------------------
# row builders
# --------------------------------------------------------------------------

def build_blocks(chat: JsonObject, message: JsonObject, chat_id: str, family: str | None) -> list[JsonObject]:
    """One row per content block. The unit every scanner addresses."""
    content = message.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []

    role = str(message.get("role") or "")
    message_id = str(message.get("uuid") or message.get("id") or "")
    created_at = message.get("created_at")
    rows: list[JsonObject] = []

    for block_idx, block in enumerate(content):
        if not isinstance(block, dict):
            continue

        block_type = str(block.get("type") or "text")
        text = _text_of(block)
        tool_name = block.get("name") if block_type == "tool_use" else block.get("tool_name")
        tool_name = str(tool_name) if isinstance(tool_name, str) else None

        if block_type == "tool_use":
            surface = SURFACE_TOOL_INPUT
        elif block_type == "tool_result":
            surface = SURFACE_TOOL_OUTPUT
        else:
            # A text block is a typed prompt only when a person wrote it.
            # Assistant prose is generated, not supplied, and counting it as a
            # human-typed surface would overstate what people put into Claude.
            surface = SURFACE_TYPED if role == "user" else None

        rows.append(
            {
                "day": day_of(created_at),
                "block_id": f"{message_id}:{block_idx}",
                "chat_id": chat_id,
                "message_id": message_id,
                "block_idx": block_idx,
                "block_type": block_type,
                "surface": surface,
                "role": role,
                "created_at": created_at,
                "model_family": family,
                "tool_name": tool_name,
                "integration": _integration_of(block, tool_name),
                "mcp_server_url": block.get("mcp_server_url") or block.get("server_url"),
                "input_keys": _tool_input_keys(block) if block_type == "tool_use" else None,
                "is_error": bool(block.get("is_error")),
                "truncated": _is_truncated(text, block),
                "char_len": len(text),
                "est_tokens": est_tokens(len(text)),
                "has_code": bool(_CODE_FENCE.search(text)),
                # Text is retained for the surfaces the scanners read. Tool
                # inputs are represented by their key names only.
                "text": text if block_type != "tool_use" else None,
            }
        )
    return rows


def build_attachments(chat: JsonObject, message: JsonObject, chat_id: str) -> list[JsonObject]:
    """Uploaded files, generated files, and artifacts — names and metadata only.

    These live at the *message* level, not inside the content list, which is why
    a block-only reading concludes they do not exist. They do exist, and their
    filenames are close to always the human original (`Q3 offer letter.docx`,
    not a hash), which makes the name itself a scannable signal.

    Bodies are never retrievable: each entry carries an opaque id and no URL.
    Any statement about attachments must say "names and metadata scanned, bodies
    not present in the export" — the distinction matters most in exactly the
    regulated settings where someone will ask.
    """
    message_id = str(message.get("uuid") or message.get("id") or "")
    created_at = message.get("created_at")
    rows: list[JsonObject] = []

    for kind, key, name_field in (
        ("file", "files", "file_name"),
        ("generated_file", "generated_files", "file_name"),
        ("artifact", "artifacts", "title"),
    ):
        entries = message.get(key)
        if not isinstance(entries, list):
            continue
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            name = entry.get(name_field) or entry.get("file_name") or entry.get("title") or entry.get("name")
            rows.append(
                {
                    "day": day_of(created_at),
                    "attachment_id": f"{message_id}:{kind}:{position}",
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "kind": kind,
                    # Artifacts carry a title where files carry a filename; the
                    # column is named for what it holds, not for one of them.
                    "display_name": str(name) if name else None,
                    "has_name": bool(name),
                    "file_type": entry.get("file_type") or entry.get("type"),
                    "file_size": entry.get("file_size") or entry.get("size"),
                    "created_at": created_at,
                    "body_retrievable": False,
                }
            )
    return rows


def build_message(chat: JsonObject, message: JsonObject, chat_id: str, turn_index: int,
                  family: str | None, blocks: list[JsonObject]) -> JsonObject:
    """One row per message. Text-level metrics, with block detail one table down."""
    text = "\n\n".join(b["text"] for b in blocks if b.get("text"))
    char_count = len(text)
    return {
        "day": day_of(message.get("created_at")),
        "message_id": str(message.get("uuid") or message.get("id") or ""),
        "chat_id": chat_id,
        "user_id": chat.get("account_uuid") or chat.get("user_id"),
        "organization_uuid": chat.get("organization_uuid"),
        "role": str(message.get("role") or ""),
        "turn_index": turn_index,
        "created_at": message.get("created_at"),
        "model_family": family,
        "block_count": len(blocks),
        "char_count": char_count,
        "word_count": len(text.split()),
        "est_tokens": est_tokens(char_count),
        "has_code": any(b.get("has_code") for b in blocks),
        "has_tool_use": any(b.get("block_type") == "tool_use" for b in blocks),
        "has_tool_result": any(b.get("block_type") == "tool_result" for b in blocks),
        "has_error": any(b.get("is_error") for b in blocks),
        "content_text": text,
    }


def build_chat(chat: JsonObject, chat_id: str, messages: list[JsonObject],
               message_rows: list[JsonObject], block_rows: list[JsonObject],
               family: str | None) -> JsonObject:
    """One row per chat, partitioned by the week it was created.

    Note the partition follows `created_at` while the incremental signal is
    `updated_at`: resuming an old chat refreshes its rows in their original week
    rather than moving them. Past weeks are therefore not frozen, and any
    week-over-week comparison must say whether it counts chats by creation or by
    activity.
    """
    timestamps = [m.get("created_at") for m in messages if m.get("created_at")]
    first = min(timestamps) if timestamps else None
    last = max(timestamps) if timestamps else None

    duration_minutes = None
    if first and last:
        start, end = _parse_iso(first), _parse_iso(last)
        if start and end:
            duration_minutes = round((end - start).total_seconds() / 60.0, 2)

    total_chars = sum(r["char_count"] for r in message_rows)

    return {
        "day": day_of(chat.get("created_at")),
        "chat_id": chat_id,
        "user_id": chat.get("account_uuid") or chat.get("user_id"),
        "user_email": chat.get("account_email") or chat.get("email"),
        "organization_uuid": chat.get("organization_uuid"),
        "organization_name": chat.get("organization_name"),
        "chat_name": chat.get("name") or chat.get("title"),
        "chat_model": chat.get("model"),
        "model_family": family,
        "project_id": chat.get("project_uuid") or chat.get("project_id"),
        "created_at": chat.get("created_at"),
        "updated_at": chat.get("updated_at"),
        "message_count": len(message_rows),
        "user_msg_count": sum(1 for r in message_rows if r["role"] == "user"),
        "assistant_msg_count": sum(1 for r in message_rows if r["role"] == "assistant"),
        "block_count": len(block_rows),
        "tool_call_count": sum(1 for b in block_rows if b["block_type"] == "tool_use"),
        "tool_error_count": sum(1 for b in block_rows if b["is_error"]),
        "truncated_block_count": sum(1 for b in block_rows if b["truncated"]),
        "total_chars": total_chars,
        "est_tokens": est_tokens(total_chars),
        "first_message_at": first,
        "last_message_at": last,
        "duration_minutes": duration_minutes,
        "is_multi_turn": len(message_rows) > 2,
        # A chat with a title and no messages. Not a bug in this parser: it is a
        # real export artifact worth reporting as a data-governance signal, and
        # a well-known way to inflate any count taken from titles.
        "is_husk": len(message_rows) == 0,
    }


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# the parse
# --------------------------------------------------------------------------

def parse_all(root: Path, *, progress_every: int = 250) -> dict[str, Any]:
    """Rebuild every derived content table from the raw store."""
    chats: list[JsonObject] = []
    messages: list[JsonObject] = []
    blocks: list[JsonObject] = []
    attachments: list[JsonObject] = []

    seen = 0
    for chat_id, chat, raw_messages in rawstore.iter_chats(root):
        if chat is None:
            continue
        seen += 1
        family = model_family(chat.get("model"))

        # Order by created_at then id so turn_index is stable across re-parses.
        # An unstable turn_index would make every verdict address drift.
        raw_messages.sort(key=lambda m: (str(m.get("created_at") or ""), str(m.get("uuid") or m.get("id") or "")))

        chat_messages: list[JsonObject] = []
        chat_blocks: list[JsonObject] = []
        for turn_index, message in enumerate(raw_messages):
            message_blocks = build_blocks(chat, message, chat_id, family)
            chat_blocks.extend(message_blocks)
            chat_messages.append(build_message(chat, message, chat_id, turn_index, family, message_blocks))
            attachments.extend(build_attachments(chat, message, chat_id))

        messages.extend(chat_messages)
        blocks.extend(chat_blocks)
        chats.append(build_chat(chat, chat_id, raw_messages, chat_messages, chat_blocks, family))

        if progress_every and seen % progress_every == 0:
            print(f"[parse] {seen} chats, {len(blocks)} blocks", file=sys.stderr)

    written = {
        DATASET_CHATS: lake.write_rows(root, DATASET_CHATS, chats),
        DATASET_MESSAGES: lake.write_rows(root, DATASET_MESSAGES, messages),
        DATASET_BLOCKS: lake.write_rows(root, DATASET_BLOCKS, blocks),
        DATASET_ATTACHMENTS: lake.write_rows(root, DATASET_ATTACHMENTS, attachments),
    }

    index_rows = write_payload_index(root, blocks, attachments)

    state = lake.read_state(root)
    for dataset, rows in (
        (DATASET_CHATS, chats),
        (DATASET_MESSAGES, messages),
        (DATASET_BLOCKS, blocks),
        (DATASET_ATTACHMENTS, attachments),
    ):
        lake.set_dataset_state(
            state, dataset,
            last_parse=_now(), rows_last_parse=len(rows),
            partitions_total=len(lake.list_partitions(root, dataset)),
        )
    lake.write_state(root, state)

    return {
        "chats": len(chats),
        "husks": sum(1 for c in chats if c["is_husk"]),
        "messages": len(messages),
        "blocks": len(blocks),
        "attachments": len(attachments),
        "payload_index_rows": index_rows,
        "by_surface": _surface_counts(blocks),
        "error_blocks": sum(1 for b in blocks if b["is_error"]),
        "truncated_blocks": sum(1 for b in blocks if b["truncated"]),
        "partitions": {k: len(v) for k, v in written.items()},
    }


def _surface_counts(blocks: list[JsonObject]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in blocks:
        surface = block.get("surface")
        if surface:
            counts[surface] = counts.get(surface, 0) + 1
    return counts


def write_payload_index(root: Path, blocks: list[JsonObject], attachments: list[JsonObject]) -> int:
    """The address book: one row per scannable item, no content.

    Every scan, every recompute, and every surface attribution joins against
    this file. It carries addresses and metadata only — no text — so it can be
    read freely at any stage without a content budget, and so a stage that only
    needs to count never has to open a payload.
    """
    path = Path(root) / "_reports" / PAYLOAD_INDEX
    path.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "block_id", "chat_id", "message_id", "block_idx", "surface", "block_type",
        "role", "day", "week", "tool_name", "integration", "char_len",
        "is_error", "truncated", "has_code",
    ]

    count = 0
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for block in blocks:
            if not block.get("surface"):
                continue  # assistant prose: not one of the four scan surfaces
            writer.writerow({
                "block_id": block["block_id"],
                "chat_id": block["chat_id"],
                "message_id": block["message_id"],
                "block_idx": block["block_idx"],
                "surface": block["surface"],
                "block_type": block["block_type"],
                "role": block["role"],
                "day": block["day"],
                "week": lake.week_key(block["day"]) if block["day"] else "",
                "tool_name": block["tool_name"] or "",
                "integration": block["integration"] or "",
                "char_len": block["char_len"],
                "is_error": int(bool(block["is_error"])),
                "truncated": int(bool(block["truncated"])),
                "has_code": int(bool(block["has_code"])),
            })
            count += 1

        # Attachments are addressed by message with no block index. Downstream,
        # a null block_idx *is* the attachment path — floors computed with and
        # without them differ by a user or two, so the basis must be stated
        # wherever a floor is published.
        for attachment in attachments:
            writer.writerow({
                "block_id": attachment["attachment_id"],
                "chat_id": attachment["chat_id"],
                "message_id": attachment["message_id"],
                "block_idx": "",
                "surface": SURFACE_ATTACHMENT,
                "block_type": attachment["kind"],
                "role": "",
                "day": attachment["day"],
                "week": lake.week_key(attachment["day"]) if attachment["day"] else "",
                "tool_name": "",
                "integration": "",
                "char_len": len(attachment["display_name"] or ""),
                "is_error": 0,
                "truncated": 0,
                "has_code": 0,
            })
            count += 1

    return count


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rebuild the content tables from the raw store. No API calls.",
    )
    ap.add_argument("--data-dir", default="data", help="lake root (default: data)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = Path(args.data_dir)
    if rawstore.count_chats(root) == 0:
        print(
            f"No raw store at {rawstore.raw_dir(root)}.\n"
            "Run the compliance pull first; this stage only re-parses what is already on disk.",
            file=sys.stderr,
        )
        return 2

    result = parse_all(root, progress_every=0 if args.quiet else 250)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
