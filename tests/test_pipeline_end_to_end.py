"""The whole pipeline, on an invented company, with no credentials.

This is the strongest signal this project can produce without an Enterprise
account: build a fake organisation's lake from scratch, run every stage over it,
and check the reports that come out are the ones a reader should get.

The lake is generated once per test session because generating it is the
expensive part; every test then reads it without modifying it.
"""

from __future__ import annotations

import json

import pytest

from pipeline.demo import generate_synthetic_lake as demo
from pipeline.render import build, compose, deliver, validate
from pipeline.stages import _window as W
from pipeline.stages import x0_gapcheck, x2_scan, x5_lock

SEED = 20260819
WEEKS = 14
# Fewer conversations than the demo default: enough to exercise every code path
# and every planted trap, few enough that the suite stays quick.
CHATS = 260


@pytest.fixture(scope="session")
def lake(tmp_path_factory):
    root = tmp_path_factory.mktemp("lake")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_content(CHATS)
    from pipeline.fetch import parse_raw
    parse_raw.parse_all(root, progress_every=0)
    generator.write_state()
    x2_scan.scan(root, "test-run")
    return root


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------

def test_the_window_is_a_complete_week(lake):
    window = x0_gapcheck.resolve_window(W.resolve_as_of(lake))
    from datetime import date
    start = date.fromisoformat(window["week_start"])
    end = date.fromisoformat(window["week_end"])
    assert start.weekday() == 0, "the window must start on a Monday"
    assert end.weekday() == 6, "the window must end on a Sunday"
    assert (end - start).days == 6


def test_the_window_never_includes_the_week_in_progress(lake):
    """A part-week compared against full weeks always reads as a collapse."""
    window = x0_gapcheck.resolve_window(W.resolve_as_of(lake))
    assert window["week_end"] < window["partial_week_start"]


def test_the_window_comes_from_the_data_not_the_clock(lake):
    """A lake pulled a month ago must report the last week it can see."""
    newest = W.latest_day(lake)
    window = x0_gapcheck.resolve_window(W.resolve_as_of(lake))
    assert window["week_start"] <= newest <= window["week_end"]


def test_a_week_label_never_contains_a_week_number(lake):
    window = x0_gapcheck.resolve_window(W.resolve_as_of(lake))
    assert "W" not in window["week_label"].replace("Week", "")


# --------------------------------------------------------------------------
# the gap check
# --------------------------------------------------------------------------

def test_a_full_lake_can_answer_every_report(lake):
    result = x0_gapcheck.gapcheck(lake)
    assert result["blocked"] == []
    assert set(result["clear"]) == {"waste", "value", "exposure"}


def test_a_missing_lake_blocks_rather_than_inventing(tmp_path):
    result = x0_gapcheck.gapcheck(tmp_path / "nothing-here")
    assert result["lake_exists"] is False
    assert result["blocked"], "a lake with nothing in it must block every report"


# --------------------------------------------------------------------------
# candidates are not findings
# --------------------------------------------------------------------------

def test_the_scan_produces_candidates_not_findings(lake):
    metrics = __import__("pipeline.stages.x2_metrics_exposure", fromlist=["compute"])
    window = x0_gapcheck.resolve_window(W.resolve_as_of(lake))
    result = metrics.compute(lake, window)
    survival = result["survival"]
    assert survival["expected_findings_low"] <= survival["expected_findings_high"]
    assert survival["expected_findings_high"] <= result["leads"]["week"], (
        "more findings can never be expected than there were matches"
    )


def test_matched_values_are_never_stored(lake):
    """A scan that stored what it matched would be a copy of the thing it is
    auditing."""
    candidates = (lake / "_reports" / "test-run" / "candidates.jsonl")
    rows = [json.loads(line) for line in candidates.read_text().splitlines() if line.strip()]
    assert rows
    for row in rows[:200]:
        assert "value" not in row
        assert "match" not in row
        assert row["value_fingerprint"], "a fingerprint must stand in for the value"


# --------------------------------------------------------------------------
# the ledger
# --------------------------------------------------------------------------

def test_findings_are_new_on_a_first_run_and_recurring_after(tmp_path_factory, lake):
    import shutil
    scratch = tmp_path_factory.mktemp("ledger")
    copy = scratch / "lake"
    shutil.copytree(lake, copy)

    first = x5_lock.lock(copy, "exposure", write=True)
    assert first["counts"]["total"] > 0
    assert first["counts"]["new"] == first["counts"]["total"]

    second = x5_lock.lock(copy, "exposure", write=True)
    assert second["counts"]["new"] == 0
    assert second["counts"]["recurring"] == second["counts"]["total"]


def test_a_finding_key_survives_its_neighbours_disappearing():
    """Keys are derived from what a finding is about, never from its position."""
    metrics = {
        "leads": {"by_family": {}, "blocks_scanned": 0, "week": 0},
        "arrival": {"machine_share_pct": 0, "segments": []},
        "reach": {"people_with_leads": 0, "people_active": 0},
        "survival": {},
        "blind_spots": {"unreadable_share_of_spend_pct": 90,
                        "reader_note": "note"},
        "truncation": {},
        "governance": {"conversations_with_no_messages": 12},
    }
    findings = x5_lock.findings_for_exposure(metrics)
    keys = [f["key"] for f in findings]
    assert keys == sorted(set(keys)), "keys must be unique"
    assert all(not k.split("/")[-1].isdigit() for k in keys), (
        "a key must never be a position in a list"
    )


def test_zero_findings_is_reported_as_zero(tmp_path):
    """Nothing is padded to fill the page."""
    empty = {"spend": {"change_pct": None, "week_usd": 0}, "seats": {"available": False},
             "cache": {"available": False}, "concentration": {"available": False}}
    assert x5_lock.findings_for_waste(empty) == []


# --------------------------------------------------------------------------
# reports
# --------------------------------------------------------------------------

@pytest.mark.parametrize("report", ["waste", "value", "exposure"])
def test_every_report_builds_and_passes_every_gate(lake, tmp_path, report):
    block = compose.compose(lake, report)
    out = build.default_out(report, block, tmp_path)
    build.build(report, block, out)

    result = validate.validate(out, out.with_suffix(".json"))
    assert not result.failures, [f"{g}: {d}" for g, d in result.failures]
    assert not result.warnings, [f"{g}: {d}" for g, d in result.warnings]


@pytest.mark.parametrize("report", ["waste", "value", "exposure"])
def test_a_report_carries_every_key_its_page_reads(lake, tmp_path, report):
    """The gate that exists because a report passed everything and still
    rendered as a blank page."""
    block = compose.compose(lake, report)
    out = tmp_path / f"{report}.html"
    build.build(report, block, out, write_sidecar=False)
    result = validate.validate(out)
    assert "completeness" in result.passed


def test_building_cannot_change_the_design(lake, tmp_path):
    """Iteration is a data swap. Anything else is refused rather than shipped."""
    block = compose.compose(lake, "exposure")
    out = tmp_path / "one.html"
    build.build("exposure", block, out, write_sidecar=False)

    template = (build.TEMPLATES / build.REPORTS["exposure"][0]).read_text(encoding="utf-8")
    built = out.read_text(encoding="utf-8")

    before = build._locate_block(template)
    after = build._locate_block(built)
    assert template[:before.start(2)] == built[:after.start(2)]
    assert template[before.end(2):] == built[after.end(2):]


def test_the_data_round_trips_through_the_built_file(lake, tmp_path):
    block = compose.compose(lake, "value")
    out = tmp_path / "value.html"
    build.build("value", block, out, write_sidecar=False)
    embedded, error = validate.extract_block(out.read_text(encoding="utf-8"))
    assert error is None
    assert embedded == block


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------

def test_a_clean_report_packages_with_everything_a_recipient_needs(lake, tmp_path):
    block = compose.compose(lake, "exposure")
    out = tmp_path / "exposure-report-2026-07-12.html"
    build.build("exposure", block, out)

    result = deliver.deliver(out, tmp_path / "deliveries", recipient="Compliance")
    folder = tmp_path / "deliveries" / block["week_ending"]

    assert (folder / "MANIFEST-exposure.json").exists()
    assert (folder / "COVER-NOTE-exposure.md").exists()
    assert (folder / "CHECKSUMS.txt").exists()
    assert result["edition"] == "shareable"

    manifest = json.loads((folder / "MANIFEST-exposure.json").read_text())
    assert manifest["files"][out.name]["sha256"] == deliver.sha256(out)


def test_delivery_refuses_a_report_that_fails_a_gate(lake, tmp_path):
    """There is no override. That is the point of the gate."""
    block = compose.compose(lake, "exposure")
    block["verdict"] = list(block["verdict"]) + ["Contact casey.doyle@northwind.example."]
    out = tmp_path / "leaky.html"
    build.build("exposure", block, out, write_sidecar=False)

    with pytest.raises(deliver.DeliveryRefused):
        deliver.deliver(out, tmp_path / "nothing")
    assert not (tmp_path / "nothing").exists(), "nothing may be written on refusal"
