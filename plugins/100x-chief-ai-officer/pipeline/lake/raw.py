#!/usr/bin/env python3
"""The raw store — every chat's full API response, written before anything parses it.

Why this module exists
----------------------
The first version of this pipeline flattened each message straight into a
"content_text" column and discarded everything else. That silently dropped every
tool call, every tool result, and every attachment reference — and because the
raw responses were never saved, the loss was unrecoverable. Recovering them cost
a full re-pull of the entire corpus.

That is the single most expensive mistake this project has made, so the rule is
now structural rather than advisory: **the fetcher writes raw to disk first, and
only then parses.** Every derived table (`dim_chat`, `fact_message`,
`fact_block`, `fact_attachment`, `payload_index`) is rebuildable from this store
with zero API calls. When a parsing bug is found — and one will be — the fix is
a local re-parse, not another week of pagination against a rate-limited API.

The layout is one file per chat so a re-parse can stream chat-by-chat without
holding the corpus in memory, and so a single corrupt chat cannot poison the set.

    data/raw/compliance/<chat_id>.jsonl
        line 1  : {"_record": "chat", ...}       the chat envelope as returned
        line 2..: {"_record": "message", ...}    each message as returned, in order

What this store contains
------------------------
Full conversation content: prompts people typed, what tools were asked, what
tools returned, and error payloads. It is the most sensitive artifact this
project creates. It is git-ignored by construction, and the fetcher will not
write it without explicit consent (see `pipeline.fetch.consent`). Treat the
directory as you would a database of the same content, because that is what it is.

A note on truncation: the source caps individual tool payloads at roughly 10,060
characters. Raw is therefore *complete as returned*, not complete as it existed —
anything past the cut never left the vendor. Counts derived from it are floors.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

RAW_ROOT = "raw"
RAW_COMPLIANCE = "compliance"

JsonObject = dict[str, Any]

# The vendor's per-payload cap. Anything at or above this length was cut at the
# source; the parser flags it so downstream counts can state the caveat.
SOURCE_TRUNCATION_CHARS = 10_060


def raw_dir(root: Path) -> Path:
    """The compliance raw store under a lake root."""
    return Path(root) / RAW_ROOT / RAW_COMPLIANCE


def _safe_name(chat_id: str) -> str:
    """A filesystem-safe filename for a chat id.

    Chat ids are vendor-issued and well-behaved in practice, but a raw store is
    written from network input; a stray separator must never escape the
    directory.
    """
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(chat_id))
    return cleaned[:200] or "unnamed"


def chat_path(root: Path, chat_id: str) -> Path:
    return raw_dir(root) / f"{_safe_name(chat_id)}.jsonl"


def exists(root: Path, chat_id: str) -> bool:
    return chat_path(root, chat_id).exists()


def write_chat(root: Path, chat_id: str, chat: JsonObject, messages: list[JsonObject]) -> Path:
    """Persist one chat's full API response. Overwrites any prior copy.

    Written atomically via a temp file and rename: a run interrupted mid-write
    leaves the previous complete copy in place rather than a half file that a
    later parse would silently read as a short chat.
    """
    path = chat_path(root, chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")

    with tmp.open("w", encoding="utf-8") as f:
        envelope = {"_record": "chat", "_captured_at": _now(), **chat}
        f.write(json.dumps(envelope, default=str, separators=(",", ":")) + "\n")
        for message in messages:
            row = {"_record": "message", **message}
            f.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")

    os.replace(tmp, path)
    return path


def read_chat(root: Path, chat_id: str) -> tuple[JsonObject | None, list[JsonObject]]:
    """Read one chat back: (chat envelope, messages in stored order)."""
    path = chat_path(root, chat_id)
    if not path.exists():
        return None, []
    return _read_path(path)


def _read_path(path: Path) -> tuple[JsonObject | None, list[JsonObject]]:
    chat: JsonObject | None = None
    messages: list[JsonObject] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # One malformed line must not lose the rest of the chat.
                continue
            if row.get("_record") == "chat":
                chat = row
            elif row.get("_record") == "message":
                messages.append(row)
    return chat, messages


def iter_chats(root: Path) -> Iterator[tuple[str, JsonObject | None, list[JsonObject]]]:
    """Stream every stored chat as (chat_id, chat, messages).

    Streaming rather than returning a list: the corpus runs to hundreds of
    megabytes and every consumer of this function processes chat-by-chat.
    """
    base = raw_dir(root)
    if not base.is_dir():
        return
    for path in sorted(base.glob("*.jsonl")):
        chat, messages = _read_path(path)
        chat_id = str((chat or {}).get("uuid") or (chat or {}).get("id") or path.stem)
        yield chat_id, chat, messages


def count_chats(root: Path) -> int:
    base = raw_dir(root)
    return len(list(base.glob("*.jsonl"))) if base.is_dir() else 0


def store_size_bytes(root: Path) -> int:
    base = raw_dir(root)
    if not base.is_dir():
        return 0
    return sum(p.stat().st_size for p in base.glob("*.jsonl"))


def purge(root: Path) -> dict[str, Any]:
    """Delete the entire raw store.

    Offered as a first-class operation because keeping conversation content on
    disk is a retention decision an organization must be able to reverse. The
    derived tables survive; only the ability to re-parse without re-pulling is
    lost.
    """
    base = raw_dir(root)
    before = {"chats": count_chats(root), "bytes": store_size_bytes(root)}
    if base.is_dir():
        shutil.rmtree(base)
    return {"purged": before, "path": str(base)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
