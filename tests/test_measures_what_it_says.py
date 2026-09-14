"""The tests that would have caught two confidently wrong reports.

Every test here exists because of one run. Two reports passed twelve gates each
and were wrong in three places, all in the direction that reassures the reader:
a stat card reading "0% of connector calls failed" over a source that publishes
no failure data, a "most-used capabilities" list that was not sorted by usage,
and a seat count thirteen per cent too high because the operator's own exclusion
file was never read.

The common cause was that the metric code was written against this project's own
demo fixture, whose shape did not match what the live API returns, and nothing
ever compared the two. So the first group of tests below compares them — the
fixture is pinned to the fetch layer's flatten functions, field for field, and a
divergence fails here rather than in production six months later.

The rest test the property that actually matters and that no existing gate
covered: that an absent measurement is reported as absent, never as zero.
"""

from __future__ import annotations

import json

import pytest

from pipeline import config
from pipeline.demo import generate_synthetic_lake as demo
from pipeline.fetch import analytics
from pipeline.lake import datalake as lake
from pipeline.lake import schema
from pipeline.render import compose, validate
from pipeline.stages import _window as W
from pipeline.stages import x0_gapcheck, x2_metrics_value, x2_metrics_waste

SEED = 20260819
WEEKS = 14


@pytest.fixture(scope="module")
def lake_root(tmp_path_factory):
    """A lake with the analytics side only — no conversation content needed here."""
    root = tmp_path_factory.mktemp("shape-lake")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_state()
    return root


def _window(root):
    return x0_gapcheck.resolve_window(W.resolve_as_of(root))


# --------------------------------------------------------------------------
# the fixture must be the shape production actually returns
# --------------------------------------------------------------------------

# A row as the live analytics API returns it, before flattening. Taken from a
# real response body, with the identifiers replaced.
LIVE_CONNECTOR_ROW = {
    "connector_name": "cova-pos-tools",
    "distinct_user_count": 1,
    "chat_metrics": {"distinct_conversation_connector_used_count": 0},
    "claude_code_metrics": {"distinct_session_connector_used_count": 0},
    "cowork_metrics": {"distinct_session_connector_used_count": 3},
    "office_metrics": {"excel": {"distinct_session_connector_used_count": 0}},
}

LIVE_SKILL_ROW = {
    "skill_name": "xlsx",
    "distinct_user_count": 4,
    "chat_metrics": {"distinct_conversation_skill_used_count": 12},
    "claude_code_metrics": {"distinct_session_skill_used_count": 22},
    "cowork_metrics": {"distinct_session_skill_used_count": 0},
    "office_metrics": {"excel": {"distinct_session_skill_used_count": 3}},
}


@pytest.mark.parametrize("dataset,flatten,live", [
    ("analytics_connectors", analytics.flatten_connector, LIVE_CONNECTOR_ROW),
    ("analytics_skills", analytics.flatten_skill, LIVE_SKILL_ROW),
])
def test_the_fixture_has_the_same_columns_as_a_real_pull(lake_root, dataset, flatten, live):
    """The demo lake and the live API must agree on what a row looks like.

    This is the test whose absence cost the most. The fixture emitted
    read_call_count, write_call_count, unclassified_call_count, error_call_count
    and invocation_count; the API returns none of them. The metric code was
    written against the fixture, passed every test, and read five fields that do
    not exist in production — silently, because a missing field sums to zero.
    """
    from_api = set(flatten(live, "2026-09-07"))
    from_fixture = set()
    for row in lake.iter_dataset(lake_root, dataset):
        from_fixture |= set(row)

    invented = from_fixture - from_api
    assert not invented, (
        f"the demo lake invents {sorted(invented)} in {dataset}, which no real pull "
        "returns. Metric code written against these fields reads zeros in "
        "production and reports them as measurements."
    )


def test_no_metric_reads_a_field_the_fetch_layer_never_writes(lake_root):
    """Every field under contract must be one a real pull actually produces."""
    for contract in schema.CONTRACTS:
        if not contract.dataset.startswith("analytics_"):
            continue
        declared = set(contract.required) | set(contract.at_least_one)
        written = set()
        for row in lake.iter_dataset(lake_root, contract.dataset):
            written |= set(row)
        if not written:
            continue
        assert declared <= written | {"day"}, (
            f"{contract.consumer} declares it reads "
            f"{sorted(declared - written - {'day'})} from {contract.dataset}, "
            "which is not in the data."
        )


# --------------------------------------------------------------------------
# an absence is reported as an absence, never as a zero
# --------------------------------------------------------------------------

def _strip_fields(root, dataset, fields):
    """Remove columns from every row, the way a schema change upstream would."""
    for week in lake.list_partitions(root, dataset):
        rows = list(lake.iter_partition(root, dataset, week))
        for row in rows:
            for field in fields:
                row.pop(field, None)
        lake.write_partition(root, dataset, week, rows)


def test_a_missing_usage_column_is_a_named_skip_not_a_zero(tmp_path_factory):
    root = tmp_path_factory.mktemp("stripped")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_state()

    _strip_fields(root, "analytics_connectors", x2_metrics_value.USED_FIELDS + ("office_metrics",))

    metrics = x2_metrics_value.compute(root, _window(root))
    connectors = metrics["connectors"]

    assert connectors["available"] is False
    assert connectors["why"], "a skip must carry the reason it skipped"
    assert "count_in_use" not in connectors, (
        "a connector count computed over a missing column would be zero, and a "
        "zero here renders as a measurement"
    )


def test_reliability_is_permanently_not_measurable_and_says_so():
    """No failure figure may be produced, however the data changes.

    The source publishes no failure count and no call count. A report that prints
    0% here is not cautious, it is wrong in the most reassuring available
    direction, and it was published.
    """
    reliability = x2_metrics_value._reliability()
    assert reliability["available"] is False
    note = reliability["reader_note"].lower()
    assert "cannot be answered" in note
    assert "not the same as everything having worked" in note


def test_no_report_claims_a_connector_error_rate(lake_root):
    block = compose.compose(lake_root, "value", write_ledger=False)
    text = json.dumps(block).lower()
    for banned in ("of connector calls failed", "error rate", "% failed"):
        assert banned not in text, f"the value report still claims {banned!r}"


# --------------------------------------------------------------------------
# a ranking must be ranked
# --------------------------------------------------------------------------

def test_capabilities_are_ranked_by_the_number_they_are_ranked_on(lake_root):
    """The published list was sorted by a key that was zero for every row.

    So the order was whatever the dictionary happened to be built in, and it was
    presented to the reader as a ranking. A wrong ranking presented as a ranking
    is worse than no ranking.
    """
    skills = x2_metrics_value.compute(lake_root, _window(lake_root))["skills"]
    assert skills["available"], skills.get("why")
    sessions = [s["sessions"] for s in skills["in_use"]]
    assert sessions == sorted(sessions, reverse=True)
    assert skills["sessions_total"] > 0
    assert sum(sessions) > 0, "a ranking over all-zero values is not a ranking"


def test_connectors_are_ranked_by_sessions(lake_root):
    connectors = x2_metrics_value.compute(lake_root, _window(lake_root))["connectors"]
    counts = [c["sessions"] for c in connectors["in_use"]]
    assert counts == sorted(counts, reverse=True)
    assert sum(counts) > 0


def test_people_are_counted_at_their_peak_day_not_summed_across_days(lake_root):
    """Summing a distinct-user count across a week double-counts anyone active
    on more than one day. It inflated one connection from six people to fifteen."""
    window = _window(lake_root)
    connectors = x2_metrics_value.compute(lake_root, window)["connectors"]

    peaks = {}
    for row in W.rows_in_window(lake_root, "analytics_connectors",
                                window["week_start"], window["week_end"]):
        name = str(row.get("connector_name") or "")
        value = row.get("distinct_user_count") or 0
        peaks[name] = max(peaks.get(name, 0), value)

    for entry in connectors["in_use"]:
        if entry["aliased"] or entry["people_described"] == "fewer than five people":
            continue
        expected = W.describe_people(int(peaks.get(entry["name"], 0)))
        assert entry["people_described"] == expected


# --------------------------------------------------------------------------
# the gate that catches a column of zeros
# --------------------------------------------------------------------------

def test_a_column_of_zeros_fails_the_gate():
    """The exact shape the wrong report shipped in."""
    block = {
        "scoreboard": {
            "head": ["Connection", "Calls", "Failed", "Change", "Verdict"],
            "rows": [[name, "0", "0%", "too new to compare", "grey|Too few calls to judge"]
                     for name in ("one", "two", "three", "four")],
        },
    }
    result = validate.Result()
    validate.gate_zero_variance(block, result)
    assert result.failures, "a column that is zero in every row must not ship"
    assert "Calls" in result.failures[0][1]


def test_a_reconciliation_of_zero_against_zero_fails_the_gate():
    block = {"reconciliation": {"connector_calls": {
        "total": 0,
        "parts": [{"label": n, "value": 0} for n in ("a", "b", "c", "d")],
        "explained": None,
    }}}
    result = validate.Result()
    validate.gate_zero_variance(block, result)
    assert result.failures, "zero equals zero is not a reconciliation"


def test_a_column_with_real_numbers_passes():
    block = {"scoreboard": {"head": ["Connection", "Sessions"],
                            "rows": [["a", "51"], ["b", "0"], ["c", "26"]]}}
    result = validate.Result()
    validate.gate_zero_variance(block, result)
    assert not result.failures
    assert "zero_variance" in result.passed


def test_a_short_table_is_not_mistaken_for_an_all_zero_one():
    """Three rows really can be three zeros; the gate must not cry wolf."""
    block = {"scoreboard": {"head": ["Thing", "Count"], "rows": [["a", "0"], ["b", "0"]]}}
    result = validate.Result()
    validate.gate_zero_variance(block, result)
    assert not result.failures


def test_the_summary_line_does_not_claim_the_numbers_were_checked():
    """"12 gates passed" was read as "the numbers are right", including by the
    people who wrote it. Every gate checks form; none checks substance."""
    result = validate.Result()
    result.ok("privacy")
    line = validate.summary_line("report.html", result).lower()
    assert "integrity checks passed" in line
    assert "numbers not independently verified" in line
    assert "gates passed" not in line


# --------------------------------------------------------------------------
# the operator's standing decisions
# --------------------------------------------------------------------------

def _write_exclusions(root, user_ids):
    path = config.config_dir(root)
    path.mkdir(parents=True, exist_ok=True)
    (path / "seat_exclusions.json").write_text(json.dumps({
        "excluded": [{"user_id": u, "reason": "service account",
                      "decided_on": "2026-08-17"} for u in user_ids]
    }), encoding="utf-8")


def test_excluded_accounts_leave_the_seat_count_and_the_money(tmp_path_factory):
    """The headline recoverable figure goes to Finance. It was 13% too high
    every week because nothing read the operator's own exclusion file."""
    root = tmp_path_factory.mktemp("exclusions")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_state()
    window = _window(root)

    before = x2_metrics_waste.compute(root, window)["seats"]

    idle = [u for u in lake.iter_snapshot(root, "directory_users")][:6]
    _write_exclusions(root, [u["user_id"] for u in idle])
    after = x2_metrics_waste.compute(root, window)["seats"]

    assert after["seats_total"] == before["seats_total"] - 6
    assert after["seats_excluded"] == 6
    assert after["recoverable_if_removed_usd"] <= before["recoverable_if_removed_usd"]
    assert "excluded from every seat count" in after["seats_note"]


def test_both_reports_agree_on_how_many_seats_there_are(tmp_path_factory):
    """The Waste Ledger and the Value X-Ray read the seat count from different
    places. They must not be able to disagree about the size of the company."""
    root = tmp_path_factory.mktemp("agree")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_state()
    window = _window(root)

    everyone = [u["user_id"] for u in lake.iter_snapshot(root, "directory_users")]
    _write_exclusions(root, everyone[:5])

    waste = x2_metrics_waste.compute(root, window)["seats"]
    value = x2_metrics_value.compute(root, window)["adoption"]
    assert waste["seats_total"] == value["seats_total"]
    assert waste["seats_excluded"] == value["seats_excluded"] == 5


def test_a_malformed_config_stops_the_run_rather_than_being_ignored(tmp_path):
    path = config.config_dir(tmp_path)
    path.mkdir(parents=True, exist_ok=True)
    (path / "seat_exclusions.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="records a decision"):
        config.seat_exclusions(tmp_path)


def test_no_config_means_nothing_is_excluded(tmp_path):
    assert config.seat_exclusions(tmp_path) == set()
    assert config.connector_aliases(tmp_path) == {}


def test_an_alias_merges_two_identities_of_one_connection(tmp_path_factory):
    """The same connection reports under a name and under a bare identifier, and
    every count of connections in use counts it twice. Only an operator can say
    they are the same thing."""
    root = tmp_path_factory.mktemp("aliases")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_state()
    window = _window(root)

    before = x2_metrics_value.compute(root, window)["connectors"]

    path = config.config_dir(root)
    path.mkdir(parents=True, exist_ok=True)
    (path / "connector_aliases.json").write_text(json.dumps(
        {"aliases": {"6f616b42-0ed8-571e-823f-ee4aca6b7ce9": "warehouse_sql"}}),
        encoding="utf-8")

    after = x2_metrics_value.compute(root, window)["connectors"]

    assert after["count_in_use"] == before["count_in_use"] - 1
    assert after["unnamed_count"] == before["unnamed_count"] - 1
    merged = next(c for c in after["in_use"] if c["name"] == "warehouse_sql")
    was = next(c for c in before["in_use"] if c["name"] == "warehouse_sql")
    assert merged["sessions"] > was["sessions"]


# --------------------------------------------------------------------------
# a week measured over five days is not compared against weeks of seven
# --------------------------------------------------------------------------

def _truncate_days(root, dataset, last_day):
    for week in lake.list_partitions(root, dataset):
        rows = [r for r in lake.iter_partition(root, dataset, week)
                if str(r.get("day") or "") <= last_day]
        lake.write_partition(root, dataset, week, rows)


def test_a_lagging_activity_table_suppresses_the_comparison(tmp_path_factory):
    """The cost table reaches the end of the week; the activity tables finalise
    a couple of days behind it. Nothing said so, and the shortfall biased every
    current week downward against four full-week baselines."""
    root = tmp_path_factory.mktemp("lagging")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_state()
    window = _window(root)

    full = x2_metrics_value.compute(root, window)["adoption"]
    assert full["change_vs_prior_pct"] is not None
    assert full["week_is_short"] is False

    # Two days short, exactly the observed lag.
    from datetime import date, timedelta
    short_to = (date.fromisoformat(window["week_end"]) - timedelta(days=2)).isoformat()
    _truncate_days(root, "analytics_users", short_to)

    short = x2_metrics_value.compute(root, window)
    adoption = short["adoption"]

    assert adoption["week_is_short"] is True
    assert adoption["change_vs_prior_pct"] is None, (
        "a five-day week compared against four seven-day weeks reads as a fall "
        "that did not happen"
    )
    assert short_to in adoption["reader_note"]
    assert "Monday-to-Sunday week. The marked point" not in short["trend"]["caption"], (
        "the caption must not assert a complete week for a point that is not one"
    )
    assert next(p for p in short["trend"]["weeks"] if p["current"])["partial"] is True


def test_coverage_reports_which_table_falls_short(tmp_path_factory):
    root = tmp_path_factory.mktemp("coverage")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_state()
    window = _window(root)

    coverage = W.window_coverage(root, ["analytics_cost", "analytics_users"],
                                 window["week_start"], window["week_end"])
    assert coverage["any_short"] is False

    from datetime import date, timedelta
    short_to = (date.fromisoformat(window["week_end"]) - timedelta(days=2)).isoformat()
    _truncate_days(root, "analytics_users", short_to)

    coverage = W.window_coverage(root, ["analytics_cost", "analytics_users"],
                                 window["week_start"], window["week_end"])
    assert coverage["short_datasets"] == ["analytics_users"]
    assert coverage["datasets"]["analytics_users"]["days_with_data"] == 5
    assert coverage["datasets"]["analytics_cost"]["short"] is False


# --------------------------------------------------------------------------
# the two absences that used to share one sentence
# --------------------------------------------------------------------------

def test_new_and_incomparable_are_told_apart(lake_root):
    """"too new to compare" was emitted whenever a change was absent, for any
    reason. The methodology section then explained it as a deliberate birth-date
    guard — so a data fault read as a considered editorial choice."""
    connectors = x2_metrics_value.compute(lake_root, _window(lake_root))["connectors"]
    reasons = {c["no_change_reason"] for c in connectors["in_use"]
               if c["change_pct"] is None}
    assert reasons <= {"new this period", "nothing to compare against"}
    for entry in connectors["in_use"]:
        if entry["change_pct"] is not None:
            assert entry["no_change_reason"] is None


def test_check_names_a_dataset_that_is_present_but_the_wrong_shape(tmp_path_factory):
    root = tmp_path_factory.mktemp("shapecheck")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_state()

    assert not [r for r in schema.check(root) if not r["ok"]]

    _strip_fields(root, "analytics_skills", x2_metrics_value.USED_FIELDS + ("office_metrics",))

    broken = [r for r in schema.check(root) if not r["ok"]]
    assert [r["dataset"] for r in broken] == ["analytics_skills"]
    assert broken[0]["reader_why"]

    printed = x0_gapcheck.render_human(x0_gapcheck.gapcheck(root, W.resolve_as_of(root)))
    assert "Present but the wrong shape" in printed
    assert "analytics_skills" in printed


# --------------------------------------------------------------------------
# a broken config is not an empty one
#
# The build refuses a config that will not parse, and says so clearly. `caio
# check` — the cheap command whose whole job is to find that out first — showed
# it as "0 entry(s)" and reported everything ready to run. Zero exclusions and
# an unreadable exclusion file are opposite situations that looked identical,
# and the one that looked fine was the broken one.
# --------------------------------------------------------------------------

def _break_config(root, name="seat_exclusions.json"):
    path = config.config_dir(root)
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text('{"excluded": [{"user_id": "u1",}]}', encoding="utf-8")


def test_check_reports_a_config_it_cannot_parse(tmp_path_factory):
    root = tmp_path_factory.mktemp("brokencfg")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_state()

    clean = x0_gapcheck.gapcheck(root, W.resolve_as_of(root))
    assert not clean.get("decisions_broken")

    _break_config(root)
    result = x0_gapcheck.gapcheck(root, W.resolve_as_of(root))

    assert result["decisions_broken"] == ["seat_exclusions.json"]
    printed = x0_gapcheck.render_human(result)
    assert "BROKEN" in printed
    assert "0 entry(s)" not in printed, (
        "an unreadable config printed as though it recorded no exclusions"
    )
    assert "Ready to run" not in result["summary"], (
        "the summary promised reports the next build will refuse to produce"
    )


def test_a_broken_config_stops_the_build_it_was_promised_to(tmp_path_factory):
    """The two must agree: whatever check says, the build must do."""
    root = tmp_path_factory.mktemp("brokencfg2")
    generator = demo.Generator(root, seed=SEED, weeks=WEEKS)
    generator.build_directory()
    generator.write_directory()
    generator.write_analytics()
    generator.write_state()
    _break_config(root)

    with pytest.raises(ValueError, match="records a decision"):
        x2_metrics_waste.compute(root, _window(root))


# --------------------------------------------------------------------------
# two connections that cannot be named are still two different connections
# --------------------------------------------------------------------------

def test_connections_with_no_name_are_told_apart(lake_root):
    """They render as the same words, so a reader sees two identical rows and
    cannot carry out the action the report gives them — look each one up."""
    connectors = x2_metrics_value.compute(lake_root, _window(lake_root))["connectors"]
    assert connectors["unnamed_count"] >= 2, "fixture no longer exercises this"

    labels = compose._connector_labels(connectors)
    assert len(labels) == len(set(labels)), f"duplicate row labels: {labels}"

    unnamed = [l for l, c in zip(labels, connectors["in_use"]) if c["unnamed"]]
    assert all(l.startswith("unnamed connection ") for l in unnamed), unnamed
    # No identifier may reach the label — that is what the privacy gate is for.
    for label, entry in zip(labels, connectors["in_use"]):
        if entry["unnamed"]:
            assert entry["name"] not in label


def test_a_single_unnamed_connection_still_reads_as_prose():
    """Numbering exists to disambiguate. With nothing to disambiguate from, the
    plain wording is better and is what the reader gets."""
    connectors = {"in_use": [{"name": "warehouse_sql", "unnamed": False, "sessions": 5},
                             {"name": "a3f19c2e-7b40-4d81-9e55-2c6f0a1b8d33",
                              "unnamed": True, "sessions": 3}]}
    assert compose._connector_labels(connectors) == [
        "warehouse_sql", "an unnamed connection"]
