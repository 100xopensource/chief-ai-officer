"""x3 and x4 — the difference between a match and a finding.

The rule these tests exist to hold: nothing reaches a reader as a finding
unless it was read and an independent second pass agreed. Every other property
here follows from that one.
"""

from __future__ import annotations

import json

import pytest

from pipeline.demo import generate_synthetic_lake as demo
from pipeline.judges import get_judge
from pipeline.judges.base import (
    VERDICT_FALSE_ALARM,
    VERDICT_REAL,
    VERDICT_UNSURE,
    Candidate,
    Verdict,
)
from pipeline.render import compose
from pipeline.stages import _window as W
from pipeline.stages import x2_metrics_exposure, x2_scan, x3_classify, x4_verify
from pipeline.stages.x0_gapcheck import resolve_window

RUN = "test-run"


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    root = tmp_path_factory.mktemp("reading")
    generator = demo.Generator(root, seed=20260819, weeks=14)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_content(200)
    from pipeline.fetch import parse_raw
    parse_raw.parse_all(root, progress_every=0)
    generator.write_state()
    x2_scan.scan(root, RUN)
    return root


# --------------------------------------------------------------------------
# the unit of judgement
# --------------------------------------------------------------------------

def test_a_passage_is_judged_once_however_many_patterns_hit_it(lake):
    """Grouping by passage, not by match. Getting this wrong silently changed
    the denominator of every coverage figure."""
    matches = x3_classify.load_candidates(lake, RUN)
    passages = x3_classify.group_by_passage(matches)

    assert len(passages) <= len(matches)
    ids = [p["block_id"] for p in passages]
    assert len(ids) == len(set(ids)), "one entry per passage"
    assert sum(p["match_count"] for p in passages) == len(matches), (
        "every match must be accounted for by exactly one passage"
    )


def test_the_strongest_pattern_decides_how_a_passage_is_filed():
    rows = [
        {"block_id": "b:0", "family": "D2", "pattern_id": "D2_X",
         "expect_precision": "zero-without-context"},
        {"block_id": "b:0", "family": "D1", "pattern_id": "D1_KEY",
         "expect_precision": "high"},
    ]
    passage = x3_classify.group_by_passage(rows)[0]
    assert passage["expect_precision"] == "high"
    assert passage["family"] == "D1"
    assert set(passage["patterns"]) == {"D2_X", "D1_KEY"}


# --------------------------------------------------------------------------
# what is stored
# --------------------------------------------------------------------------

def test_a_verdict_never_stores_what_it_read(lake):
    x3_classify.classify(lake, RUN, judge_name="demo", limit=0, resume=False)
    rows = [json.loads(line) for line
            in (lake / "_reports" / RUN / "verdicts.jsonl").read_text().splitlines()
            if line.strip()]
    assert rows
    for row in rows:
        assert "text" not in row
        assert "value" not in row
        assert "passage" not in row


def test_a_reason_is_scrubbed_of_things_it_must_not_carry():
    from pipeline.judges.worksheet import _scrub
    dirty = ("saw casey.doyle@northwind.example in Q3-plan.xlsx alongside 4532015112830366.")
    clean = _scrub(dirty)
    assert "@" not in clean
    assert ".xlsx" not in clean
    assert "4532015112830366" not in clean


def test_a_severity_is_dropped_when_the_verdict_is_not_real():
    """A severity on a cleared passage later reads as a finding."""
    verdict = Verdict(block_id="b:0", verdict=VERDICT_FALSE_ALARM,
                      reason="placeholder", judged_by="test", severity="high")
    assert verdict.severity is None


# --------------------------------------------------------------------------
# the demo judge
# --------------------------------------------------------------------------

def test_a_placeholder_is_a_false_alarm():
    judge = get_judge("demo")
    verdict = judge.judge_one(_candidate(
        text="password=REDACTED endpoint=internal", precision="low"))
    assert verdict.verdict == VERDICT_FALSE_ALARM


def test_a_hostname_containing_example_is_not_documentation():
    """A live signed token was dismissed because its host was example.com."""
    judge = get_judge("demo")
    verdict = judge.judge_one(_candidate(
        text="Download: https://bi.internal.example.com/export/r-1?token=abc123def456",
        precision="high"))
    assert verdict.verdict == VERDICT_REAL


def test_prose_saying_for_example_still_reads_as_documentation():
    judge = get_judge("demo")
    verdict = judge.judge_one(_candidate(
        text="For example, a token looks like tok_abcdef123456 in the docs.",
        precision="high"))
    assert verdict.verdict == VERDICT_FALSE_ALARM


def test_the_demo_judge_admits_it_is_not_a_reading():
    verdict = get_judge("demo").judge_one(_candidate(text="anything", precision="high"))
    assert verdict.judged_by == "demo-offline"
    assert verdict.to_row()["not_a_reading"] is True


# --------------------------------------------------------------------------
# confirmation requires agreement
# --------------------------------------------------------------------------

def test_only_what_both_passes_call_real_is_confirmed(lake):
    x3_classify.classify(lake, RUN, judge_name="demo", limit=0, resume=False)
    result = x4_verify.verify(lake, RUN, judge_name="demo", resume=False)

    statuses = result["statuses"]
    assert statuses["confirmed"] > 0, "the synthetic lake plants material that survives"
    assert statuses["cleared"] > 0, "and material that does not"
    assert result["agreement_pct"] is not None


def test_a_disagreement_confirms_nothing(lake, monkeypatch):
    """The whole point of an independent pass is that it can say no."""
    x3_classify.classify(lake, RUN, judge_name="demo", limit=0, resume=False)

    class Contrarian:
        name = "contrarian"

        def __init__(self, **_):
            pass

        def judge_one(self, candidate):
            return Verdict(block_id=candidate.block_id, verdict=VERDICT_FALSE_ALARM,
                           reason="Not convinced.", judged_by=self.name)

        def close(self):
            pass

    monkeypatch.setattr(x4_verify, "get_judge", lambda name, **kw: Contrarian())
    result = x4_verify.verify(lake, RUN, judge_name="demo", resume=False)

    assert result["statuses"]["confirmed"] == 0
    assert result["statuses"]["disputed"] > 0
    assert result["agreement_pct"] == 0.0


def test_an_unread_passage_is_never_a_finding(lake):
    """Read only a couple, and the rest must stay candidates."""
    x3_classify.classify(lake, RUN, judge_name="demo", limit=2, resume=False)
    result = x4_verify.verify(lake, RUN, judge_name="demo", resume=False)
    assert result["statuses"]["unverified"] > 0


@pytest.mark.parametrize("first,second,expected", [
    (VERDICT_REAL, VERDICT_REAL, "confirmed"),
    (VERDICT_REAL, VERDICT_FALSE_ALARM, "disputed"),
    (VERDICT_REAL, VERDICT_UNSURE, "disputed"),
    (VERDICT_REAL, None, "unverified"),
    (VERDICT_FALSE_ALARM, None, "cleared"),
    (VERDICT_UNSURE, None, "cleared"),
    (None, None, "unverified"),
])
def test_the_resolution_table(first, second, expected):
    verdict = {"verdict": first} if first else None
    verification = {"verdict": second} if second else None
    assert x4_verify.resolve(verdict, verification) == expected


# --------------------------------------------------------------------------
# what reaches the report
# --------------------------------------------------------------------------

def test_the_report_names_who_read_it(lake):
    x3_classify.classify(lake, RUN, judge_name="demo", limit=0, resume=False)
    x4_verify.verify(lake, RUN, judge_name="demo", resume=False)

    block = compose.compose(lake, "exposure")
    verifline = block["verifline"]
    assert "stand-in" in verifline, (
        "a report built by the offline stand-in must say so on its own face"
    )


def test_an_unjudged_lake_reports_candidates_not_findings(lake, tmp_path):
    """Before anything is read, the honest headline is that nothing is a finding."""
    import shutil
    copy = tmp_path / "unjudged"
    shutil.copytree(lake, copy)
    for name in ("verdicts.jsonl", "verifications.jsonl"):
        (copy / "_reports" / RUN / name).unlink(missing_ok=True)

    metrics = x2_metrics_exposure.compute(
        copy, resolve_window(W.resolve_as_of(copy)), RUN)
    assert metrics["judgement"]["available"] is False

    block = compose.compose(copy, "exposure")
    assert "candidate" in block["verdict"][0].lower()
    assert "Nothing here has been read" in block["verifline"]


# --------------------------------------------------------------------------

def _candidate(*, text: str, precision: str) -> Candidate:
    return Candidate(
        block_id="chat_1_m0:0", chat_id="chat_1", family="D1",
        family_title="Credentials and secrets", pattern_id="D1_TEST",
        expect_precision=precision, surface="tool_output", day="2026-07-08",
        text=text,
    )
