#!/usr/bin/env python3
"""The one entry point. Everything else is a stage this calls in order.

    caio all --data-dir data --out-dir _reports

That single command runs the whole thing: check what the data can answer,
compute each report's numbers, lock the findings against previous runs, render
three HTML files, and put every one through the publication gates. Add
`--deliver` and it also packages them with a cover note and checksums.

Why one command
---------------
The pipeline has six stages and each is independently runnable, which is right
for developing it and wrong for using it. Somebody running this on a Monday
morning should not have to know that the scan comes before the metrics, or that
the ledger has to be written before the report can say "third week running".
Ordering is the tool's job.

Every stage still runs on its own:

    python3 -m pipeline.stages.x0_gapcheck   --data-dir data
    python3 -m pipeline.stages.x2_scan       --data-dir data
    python3 -m pipeline.stages.x5_lock       --data-dir data --all
    python3 -m pipeline.render.compose       --data-dir data --report exposure
    python3 -m pipeline.render.deliver       report.html

What it will not do
-------------------
It will not pull data. Pulling needs credentials and touches a network, and
bundling that into the same command as "produce the reports" means a person
who wanted a report gets a network call they did not ask for. Pull explicitly:

    python3 -m pipeline.fetch.analytics  --data-dir data
    python3 -m pipeline.fetch.compliance --data-dir data

(Those stay as module paths deliberately: pulling touches a network and needs
credentials, and it should not look like the same kind of thing as `caio all`.)

It will not write a report that fails its gates. A stage that fails stops the
run for that report and says why; the other reports still finish, because one
blocked report is not a reason to withhold two good ones.

Commands
--------
    welcome    what this is and what to do next, in plain English
    demo       invent a company to try the tools on, with no credentials
    check      what this lake can answer, and what it is missing
    scan       sweep conversation content for sensitive-data patterns
    judge      read the flagged passages and judge them
    verify     independently re-read what the first pass called real
    report     build one or more reports
    deliver    package already-built reports for sending
    all        check, scan, report — the Monday-morning command
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline import welcome
from pipeline.render import build as render_build
from pipeline.render import compose as render_compose
from pipeline.render import deliver as render_deliver
from pipeline.render import validate as render_validate
from pipeline.stages import _window as W
from pipeline.stages import x0_gapcheck, x2_scan, x3_classify, x4_verify, x5_lock

JsonObject = dict[str, Any]

REPORTS = ("waste", "value", "exposure")


def invoked_as() -> str:
    """How this run was started, so a printed next step is one the reader can paste.

    The package is usable two ways — installed, as `caio`, or from a checkout as
    `python3 -m pipeline.cli`. Printing the wrong one is a small thing that
    wastes somebody's first ten minutes, which is exactly the failure this
    project keeps finding in its own documentation.
    """
    name = Path(sys.argv[0]).name
    return "caio" if name == "caio" else "python3 -m pipeline.cli"


def log(message: str) -> None:
    print(message, file=sys.stderr)


# --------------------------------------------------------------------------

def cmd_welcome(args: argparse.Namespace) -> int:
    """Say what this is and what the two next moves are.

    Deliberately does nothing else. Somebody running a command called `welcome`
    on a tool that reads their company's Claude history should not have it read
    anything.
    """
    state = welcome.detect()
    if getattr(args, "as_json", False):
        print(json.dumps(state, indent=2))
    elif getattr(args, "for_claude", False):
        print(welcome.brief_for_claude(state), end="")
    else:
        print(welcome.message(state), end="")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Build an invented company's lake, so the tools can be tried on nothing real.

    This is the first command most people run, so it is a first-class command
    rather than a module path somebody has to be told about.
    """
    from pipeline.demo import generate_synthetic_lake as synth
    from pipeline.fetch import parse_raw

    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)

    generator = synth.Generator(root, args.seed, args.weeks)
    log(f"Inventing {synth.ORG_NAME}: {args.weeks} weeks of history, seed {args.seed}.")

    generator.build_directory()
    generator.write_directory()
    log(f"  {len(generator.users)} seats")

    generator.write_analytics()
    log(f"  ${generator.total_spend:,.0f} of invented spend")

    generator.write_content(args.chats)
    parsed = parse_raw.parse_all(root, progress_every=0)
    log(f"  {args.chats:,} conversations, {parsed.get('messages', 0):,} messages")

    generator.write_state()
    log("")
    log(f"Done. Everything in {root} is invented — the company, the people, the numbers.")
    log(f"Now build the reports:  {invoked_as()} all --data-dir {root}")
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)
    run_id = args.run_id or x3_classify.latest_run(root)
    if not run_id:
        log("judge: no scan output. Run `scan` first.")
        return 1

    options: JsonObject = {"worksheet": args.worksheet}
    if getattr(args, "model", None):
        options["model"] = args.model

    result = x3_classify.classify(root, run_id, judge_name=args.judge,
                                  limit=args.limit, **options)
    if "error" in result:
        log(f"judge: {result['error']}")
        return 1

    if result.get("awaiting_review"):
        log(f"Wrote {result['worksheet']} — {result['awaiting_review']} passage(s) to read.")
        log("Fill in each VERDICT line, then run this again to read it back.")
        return 0

    counts = result["verdicts_this_run"]
    log(f"Read {result['judged_total']:,} of {result['candidates_total']:,} passages "
        f"({result['read_fraction_pct']}%) — {counts['real']:,} look real, "
        f"{counts['false_alarm']:,} false alarms, {counts['unsure']:,} unsure.")
    if args.judge == "demo":
        log("  Judged by the offline stand-in: nothing was actually read. "
            "Use --judge worksheet or --judge anthropic for a real reading.")
    log("  Nothing is confirmed until the verification pass agrees.")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)
    run_id = args.run_id or x3_classify.latest_run(root)
    if not run_id:
        log("verify: no scan output. Run `scan` first.")
        return 1

    options: JsonObject = {"worksheet": args.worksheet}
    if getattr(args, "model", None):
        options["model"] = args.model

    result = x4_verify.verify(root, run_id, judge_name=args.judge,
                              limit=args.limit, **options)
    if "error" in result:
        log(f"verify: {result['error']}")
        return 1

    statuses = result["statuses"]
    log(f"{statuses['confirmed']:,} confirmed, {statuses['disputed']:,} disputed, "
        f"{statuses['cleared']:,} cleared, {statuses['unverified']:,} still unread.")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)
    result = x0_gapcheck.gapcheck(root, W.resolve_as_of(root, args.as_of))
    print(json.dumps(result, indent=2) if args.json
          else x0_gapcheck.render_human(result))
    if not result["lake_exists"]:
        return 2
    return 1 if result["blocked"] else 0


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)
    if not W.dataset_exists(root, "fact_block"):
        log("scan: no parsed conversation content in this lake. Pull and parse first, "
            "or generate a synthetic lake to try the tools on.")
        return 1
    run_id = args.run_id or datetime.now(timezone.utc).date().isoformat()
    summary = x2_scan.scan(root, run_id)
    log(f"[scan] {summary['leads_total']:,} candidates from "
        f"{summary['blocks_scanned']:,} pieces of conversation. "
        "Candidates, not findings — nothing here is publishable unread.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    wanted = args.reports or list(REPORTS)

    gaps = x0_gapcheck.gapcheck(root, W.resolve_as_of(root, args.as_of))
    if not gaps["lake_exists"]:
        log(f"report: no lake at {root}. Nothing to report on.")
        return 2

    log(f"Window: {gaps['window']['week_label']} "
        f"({gaps['window']['week_start']} to {gaps['window']['week_end']})")

    built, refused, blocked = [], [], []
    for report in wanted:
        status = gaps["reports"][report]
        if status["status"] == "blocked":
            log(f"  skip   {status['title']}: {status['reader_note']}")
            blocked.append(report)
            continue

        try:
            # The ledger is written once per report per run, here — not in the
            # composer, so that composing a report twice to look at it does not
            # advance its "weeks seen" count.
            block = render_compose.compose(root, report,
                                           scope_tag=args.scope_tag,
                                           write_ledger=not args.dry_run)
        except Exception as exc:  # a stage that cannot compute says so and stops
            log(f"  FAIL   {report}: could not compute its numbers: {exc}")
            refused.append(report)
            continue

        out = render_build.default_out(report, block, out_dir)
        try:
            render_build.build(report, block, out)
        except render_build.BuildError as exc:
            log(f"  FAIL   {report}: {exc}")
            refused.append(report)
            continue

        result = render_validate.validate(out, out.with_suffix(".json"), args.edition)
        if result.failures:
            for gate, detail in result.failures:
                log(f"  FAIL   {report}: {gate}: {detail}")
            log(f"         {out} was written but does not pass its gates. "
                "It must not be shared until it does.")
            refused.append(report)
            continue

        count = len(block.get("findings") or [])
        log(f"  built  {out.name}  ·  {count} finding(s)  ·  "
            f"{len(result.passed)} gates passed")
        built.append(out)

    if args.deliver and built:
        log("")
        for path in built:
            try:
                delivered = render_deliver.deliver(
                    path, Path(args.deliver_dir), edition=args.edition,
                    recipient=args.to, sender=args.sender)
                log(f"  packaged  {Path(delivered['report']).name} -> "
                    f"{delivered['delivered_to']}")
            except render_deliver.DeliveryRefused as exc:
                log(f"  FAIL   packaging: {exc}")
                refused.append(str(path))

    log("")
    log(f"{len(built)} report(s) built"
        + (f", {len(blocked)} blocked on missing data" if blocked else "")
        + (f", {len(refused)} refused" if refused else "")
        + ".")
    if built and not refused:
        log(f"Open one: {built[0]}")
    return 1 if refused else 0


def cmd_deliver(args: argparse.Namespace) -> int:
    refused = 0
    for name in args.reports:
        try:
            result = render_deliver.deliver(
                Path(name), Path(args.deliver_dir), edition=args.edition,
                recipient=args.to, sender=args.sender)
            log(f"  packaged  {Path(result['report']).name} -> {result['delivered_to']}")
        except render_deliver.DeliveryRefused as exc:
            log(f"  FAIL   {exc}")
            refused += 1
    return 1 if refused else 0


def cmd_all(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)

    log("── what this lake can answer ───────────────────────────────")
    gaps = x0_gapcheck.gapcheck(root, W.resolve_as_of(root, args.as_of))
    if not gaps["lake_exists"]:
        log(f"No lake at {root}.")
        how = invoked_as()
        log("Try the tools on an invented company first — no credentials needed:")
        log(f"    {how} demo --out data-demo")
        log(f"    {how} all --data-dir data-demo")
        return 2
    log(x0_gapcheck.render_human(gaps))

    if W.dataset_exists(root, "fact_block"):
        log("")
        log("── sweeping conversation content ───────────────────────────")
        cmd_scan(args)

    if getattr(args, "read", False) and W.dataset_exists(root, "fact_block"):
        log("")
        log("── reading the flagged passages ────────────────────────────")
        cmd_judge(args)
        log("")
        log("── verifying independently ─────────────────────────────────")
        cmd_verify(args)

    log("")
    log("── building reports ────────────────────────────────────────")
    return cmd_report(args)


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="pipeline.cli",
        description="100x Chief AI Officer — read your own Claude usage, produce three reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Start here. No credentials, no real data, two commands:\n"
            "    caio demo --out data-demo\n"
            "    caio all --data-dir data-demo --out-dir _reports\n"
        ),
    )
    sub = ap.add_subparsers(dest="command", required=True)

    def shared(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--data-dir", default="data", help="the lake (default: data)")
        parser.add_argument("--as-of", help="run date (YYYY-MM-DD); defaults to the day "
                                            "after the newest day of data in the lake")

    def output(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--out-dir", default="_reports", help="where reports are written")
        parser.add_argument("--edition", choices=["shareable", "named"], default="shareable",
                            help="'named' permits email addresses; every other gate still applies")
        parser.add_argument("--scope-tag", help="override the edition label in the masthead")
        parser.add_argument("--dry-run", action="store_true",
                            help="build without advancing the findings ledger")
        parser.add_argument("--deliver", action="store_true",
                            help="also package each report with a cover note and checksums")
        parser.add_argument("--deliver-dir", default="deliveries")
        parser.add_argument("--to", help="who the deliverable is going to")
        parser.add_argument("--from", dest="sender", help="who is sending it")

    p = sub.add_parser("welcome", help="what this is and what to do next, in plain English")
    p.add_argument("--for-claude", action="store_true",
                   help="write the session-start brief instead of the page a person reads")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="write the measured state rather than prose")
    p.set_defaults(func=cmd_welcome)

    p = sub.add_parser("demo", help="invent a company to try the tools on, with no credentials")
    p.add_argument("--out", default="data-demo", help="where to build it (default: data-demo)")
    p.add_argument("--seed", type=int, default=20260819, help="same seed, same company")
    p.add_argument("--weeks", type=int, default=14, help="weeks of history (default: 14)")
    p.add_argument("--chats", type=int, default=900, help="conversations (default: 900)")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("check", help="what this lake can answer, and what it is missing")
    shared(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("scan", help="sweep conversation content for sensitive-data patterns")
    shared(p)
    p.add_argument("--run-id", help="name this run (default: today)")
    p.set_defaults(func=cmd_scan)

    def reading(parser: argparse.ArgumentParser, default_worksheet: str) -> None:
        parser.add_argument("--judge", default="demo",
                            choices=["demo", "worksheet", "anthropic"],
                            help="who reads: an offline stand-in, a person via a "
                                 "worksheet, or a model (needs a key)")
        parser.add_argument("--run-id", help="which scan run (default: the newest)")
        parser.add_argument("--model", help="model id, for the anthropic judge")
        parser.add_argument("--worksheet", default=default_worksheet)

    p = sub.add_parser("judge", help="read the flagged passages and judge them")
    shared(p)
    reading(p, "review-worksheet.md")
    p.add_argument("--limit", type=int, default=x3_classify.DEFAULT_LIMIT,
                   help="most passages to read in this run (0 for no limit)")
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("verify", help="independently re-read what the first pass called real")
    shared(p)
    reading(p, "verify-worksheet.md")
    p.add_argument("--limit", type=int, default=0,
                   help="most passages to re-read (default: all of them)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("report", help="build one or more reports")
    shared(p)
    output(p)
    p.add_argument("reports", nargs="*", choices=[*REPORTS, []], default=[],
                   help="which reports (default: all three)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("deliver", help="package already-built reports for sending")
    p.add_argument("reports", nargs="+", help="rendered .html files")
    p.add_argument("--deliver-dir", default="deliveries")
    p.add_argument("--edition", choices=["shareable", "named"], default="shareable")
    p.add_argument("--to")
    p.add_argument("--from", dest="sender")
    p.set_defaults(func=cmd_deliver)

    p = sub.add_parser("all", help="check, scan and build everything")
    shared(p)
    output(p)
    p.add_argument("--read", action="store_true",
                   help="also read and verify the flagged passages, so the exposure "
                        "report can carry confirmed findings rather than candidates")
    reading(p, "review-worksheet.md")
    p.add_argument("--limit", type=int, default=x3_classify.DEFAULT_LIMIT,
                   help="most passages to read, with --read")
    p.set_defaults(func=cmd_all, reports=[])
    return p.get_default("func") and ap or ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
