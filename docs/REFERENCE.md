# Command reference

[← back to the README](../README.md)

## Every command

| Command | What it does |
|---|---|
| `caio welcome` | What this is and what to do next, in plain English |
| `caio demo` | Invent a fictional company to try it on |
| `caio pull` | Download your own account's data into the lake |
| `caio check` | What your data can answer, and what's missing |
| `caio scan` | Sweep conversation content for sensitive-data patterns |
| `caio judge` | Read the flagged passages and decide if they're real |
| `caio verify` | Independently re-read whatever the first pass called real |
| `caio report` | Build one report, or all three |
| `caio deliver` | Package a built report for sending |
| `caio all` | Check, scan and build everything — **the Monday-morning command** |

Add `--help` to any of them for the full option list.

## Downloading is always asked for by name

Downloading is a command of its own on purpose, and `caio all` never does it.
Pulling touches a network and needs credentials, and someone who asked for a
report should not get a network call they didn't ask for. So you ask for it by
name:

```bash
caio pull analytics  --data-dir data   # cost and usage
caio pull directory  --data-dir data   # who holds a seat
caio pull everything --data-dir data   # both of the above — never content
caio pull content    --data-dir data   # conversation text, consent-gated

python3 -m pipeline.fetch.consent --data-dir data --show   # what you consented to
```

Anything you put after the target is passed straight to the fetcher, so
`caio pull analytics --since 2026-04-01` and `--full-refresh` work as before.

## Prefer not to install?

Every command works without `pip install -e .` if you tell Python where the code
lives. `caio X` becomes `PYTHONPATH=plugins/100x-chief-ai-officer python3 -m pipeline.cli X`:

```bash
pip install -r requirements.txt
export PYTHONPATH=plugins/100x-chief-ai-officer

python3 -m pipeline.cli demo --out data-demo
python3 -m pipeline.cli all  --data-dir data-demo --out-dir _reports
```

## The six stages

One command runs them in order; each also runs alone.

| Stage | What it does |
|---|---|
| `x0` gap check | What can be answered, what's missing, which dates |
| `x1` pull | Download into local storage; content lands raw before parsing |
| `x2` metrics | Arithmetic over the data; no model reads anything here |
| `x3` classify | Something reads the flagged material and judges it |
| `x4` verify | An independent pass tries to break x3's conclusions |
| `x5` lock | The findings ledger, then render and validate |

## What's in this repository

```text
plugins/100x-chief-ai-officer/
  pipeline/
    fetch/        the only modules that touch a network
    lake/         how downloaded data is stored and read back
    detectors/    nine families of sensitive-data pattern
    stages/       gap check, scan, metrics, findings ledger
    render/       compose, build, validate, deliver
    demo/         the fictional company
  skills/         five skills, for using this from Claude
  hooks/          the greeting shown the first time after installing
  references/     the data contract, the pipeline, the privacy rules
tools/scrubber/     the publication gate — run before anything ships
docs/mock-reports/  three example reports, fictional company
tests/              81 tests, run end to end with no credentials
```

## Requirements

Python 3.10 or newer, and `pip`. Two dependencies: `requests` for downloading,
`pandas` for the analysis. Storing data, the gap check, the metrics and the
report gates are all standard-library only — so the parts that have to run in a
locked-down environment have nothing to install.

One optional extra: `pip install anthropic`, needed only if you want a model to
do the reading in `caio judge --judge anthropic`.

## Status

All six stages are implemented and tested end to end against the fictional
company, with no credentials needed. 81 tests.

The reading passes ship with three judges: an offline stand-in for the demo and
for tests, a worksheet a person fills in, and a Claude model. The stand-in is
not a real reading, and every report built with it says so on its face.

## Going deeper

- [`docs/architecture.md`](architecture.md) — four diagrams: what ships, what
  a person does with it, how data moves, and the rule separating a match from a
  finding.
- [`references/pipeline_spine.md`](../plugins/100x-chief-ai-officer/references/pipeline_spine.md)
  — what each stage does and where to add things.
- [`references/data_contract.md`](../plugins/100x-chief-ai-officer/references/data_contract.md)
  — every table and column of downloaded data.
- [`references/privacy_rules.md`](../plugins/100x-chief-ai-officer/references/privacy_rules.md)
  — the privacy rules the code enforces, and why each exists.
- [`HANDOFF.md`](../HANDOFF.md) — what's done and what's next.
