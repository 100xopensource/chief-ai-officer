# Handoff — 100x Chief AI Officer

**Read this if you are picking this work up.** For what the project is and how
to run it, read [`README.md`](README.md) first; this file is only about state.

## Decisions already made (do not relitigate without asking)

| Decision | Value |
|---|---|
| Product name in reports and README | **100x Chief AI Officer** |
| Licence | Apache-2.0 |
| First release scope | All three reports |
| Architecture | Full staged pipeline, x0 through x5 |
| Conversation-content storage | On by default, behind an explicit consent record |
| Day-0 experience | Synthetic lake generator plus three pre-built example reports |
| Also required at launch | Scrubber CI gate, Claude marketplace readiness |

## What works today

Everything below runs end to end against a synthetic company, with no
credentials, and is covered by the test suite.

- **Foundation** — Apache-2.0, `NOTICE`, a `.gitignore` that keeps the lake and
  any real rendered report out of git by construction, two CI workflows.
- **Scrubber** (`tools/scrubber/scrub.py`) — fails on emails, corporate domains,
  credentials, national identity numbers, private keys, and rendered
  named-edition reports. Verified against a planted leak. `--explain` prints
  every rule and what it protects against.
- **Raw store** (`pipeline/lake/raw.py`) — every chat's full response written
  atomically before anything parses it. Derived tables rebuild locally with zero
  API calls.
- **Consent** (`pipeline/fetch/consent.py`) — a per-lake record that must exist
  before conversation content is pulled, naming an accountable person even for
  scheduled runs.
- **Parser** (`pipeline/fetch/parse_raw.py`) — raw to `dim_chat`,
  `fact_message`, `fact_block`, `fact_attachment` and `payload_index.csv`. Four
  scan surfaces; the error channel is a flag on tool output, not a fifth surface.
- **Fetchers** — analytics, directory and compliance. Rate-limited under the
  organisation-shared ceiling, with mid-run checkpointing.
- **Synthetic lake** (`pipeline/demo/generate_synthetic_lake.py`) — a 112-seat
  fictional company, seeded and reproducible, that runs through the real parser
  and plants the traps that have historically produced wrong conclusions.
- **Detectors** (`pipeline/detectors/families.py`) — nine families, each pattern
  carrying its historical precision. Definitions are saved at scan time.
- **Gap check** (`stages/x0_gapcheck.py`) — what each report can be produced
  from, what is blocked, and which dates the window resolves to. Resolves the
  window from the newest day of data rather than the wall clock.
- **Scan** (`stages/x2_scan.py`) — sweeps the corpus, writing a fingerprint of
  each matched value rather than the value.
- **Metrics** (`stages/x2_metrics_waste.py`, `_value.py`, `_exposure.py`) —
  deterministic computation for each report. No model reads anything.
- **Reading passes** (`stages/x3_classify.py`, `stages/x4_verify.py`) — x3 reads
  each flagged passage and judges it; x4 re-reads independently, never shown x3's
  answer. Only what both agree on is published as a finding; disagreements are
  published as neither. Three judges: `demo` (offline, for CI and the synthetic
  lake, and stamped as not-a-reading), `worksheet` (a person), `anthropic` (a
  model). The passage, not the pattern match, is the unit of judgement.
- **Findings ledger** (`stages/x5_lock.py`) — stable derived keys, and status
  across runs: new, still here, getting worse, getting better, cleared.
- **Compose** (`render/compose.py`) — measured numbers into reader-facing
  sentences, and the only place that happens.
- **Render** (`render/build.py`, `render/validate.py`) — the build refuses to
  write unless the markup outside the data record is byte-identical and the
  record round-trips. Twelve gates run over the rendered artifact.
- **Delivery** (`render/deliver.py`) — packages a report with a cover note, a
  manifest and checksums. Re-runs the gates and refuses, with no override, if
  any fails.
- **Entry point** (`pipeline/cli.py`) — `welcome`, `demo`, `check`, `scan`,
  `judge`, `verify`, `report`, `deliver`, `all`, installed as the `caio` command
  by `pyproject.toml`.
- **Post-install greeting** (`pipeline/welcome.py`, `hooks/`) — a session-start
  hook that speaks once after installation: what this is, that it has read
  nothing, and the two next steps. Standard library only and imports nothing
  else in the pipeline, because it runs before anything is installed.
- **Plugin** — `plugin.json`, `marketplace.json`, five skills, three reference
  documents, and `tools/check_manifests.py` gating all of it in CI.
- **Tests** — 59 tests. The privacy rules and the whole pipeline, on a
  synthetic company.
- **Example reports** — all three built through the real builder, passing every
  gate.

## What is left

1. **A named-edition render path.** The edition flag and its handling exist end
   to end; what does not exist is the step that adds names back at render time
   for a reviewer who has asked for them.
2. **Safeguard states.** The Exposure Report reports the four settings as
   unread, because nothing here can read them. If an administrative endpoint
   becomes available, wire it in — the report already has the shape for it.
3. **Scheduling.** Nothing here runs itself. A weekly run is currently a person
   or a cron entry.

## Principles the code encodes

Not style preferences. Each was paid for by a wrong number reaching a reader,
and the code enforces them so they cannot be forgotten. See
[`references/privacy_rules.md`](plugins/100x-chief-ai-officer/references/privacy_rules.md).

- **Persist raw before parsing.** A parsing bug should cost a local re-parse,
  never another full pull.
- **A pattern match is a candidate, never a finding.** Roughly 44% survive being
  read; some families survive none. Nothing is published unread.
- **Publish floors, not estimates.** And say so, in the reader's words.
- **Check when a dimension was born before trending across it.**
- **Verification runs independently of classification.**
- **Counts and ranks in records; names only at render, only on request.** Any
  group under five is described as "fewer than five".
- **Zero findings is a legal result.** Never pad.
- **Every finding needs an owner and an action within the organisation's
  control.**
- **Never test a credential.** Liveness is judged from context, never by use.
- **An absence of measurement is not a measurement of zero.** This one was
  added last and cost the most. A metric reading a field the data does not have
  does not crash — it sums to zero, and a zero passes every gate in the project:
  the page renders, nothing leaks, and the reconciliation block ties out
  perfectly because zero equals zero. Two reports shipped that way, one of them
  carrying a stat card reading "0% of connector calls failed" over a source that
  publishes no failure data at all. Every field a stage reads is now declared in
  `pipeline/lake/schema.py` and checked against the lake; a missing one is a
  named skip. Before adding a metric, check the field against what
  `pipeline/fetch/analytics.py` actually writes — not against the demo fixture,
  which is what went wrong.
- **The gates certify form, never substance.** They say the document is
  well-formed and leaks nothing. They cannot tell a real figure from one
  computed over an absent field, which is why the run now prints "integrity
  checks passed · numbers not independently verified" rather than a count that
  read as a guarantee.
- **Standing operator decisions live in one layer.** `pipeline/config.py` reads
  `_reports/config/`. Every stage that needs a seat count or a connection name
  asks it. Each stage building its own from the raw snapshot is what overstated
  the headline recoverable figure by thirteen per cent, weekly, to Finance.

## Running it

```bash
pip install -e ".[dev]"

# Everything, on invented data, with no credentials:
caio demo --out data-demo
caio all  --data-dir data-demo --out-dir _reports

# Against real data:
caio pull everything --data-dir data     # cost, usage, seats
caio pull content    --data-dir data     # conversation text, consent-gated
caio check --data-dir data               # window, blockers, shape drift, decisions in force

# Before anything ships:
python3 -m pytest
python3 tools/check_manifests.py
python3 tools/scrubber/scrub.py

# Does the lake carry what the stages read? Run it against real data too:
python3 -m pipeline.lake.schema --data-dir data
```

## A standing warning

The internal predecessor of this project shipped a **named edition** report
containing real employee names and email addresses. It is not in this repository
and must never be committed to it. The scrubber exists specifically to make that
mistake impossible; do not weaken it.
