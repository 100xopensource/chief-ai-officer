"""Judges — the things that read a flagged passage and decide whether it is real.

A judge is deliberately a small, swappable interface rather than a hard-wired
call to a model, for three reasons:

  · The project has to run with no credentials. CI, the demo lake and the test
    suite all exercise the full pipeline, and none of them may require an API
    key or a network.
  · Reading a colleague's conversations is a decision an organisation makes,
    not a default a tool assumes. Somebody choosing to have a model read the
    corpus should have to name the judge that does it.
  · Every verdict records which judge produced it, so a report built by a
    stand-in judge can never be mistaken for one built by a real reader. The
    report prints the judge's name on its own face.

The judges
----------
    demo        deterministic, offline. For the synthetic lake, CI and tests.
                Never presented as a real reading, and says so in every verdict.
    worksheet   writes a review file, a person fills it in, it reads it back.
                The judge a compliance reviewer actually is.
    anthropic   a model reads the passage. Needs a key; used for live runs.

Adding one means implementing `judge_one` and registering it here.
"""

from __future__ import annotations

from pipeline.judges.base import (
    JUDGEMENT_RUBRIC,
    Candidate,
    Judge,
    Verdict,
    VERDICT_FALSE_ALARM,
    VERDICT_REAL,
    VERDICT_UNSURE,
)

__all__ = [
    "JUDGEMENT_RUBRIC",
    "Candidate",
    "Judge",
    "Verdict",
    "VERDICT_FALSE_ALARM",
    "VERDICT_REAL",
    "VERDICT_UNSURE",
    "get_judge",
    "JUDGES",
]

JUDGES = ("demo", "worksheet", "anthropic")


def get_judge(name: str, **options) -> Judge:
    """Resolve a judge by name. Imported lazily so a missing optional
    dependency only bites the person who asked for that backend."""
    if name == "demo":
        from pipeline.judges.demo import DemoJudge
        return DemoJudge(**options)
    if name == "worksheet":
        from pipeline.judges.worksheet import WorksheetJudge
        return WorksheetJudge(**options)
    if name == "anthropic":
        from pipeline.judges.anthropic import AnthropicJudge
        return AnthropicJudge(**options)
    raise ValueError(f"unknown judge '{name}'. Known: {', '.join(JUDGES)}")
