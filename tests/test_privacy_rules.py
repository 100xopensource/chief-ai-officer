"""The rules that stop a person being identified by a report.

These are the tests worth having. Every one of them corresponds to something
that has actually reached a reader, and each failure here is a privacy incident
rather than a broken build.
"""

from __future__ import annotations

import json

import pytest

from pipeline.render import build, validate
from pipeline.stages import _window as W


# --------------------------------------------------------------------------
# the small-group floor
# --------------------------------------------------------------------------

@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_small_groups_are_never_a_digit(count):
    """One to four people is a name with extra steps."""
    assert W.describe_people(count) == "fewer than five people"


@pytest.mark.parametrize("count", [5, 6, 40, 1200])
def test_groups_of_five_or_more_are_counted(count):
    assert W.describe_people(count) == f"{count:,} people"


def test_nobody_is_nobody():
    assert W.describe_people(0) == "nobody"


def test_non_person_counts_have_no_floor():
    """A count of conversations is not a person and is not floored."""
    assert W.describe_count(3, "conversation") == "3 conversations"


# --------------------------------------------------------------------------
# the gates that stop identifying detail
# --------------------------------------------------------------------------

def _report_with(tmp_path, extra_text, kind="exposure"):
    """Build a real report carrying one extra sentence, and validate it."""
    template = build.TEMPLATES / build.REPORTS[kind][0]
    block = json.loads(build._locate_block(template.read_text(encoding="utf-8")).group(2))
    block["verdict"] = list(block["verdict"]) + [extra_text]
    out = tmp_path / "report.html"
    build.build(kind, block, out, write_sidecar=False)
    return validate.validate(out)


def test_an_email_address_stops_a_shareable_report(tmp_path):
    result = _report_with(tmp_path, "Ask casey.doyle@northwind.example about this.")
    assert any(gate == "privacy" for gate, _ in result.failures)


def test_a_filename_stops_a_report(tmp_path):
    result = _report_with(tmp_path, "The material was in Q3-headcount-plan.xlsx.")
    assert any(gate == "privacy" for gate, _ in result.failures)


def test_a_bare_run_of_digits_stops_a_report(tmp_path):
    """A matched value leaking through as digits — the shape a card or an
    identity number would have."""
    result = _report_with(tmp_path, "The value recorded was 4532015112830366.")
    assert any(gate == "privacy" for gate, _ in result.failures)


def test_thousands_separators_are_not_a_leak(tmp_path):
    """Real numbers must still be printable, or the gate gets switched off."""
    result = _report_with(tmp_path, "Across the period there were 1,227,415 requests.")
    assert not any(gate == "privacy" for gate, _ in result.failures)


def test_a_small_group_as_a_digit_stops_a_report(tmp_path):
    result = _report_with(tmp_path, "This affected 3 people in the commercial team.")
    assert any(gate == "small_group" for gate, _ in result.failures)


def test_regulatory_status_cannot_be_asserted(tmp_path):
    """This pipeline cannot establish that a regulation applies, so it may not
    say one does."""
    result = _report_with(tmp_path, "This is HIPAA-regulated material and must be handled so.")
    assert any(gate == "privacy" for gate, _ in result.failures)


def test_a_named_edition_permits_emails_but_nothing_else(tmp_path):
    template = build.TEMPLATES / build.REPORTS["exposure"][0]
    block = json.loads(build._locate_block(template.read_text(encoding="utf-8")).group(2))
    block["verdict"] = list(block["verdict"]) + ["Reviewer: casey.doyle@northwind.example."]
    out = tmp_path / "named.html"
    build.build("exposure", block, out, write_sidecar=False)

    shareable = validate.validate(out, edition="shareable")
    named = validate.validate(out, edition="named")

    assert any(gate == "privacy" for gate, _ in shareable.failures)
    assert not any(gate == "privacy" for gate, _ in named.failures)
    # everything else still applies
    assert named.passed
