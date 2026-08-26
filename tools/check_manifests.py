#!/usr/bin/env python3
"""Check the plugin manifests and skill definitions before publication.

Both classes of file fail silently, which is why this exists as a gate rather
than as something anyone remembers to look at:

  · A malformed `marketplace.json` is rejected at publication time, after the
    announcement has gone out.
  · A skill whose frontmatter does not parse simply never loads. Nothing errors.
    The plugin installs, the skill is listed nowhere, and the first person to
    notice is a user who wonders why nothing happened.

So this reads every manifest and every SKILL.md and complains in specific terms.
Standard library only, so it runs anywhere CI can run Python.

Usage
-----
    python3 tools/check_manifests.py
    python3 tools/check_manifests.py --root . --verbose
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]

REQUIRED_PLUGIN_FIELDS = ("name", "description", "version")
REQUIRED_MARKETPLACE_FIELDS = ("name", "owner", "plugins")
REQUIRED_SKILL_FIELDS = ("name", "description")

# A skill is selected by its description alone. One that describes what the
# skill *is* without saying when to use it never gets picked, so the length is
# a real requirement rather than a style rule.
MIN_DESCRIPTION_CHARS = 40

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+].*)?$")


class Problems:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.notes: list[str] = []

    def error(self, where: Path | str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def note(self, message: str) -> None:
        self.notes.append(message)


def load_json(path: Path, problems: Problems) -> JsonObject | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        problems.error(path, f"not valid JSON — {exc}")
    except OSError as exc:
        problems.error(path, f"cannot be read — {exc}")
    return None


# --------------------------------------------------------------------------

def check_plugin(path: Path, problems: Problems) -> str | None:
    manifest = load_json(path, problems)
    if manifest is None:
        return None

    for field in REQUIRED_PLUGIN_FIELDS:
        if not manifest.get(field):
            problems.error(path, f"missing required field '{field}'")

    name = manifest.get("name")
    if name and not NAME_RE.match(str(name)):
        problems.error(path, f"name '{name}' must be lowercase words joined by hyphens")

    version = manifest.get("version")
    if version and not SEMVER_RE.match(str(version)):
        problems.error(path, f"version '{version}' is not a version number like 2.0.0")

    # The plugin directory's name and the manifest's name are matched by the
    # loader. When they disagree the plugin loads under a name nobody expects.
    directory = path.parent.parent.name
    if name and directory != name:
        problems.error(path, f"name '{name}' does not match its directory '{directory}'")

    problems.note(f"plugin {name} v{version}")
    return str(name) if name else None


def check_marketplace(path: Path, problems: Problems,
                      known_plugins: set[str]) -> None:
    manifest = load_json(path, problems)
    if manifest is None:
        return

    for field in REQUIRED_MARKETPLACE_FIELDS:
        if not manifest.get(field):
            problems.error(path, f"missing required field '{field}'")

    owner = manifest.get("owner")
    if isinstance(owner, dict) and not owner.get("name"):
        problems.error(path, "owner has no name")

    entries = manifest.get("plugins")
    if not isinstance(entries, list) or not entries:
        problems.error(path, "'plugins' must be a non-empty list")
        return

    for index, entry in enumerate(entries):
        where = f"{path} → plugins[{index}]"
        if not isinstance(entry, dict):
            problems.error(where, "must be an object")
            continue
        for field in ("name", "source", "description"):
            if not entry.get(field):
                problems.error(where, f"missing '{field}'")

        source = str(entry.get("source") or "")
        if source.startswith("./"):
            target = (path.parent.parent / source[2:]).resolve()
            if not target.is_dir():
                problems.error(where, f"source '{source}' is not a directory")
            elif not (target / ".claude-plugin" / "plugin.json").exists():
                problems.error(where, f"source '{source}' has no .claude-plugin/plugin.json")

        name = str(entry.get("name") or "")
        if name and known_plugins and name not in known_plugins:
            problems.error(where, f"lists '{name}', which is not a plugin in this repository")

    problems.note(f"marketplace lists {len(entries)} plugin(s)")


def check_hooks(path: Path, problems: Problems) -> None:
    """A hooks file fails the same silent way a skill does.

    If it does not parse, or names a script that is not there, the plugin still
    installs and the hook simply never fires. Nothing errors, so the first
    person to notice is a user who installed this and was told nothing.
    """
    config = load_json(path, problems)
    if config is None:
        return

    events = config.get("hooks")
    if not isinstance(events, dict) or not events:
        problems.error(path, "'hooks' must be a non-empty object keyed by event name")
        return

    for event, groups in events.items():
        if not isinstance(groups, list) or not groups:
            problems.error(path, f"{event}: must be a non-empty list")
            continue
        for group in groups:
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if not isinstance(hook, dict) or not hook.get("type"):
                    problems.error(path, f"{event}: a hook has no 'type'")
                    continue
                if hook["type"] != "command":
                    continue
                command = str(hook.get("command") or "")
                if not command:
                    problems.error(path, f"{event}: a command hook has no command")
                    continue
                # The path is the only part that can be checked from here, and
                # it is the part that breaks when a file is renamed.
                for token in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}[^\s\"']*", command):
                    relative = token.replace("${CLAUDE_PLUGIN_ROOT}", "").lstrip("/")
                    if not (path.parent.parent / relative).is_file():
                        problems.error(path, f"{event}: command names '{relative}', "
                                             "which is not a file in this plugin")

    problems.note(f"hooks for {', '.join(sorted(events))}")


def parse_frontmatter(text: str) -> tuple[JsonObject | None, str | None]:
    """Read a SKILL.md's YAML frontmatter without a YAML dependency.

    Skill frontmatter is a flat block of `key: value` pairs, so a full YAML
    parser would be a dependency bought for nothing. Anything nested here is
    reported as an error rather than silently half-read.
    """
    if not text.startswith("---"):
        return None, "no frontmatter — a SKILL.md must open with a --- block"

    end = text.find("\n---", 3)
    if end == -1:
        return None, "frontmatter is never closed with ---"

    fields: JsonObject = {}
    for number, line in enumerate(text[3:end].strip("\n").split("\n"), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            return None, f"line {number}: nested frontmatter is not supported here"
        if ":" not in line:
            return None, f"line {number}: not a 'key: value' pair"
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields, None


def check_skill(path: Path, problems: Problems) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        problems.error(path, f"cannot be read — {exc}")
        return

    fields, error = parse_frontmatter(text)
    if fields is None:
        problems.error(path, error or "unreadable frontmatter")
        return

    for field in REQUIRED_SKILL_FIELDS:
        if not fields.get(field):
            problems.error(path, f"frontmatter is missing '{field}'")

    name = fields.get("name")
    if name:
        if not NAME_RE.match(name):
            problems.error(path, f"name '{name}' must be lowercase words joined by hyphens")
        if name != path.parent.name:
            problems.error(path, f"name '{name}' does not match its directory "
                                 f"'{path.parent.name}'")

    description = fields.get("description", "")
    if description and len(description) < MIN_DESCRIPTION_CHARS:
        problems.error(path, "description is too short to select on. Say when to use "
                             "the skill, not just what it is.")

    body = text[text.find("\n---", 3) + 4:].strip()
    if len(body) < 200:
        problems.error(path, "body is nearly empty — a skill with no instructions "
                             "loads and then does nothing")

    problems.note(f"skill {name}")


# --------------------------------------------------------------------------

def check(root: Path) -> Problems:
    problems = Problems()

    plugin_manifests = sorted(root.glob("plugins/*/.claude-plugin/plugin.json"))
    if not plugin_manifests:
        problems.error(root, "no plugins/*/.claude-plugin/plugin.json found")

    names: set[str] = set()
    for path in plugin_manifests:
        name = check_plugin(path, problems)
        if name:
            names.add(name)

    marketplace = root / ".claude-plugin" / "marketplace.json"
    if not marketplace.exists():
        problems.error(marketplace, "missing — the repository cannot be added as a "
                                    "marketplace without it")
    else:
        check_marketplace(marketplace, problems, names)

    for path in sorted(root.glob("plugins/*/hooks/hooks.json")):
        check_hooks(path, problems)

    skills = sorted(root.glob("plugins/*/skills/*/SKILL.md"))
    if not skills:
        problems.error(root, "no plugins/*/skills/*/SKILL.md found")
    for path in skills:
        check_skill(path, problems)

    # A skill directory with no SKILL.md is invisible, and looks fine on disk.
    for directory in sorted(root.glob("plugins/*/skills/*")):
        if directory.is_dir() and not (directory / "SKILL.md").exists():
            problems.error(directory, "skill directory has no SKILL.md, so it will "
                                      "never load")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Check plugin manifests and skill definitions.")
    ap.add_argument("--root", default=".", help="repository root (default: .)")
    ap.add_argument("--verbose", action="store_true", help="list everything checked")
    args = ap.parse_args()

    problems = check(Path(args.root).resolve())

    if args.verbose:
        for note in problems.notes:
            print(f"  ok     {note}")

    for error in problems.errors:
        print(f"  FAIL   {error}", file=sys.stderr)

    checked = len(problems.notes)
    print(f"\nmanifests: {checked} item(s) checked, {len(problems.errors)} problem(s).")
    if problems.errors:
        print("A malformed manifest is rejected at publication; a malformed skill "
              "silently never loads.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
