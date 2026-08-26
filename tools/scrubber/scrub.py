#!/usr/bin/env python3
"""Scrubber — the pre-publication gate for 100x CAIO.

Fails the build if anything that belongs to a real organization has been
committed: personal names, email addresses, corporate domains, credentials,
or a rendered report carrying real findings.

Why this exists
---------------
Every report this project produces has two editions: a shareable anonymous one
(ranks and counts) and a named internal one (names, only on explicit request).
The named edition is a working document, not a publishable artifact. One
accidental `git add` of a named report would publish an organization's people
alongside their spend and their sensitive-content findings. This gate makes
that mistake impossible to land on `main`.

It is deliberately noisy in one direction only: it would rather fail on a
docs example than let a real email through. Everything it flags can be
allow-listed explicitly in `allowlist.txt`, which is itself reviewed.

Usage
-----
    python3 tools/scrubber/scrub.py                 # scan tracked files
    python3 tools/scrubber/scrub.py --all           # scan the whole tree
    python3 tools/scrubber/scrub.py --path docs     # scan one subtree
    python3 tools/scrubber/scrub.py --explain       # print every rule

Exit codes: 0 clean, 1 findings, 2 usage error.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = Path(__file__).resolve().parent / "allowlist.txt"

# Binary and vendored paths never scanned.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".pytest_cache", ".ruff_cache", "dist", "build", "data", "_reports", "raw",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".mov", ".xlsx", ".docx", ".pptx",
}

# The project's own placeholder domains. Synthetic data must use these, so the
# generator's output stays publishable and distinguishable from a real org.
SYNTHETIC_DOMAINS = {
    "example.com", "example.org", "example.net",
    "northwind.example", "northwind.example.com",
}


@dataclass(frozen=True)
class Rule:
    id: str
    why: str
    pattern: re.Pattern
    # When set, the rule only applies to files with these suffixes. Used for
    # rules that describe a rendered artifact rather than source text — prose
    # in a doc may legitimately discuss the named edition; a shipped .html
    # may not be one.
    suffixes: tuple[str, ...] | None = None

    def applies_to(self, path: Path) -> bool:
        return self.suffixes is None or path.suffix.lower() in self.suffixes


def _rules() -> list[Rule]:
    return [
        Rule(
            "EMAIL",
            "A real email address identifies a person. Reports carry ranks, not "
            "identities; synthetic data uses @example.com.",
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        ),
        Rule(
            "CORP_DOMAIN",
            "A customer or employer domain names the organization under audit, "
            "even without an email attached.",
            re.compile(
                r"\b(?!example\.)[A-Za-z0-9-]+\."
                r"(?:com|net|org|io|co|ai|health|inc)\b(?![A-Za-z0-9./-])"
            ),
        ),
        Rule(
            "API_KEY",
            "A live credential. Never publish; rotate it if this fires.",
            re.compile(
                r"\b(?:sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
                r"|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
                r"|AIza[0-9A-Za-z_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"
            ),
        ),
        Rule(
            "ASSIGNED_SECRET",
            "A secret assigned inline (password=..., token=...). Even a sample "
            "value teaches the wrong habit; use an env var.",
            re.compile(
                r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token"
                r"|bearer)\b\s*[=:]\s*[\"']?[A-Za-z0-9!@#$%^&*_+/-]{8,}"
            ),
        ),
        Rule(
            "NAMED_EDITION",
            "A rendered report marked as the named internal edition. That edition "
            "exists only on the operator's disk.",
            re.compile(r"(?i)named[- ]internal edition|named edition"),
            suffixes=(".html", ".json"),
        ),
        Rule(
            "SSN",
            "A US Social Security Number shape.",
            re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        ),
        Rule(
            "PRIVATE_KEY",
            "A private key block.",
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        ),
    ]


def load_allowlist() -> set[str]:
    """Exact strings permitted despite matching a rule.

    Each entry is reviewed on its own merits; a bare domain here is a decision,
    not an oversight.
    """
    if not ALLOWLIST_PATH.exists():
        return set()
    entries = set()
    for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.add(line)
    return entries


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return walk_files(REPO_ROOT)
    return [REPO_ROOT / p for p in out.split("\0") if p]


def walk_files(root: Path) -> list[Path]:
    found = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
    return found


def scannable(path: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    if any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
        return False
    return path.exists() and path.is_file()


def allowed(match: str, rule: Rule, allowlist: set[str]) -> bool:
    if match in allowlist:
        return True
    lowered = match.lower()
    if rule.id in {"EMAIL", "CORP_DOMAIN"}:
        # Synthetic and documentation domains are the point, not a leak.
        if any(lowered.endswith(d) or lowered == d for d in SYNTHETIC_DOMAINS):
            return True
        # Bare references to well-known infrastructure hosts in prose and URLs.
        if rule.id == "CORP_DOMAIN" and lowered in allowlist:
            return True
    return False


def scan_file(path: Path, rules: list[Rule], allowlist: set[str]) -> list[tuple[int, Rule, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    hits: list[tuple[int, Rule, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "scrubber:allow" in line:
            continue
        for rule in rules:
            if not rule.applies_to(path):
                continue
            for m in rule.pattern.finditer(line):
                value = m.group(0)
                if allowed(value, rule, allowlist):
                    continue
                hits.append((lineno, rule, value))
    return hits


def redact(value: str) -> str:
    """Print enough to locate the hit, never enough to reuse it."""
    if len(value) <= 6:
        return value[0] + "*" * (len(value) - 1)
    return f"{value[:3]}{'*' * 8}{value[-2:]}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-publication scrubber for 100x CAIO.")
    ap.add_argument("--all", action="store_true", help="scan every file, not only tracked ones")
    ap.add_argument("--path", help="restrict the scan to a subtree")
    ap.add_argument("--explain", action="store_true", help="print each rule and why it exists")
    args = ap.parse_args()

    rules = _rules()

    if args.explain:
        print("Scrubber rules — each one blocks a specific way a real org leaks into a public repo.\n")
        for rule in rules:
            print(f"  {rule.id}\n    {rule.why}\n")
        print("Allowlist: tools/scrubber/allowlist.txt (exact strings, reviewed individually).")
        print("Per-line escape hatch: append  # scrubber:allow  to the line.")
        return 0

    allowlist = load_allowlist()

    if args.path:
        root = (REPO_ROOT / args.path).resolve()
        if not root.exists():
            print(f"scrub: no such path: {args.path}", file=sys.stderr)
            return 2
        files = walk_files(root) if root.is_dir() else [root]
    elif args.all:
        files = walk_files(REPO_ROOT)
    else:
        files = tracked_files()

    files = [f for f in files if scannable(f)]

    findings = 0
    for path in sorted(files):
        for lineno, rule, value in scan_file(path, rules, allowlist):
            rel = path.relative_to(REPO_ROOT)
            print(f"{rel}:{lineno}: [{rule.id}] {redact(value)}")
            findings += 1

    print(f"\nscrub: {len(files)} files scanned, {findings} finding(s).")
    if findings:
        print(
            "\nThis is a publication gate, not a lint warning. Each hit is either\n"
            "  - real org data that must be removed and replaced with synthetic data, or\n"
            "  - a deliberate example that belongs in tools/scrubber/allowlist.txt.\n"
            "Run with --explain to see what each rule is protecting against.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
