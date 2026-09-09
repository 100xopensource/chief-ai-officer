#!/usr/bin/env python3
"""What to say to somebody who has just installed this and knows nothing about it.

The problem this solves
-----------------------
Installing the plugin used to be silent. Nothing appeared, nothing ran, and the
five skills only surface once you already know enough to ask for them. Somebody
who installed it because a colleague said "this tells you what Claude is costing
us" was left at a prompt with no idea that the next move was theirs.

So this is the one place that answers three questions in plain words: what this
is, what it has and has not already done, and what the two sensible next moves
are. It is used from two directions — the session-start hook that fires the
first time after installation, and `caio welcome` for anyone who wants it again.

Rules this file lives by
------------------------
**Standard library only, and no imports from the rest of the pipeline.** The
hook runs before anything is installed — that is the whole point of it — so
importing `pandas`, or a stage module that imports `pandas`, would make the
welcome fail in exactly the situation it exists for.

**Say only what is true of this machine right now.** "Run `caio demo`" is wrong
advice if `caio` is not installed yet, and "look at the examples in
`docs/mock-reports/`" is wrong advice if the plugin was installed from the
marketplace and there is no checkout on disk. Both mistakes waste the first ten
minutes, which is the failure this project keeps finding in its own docs. So the
state is measured, and the wording follows the state.

**Never claim to have done anything.** Installing this reads nothing and pulls
nothing. The message says so, because the reasonable fear on installing a thing
that reads your company's Claude history is that it has already read it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]

REPO_URL = "https://github.com/100xopensource/chief-ai-officer"
EXAMPLES_URL = f"{REPO_URL}/tree/main/docs/mock-reports"

# Where a checkout keeps things, relative to the repository root.
EXAMPLES_DIR = Path("docs/mock-reports")
PLUGIN_DIR = Path("plugins/100x-chief-ai-officer")

# Folder names the README and the skills tell people to use. Checked so the
# message can say "you already have one" instead of telling somebody to build
# a second lake next to the one they built yesterday. A lake is recognised by
# its `_state.json` rather than by the folder existing, because an empty
# `data/` is what you get from a pull that failed on the first call.
LAKE_NAMES = ("data", "data-demo")
LAKE_MARKER = "_state.json"


# --------------------------------------------------------------------------
# What is actually on this machine

def find_repo_root(start: Path) -> Path | None:
    """The checkout this is running inside, if it is running inside one.

    Two things must both be present, because either alone is a common false
    positive: `pyproject.toml` matches any Python project, and a `plugins/`
    directory matches any other plugin repository.
    """
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / PLUGIN_DIR).is_dir():
            return candidate
    return None


def command_is_available() -> bool:
    """Can the reader type `caio` and have something happen?

    Two ways to be sure, and both are needed. `which` misses the common case of
    a virtualenv that is installed but not activated — the console script exists
    at `.venv/bin/caio`, PATH does not mention it, and telling that person to
    `pip install` again is nonsense. So the first check is how this very process
    was started: if it was started *as* `caio`, the command plainly works.

    The hook has no such luck — it is started as a script path — so there PATH
    is the honest proxy for "can they type it".
    """
    if Path(sys.argv[0]).name == "caio":
        return True
    return shutil.which("caio") is not None


def detect(cwd: Path | None = None) -> JsonObject:
    """Measure the situation, so the wording can follow it."""
    cwd = (cwd or Path.cwd()).resolve()
    root = find_repo_root(cwd)

    lakes = [name for name in LAKE_NAMES if (cwd / name / LAKE_MARKER).is_file()]

    reports: list[str] = []
    for directory in (cwd / "_reports", cwd / "reports"):
        if directory.is_dir():
            reports += sorted(path.name for path in directory.glob("*.html"))

    examples = root / EXAMPLES_DIR if root else None

    return {
        "repo_root": str(root) if root else None,
        "installed": command_is_available(),
        "examples_dir": str(examples) if examples and examples.is_dir() else None,
        "lakes": lakes,
        "reports": reports,
    }


# --------------------------------------------------------------------------
# What to say about it

def _run(state: JsonObject, command: str) -> str:
    """A command line the reader can paste, given how the package is reachable.

    Installed, it is `caio`. In a checkout that has not been installed yet, the
    same thing is a module path with `PYTHONPATH` in front of it. Printing the
    first to somebody who has only the second is the single most common way a
    first run fails.
    """
    if state.get("installed"):
        return f"caio {command}"
    if state.get("repo_root"):
        return f"PYTHONPATH={PLUGIN_DIR} python3 -m pipeline.cli {command}"
    return f"caio {command}"


def what_it_is() -> list[str]:
    return [
        "**100x Chief AI Officer** reads the Claude usage records your company already has, and",
        "writes three reports about them:",
        "",
        "| Report | Written for | The question it answers |",
        "|---|---|---|",
        "| **Waste Ledger** | Finance | What did it cost, and where did money go that bought nothing? |",
        "| **Value X-Ray** | whoever owns AI | What did the spending actually buy, and what is getting in the way? |",
        "| **Exposure Report** | Compliance | What sensitive material reached the AI, and how did it get there? |",
        "",
        "Each report is a single web page in a single file. You open it in a browser,",
        "or email it to someone as an attachment — there is nothing to install and",
        "nothing to log into at the other end.",
        "",
        "Everything runs on this machine. Nothing is uploaded anywhere.",
    ]


def nothing_has_happened() -> list[str]:
    return [
        "**Nothing has happened yet.** Installing this read nothing and pulled",
        "nothing. It does not touch your company's data until you ask it to, and",
        "reading what people actually wrote needs a second key and a consent record",
        "on top of that.",
    ]


def look_at_an_example(state: JsonObject) -> list[str]:
    """Offer one: see a finished report. No keys, no company data, no setup."""
    out = ["### 1. Look at a finished report", "",
           "No keys, no company data, nothing to set up. Every number, person and",
           "company in these is invented."]

    examples = state.get("examples_dir")
    if examples:
        out += [
            "",
            f"Three of them are already on disk, at `{EXAMPLES_DIR}/`. Open any",
            "`.html` file in a browser.",
        ]
    else:
        out += ["", f"Three of them are here: {EXAMPLES_URL}"]

    out += ["", "Or invent a company and produce your own, which takes about two minutes:", ""]

    if state.get("installed"):
        # Installed: the two commands, and nothing in front of them.
        out += ["```bash", "caio demo --out data-demo",
                "caio all  --data-dir data-demo --out-dir _reports", "```"]
    elif state.get("repo_root"):
        # A checkout that has not been installed. Show the installed form,
        # because that is what the second line will be once the first has run —
        # printing the un-installed form under an install step contradicts it.
        out += ["```bash", "pip install -e .          # once, from the repository root",
                "caio demo --out data-demo",
                "caio all  --data-dir data-demo --out-dir _reports", "```",
                "",
                "Rather not install anything? Every command also works as",
                f"`PYTHONPATH={PLUGIN_DIR} python3 -m pipeline.cli …` from the",
                "repository root."]
    else:
        # No checkout on this machine at all, so the first step is getting one.
        out += ["```bash", f"git clone {REPO_URL}.git", "cd 100x-chief-ai-officer",
                "pip install -e .", "", "caio demo --out data-demo",
                "caio all  --data-dir data-demo --out-dir _reports", "```"]
    return out


def connect_your_own_data(state: JsonObject) -> list[str]:
    """Offer two: the real thing. Deliberately vague on commands — ask instead."""
    return [
        "### 2. Get set up with your own data",
        "",
        "This needs one or both of two keys from whoever administers your company's",
        "Claude account:",
        "",
        "- an **Admin API key** — the cost and usage numbers. Two of the three reports.",
        "- a **Compliance API key** — what people actually wrote. The Exposure Report only.",
        "",
        "Either works on its own; you do not need both to start.",
        "",
        "Ask me to *walk you through setup* and I will do it a step at a time,",
        "including what to say when you ask your administrator for a key.",
    ]


def where_you_are(state: JsonObject) -> list[str]:
    """Say what is already here, so nobody is told to build a second one."""
    out: list[str] = []
    lakes = state.get("lakes") or []
    reports = state.get("reports") or []

    if reports:
        shown = ", ".join(f"`{name}`" for name in reports[:3])
        more = f" (and {len(reports) - 3} more)" if len(reports) > 3 else ""
        out += [f"You already have reports built here: {shown}{more}. Open one in a",
                "browser, or ask me to explain any number in it."]
    elif lakes:
        joined = ", ".join(f"`{name}`" for name in lakes)
        out += [f"There is already data pulled here, in {joined}. To build the reports",
                f"from it: `{_run(state, 'all --data-dir ' + lakes[0] + ' --out-dir _reports')}`."]

    return out


def message(state: JsonObject | None = None) -> str:
    """The whole thing, in Markdown, for a person to read."""
    state = state if state is not None else detect()

    blocks: list[list[str]] = [what_it_is()]

    here = where_you_are(state)
    if here:
        blocks.append(here)

    blocks += [
        ["## Two things you can do now", "",
         "Most people want the first one first."],
        look_at_an_example(state),
        connect_your_own_data(state),
        nothing_has_happened(),
    ]

    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


def brief_for_claude(state: JsonObject | None = None) -> str:
    """The same thing, addressed to Claude rather than to the reader.

    A session-start hook's output is context, not a printed page: Claude reads
    it and then talks to the person. So this says what to offer and how to
    behave, and leaves the wording to the conversation — a hook that hands over
    a wall of Markdown to be recited reads like a recital.
    """
    state = state if state is not None else detect()
    lines = [
        "The 100x Chief AI Officer plugin was just installed and this is the first session "
        "since. The person may not know what it does.",
        "",
        "Greet them briefly and in plain English — no jargon, no stage names, no "
        "pipeline vocabulary. Cover, in a few sentences:",
        "",
        "- what it is: it reads the Claude usage records their company already has "
        "and writes three reports — one for Finance on cost and waste, one for "
        "whoever owns AI on what the spending bought, one for Compliance on what "
        "sensitive material reached the AI. Each is a single HTML file they can "
        "email. Everything runs locally; nothing is uploaded.",
        "- that nothing has happened yet: installing it read nothing and pulled "
        "nothing, and it will not touch company data until they ask.",
        "- then offer exactly two next steps and stop:",
        "  1. look at a finished example report — no keys, no company data, nothing "
        "to set up;",
        "  2. get set up with their own data — needs a key from whoever administers "
        "their company's Claude account.",
        "",
        "Do not run anything, pull anything or build anything until they pick. "
        "Use the caio-setup skill once they do.",
        "",
        "What is true of this machine right now:",
    ]

    if state.get("installed"):
        lines.append("- the `caio` command is installed and on PATH")
    elif state.get("repo_root"):
        lines.append("- not installed yet; there is a checkout at "
                     f"{state['repo_root']}, so `pip install -e .` from there, or "
                     f"`PYTHONPATH={PLUGIN_DIR} python3 -m pipeline.cli`")
    else:
        lines.append("- the `caio` command is not installed and there is no checkout "
                     f"in this directory. The repository is {REPO_URL}")

    if state.get("examples_dir"):
        lines.append(f"- example reports are already on disk at {EXAMPLES_DIR}/")
    else:
        lines.append(f"- example reports are not on disk here; they are at {EXAMPLES_URL}")

    lakes = state.get("lakes") or []
    reports = state.get("reports") or []
    if reports:
        lines.append(f"- reports have already been built here: {', '.join(reports[:5])}")
    if lakes:
        lines.append(f"- data has already been pulled here, in: {', '.join(lakes)}")
    if not lakes and not reports:
        lines.append("- no data has been pulled here and no reports have been built")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Explain what this plugin is and what to do next.")
    ap.add_argument("--for-claude", action="store_true",
                    help="write the session-start brief instead of the page a person reads")
    ap.add_argument("--json", action="store_true", help="write the measured state")
    ap.add_argument("--dir", type=Path, default=None,
                    help="measure this directory instead of the current one")
    args = ap.parse_args(argv)

    state = detect(args.dir)
    if args.json:
        print(json.dumps(state, indent=2))
    elif args.for_claude:
        print(brief_for_claude(state), end="")
    else:
        print(message(state), end="")
    return 0


if __name__ == "__main__":
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.exit(main())
