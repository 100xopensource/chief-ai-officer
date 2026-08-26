#!/usr/bin/env python3
"""Say what this plugin is, once, the first time a session starts after install.

Why a hook at all
-----------------
Installing the plugin was silent. The five skills only surface when somebody
already knows enough to ask for them, so a person who installed this because a
colleague said "it tells you what Claude is costing us" got a prompt and no
hint that the next move was theirs. This is the one thing that speaks first.

What it deliberately does not do
--------------------------------
**It runs nothing.** No pull, no scan, no report. Installing a tool that reads
your company's Claude history and having it immediately do so is the outcome
this whole project is built to avoid, and a hook is exactly where that mistake
would be easy to make.

**It speaks once.** A greeting on every session start is an advert. The marker
file below is how it remembers. Set `CAIO_WELCOME=always` to see it every time
while working on it, or `CAIO_WELCOME=never` to silence it; `caio welcome`
prints the same thing on demand, forever.

**It never breaks a session.** Every failure path here exits 0 with no output.
A plugin whose greeting can stop somebody from starting work is worse than a
plugin with no greeting, so the bar is: if anything at all goes wrong, say
nothing and get out of the way.

**It does not depend on the package being installed.** That is the whole point
of it — the hook fires before anybody has run `pip install`. So it imports one
standard-library-only module by path and nothing else.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MARKER_NAME = "welcomed.json"

# Short enough to read in the transcript without scrolling. The detail goes to
# Claude as context; this line is only so the person sees that something is
# there and did not run.
SYSTEM_MESSAGE = ("100x Chief AI Officer is installed. It has not read or pulled anything — "
                  "ask for an example report, or for help setting it up.")


def plugin_root() -> Path:
    """Where the plugin lives, whether or not Claude Code told us."""
    told = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if told:
        return Path(told)
    return Path(__file__).resolve().parent.parent


def marker_path() -> Path:
    """A file that says the greeting has been given.

    `CLAUDE_PLUGIN_DATA` is the directory Claude Code keeps for a plugin across
    updates, which is exactly the lifetime wanted here. Without it, fall back to
    the usual cache location rather than writing inside the plugin directory —
    a plugin that modifies its own install is a plugin that loses the file on
    upgrade.
    """
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if data:
        return Path(data) / MARKER_NAME
    cache = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(cache) / "100x-chief-ai-officer" / MARKER_NAME


def should_speak() -> bool:
    setting = (os.environ.get("CAIO_WELCOME") or "").strip().lower()
    if setting == "always":
        return True
    if setting == "never":
        return False
    return not marker_path().exists()


def remember() -> None:
    """Record that it has been said. Failure to record is not worth an error."""
    path = marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "welcomed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "Delete this file, or set CAIO_WELCOME=always, to see the "
                    "100x Chief AI Officer welcome again. `caio welcome` prints it any time.",
        }, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_welcome():
    """Import the welcome text from the plugin, without needing it installed."""
    root = plugin_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from pipeline import welcome  # noqa: PLC0415 — deliberately late and path-dependent
    return welcome


def main() -> int:
    # The payload is read and discarded: the matcher in hooks.json already
    # restricts this to a fresh session, and nothing else in it changes what is
    # said. Reading it anyway keeps the stream from filling if Claude Code
    # writes more than the pipe holds.
    try:
        sys.stdin.read()
    except OSError:
        pass

    if not should_speak():
        return 0

    welcome = load_welcome()
    where = os.environ.get("CLAUDE_PROJECT_DIR")
    state = welcome.detect(Path(where) if where else None)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": welcome.brief_for_claude(state),
        },
        "systemMessage": SYSTEM_MESSAGE,
    }))
    remember()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Nothing this file can fail at is worth interrupting somebody's work.
        sys.exit(0)
