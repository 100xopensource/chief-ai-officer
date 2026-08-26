"""The offline judge. For the synthetic lake, CI and the test suite.

What this is
------------
A deterministic stand-in that applies the cheap, mechanical parts of the rubric
— is it a placeholder, does the context look like documentation, how reliable
has this pattern been before — and nothing else. It cannot weigh whether
material matters to this organisation, which is most of what judgement is.

What this is not
----------------
It is **not** a reading. Every verdict it produces is stamped
`judged_by: demo-offline`, the pipeline carries that stamp through to the
report, and the report prints it. A report judged this way says on its own face
that no human and no model read anything, which is the only honest thing it can
say.

It exists so the whole pipeline can be exercised end to end by anyone who has
just cloned the repository, and so the confirm path, the false-alarm path and
the disagreement path are all covered by tests that need no credentials.
"""

from __future__ import annotations

import re

from pipeline.judges.base import (
    VERDICT_FALSE_ALARM,
    VERDICT_REAL,
    VERDICT_UNSURE,
    Candidate,
    Verdict,
)

NAME = "demo-offline"

# Text around a match that means it is almost certainly not live material.
DOCUMENTATION_CONTEXT = re.compile(
    r"(?i)\b(?:example|sample|placeholder|dummy|fixture|test\s+data|for\s+instance"
    r"|e\.g\.|redacted|your[-_ ](?:key|token|secret)|xxx+|lorem ipsum"
    r"|documentation|tutorial|readme)\b"
)

# Text that raises rather than lowers the odds, because it implies the material
# is operational rather than illustrative.
OPERATIONAL_CONTEXT = re.compile(
    r"(?i)\b(?:production|prod\b|live\b|customer|patient|employee|payroll|invoice"
    r"|contract|confidential|internal only|do not share|urgent)\b"
)

# Severity by family, matching how the detectors describe themselves.
FAMILY_SEVERITY = {
    "D1": "high",     # credentials
    "D2": "high",     # structured personal information
    "D3": "high",     # identity documents
    "D4": "internal",
    "D5": "internal",
    "D6": "internal",
    "D7": "low",
    "D8": "low",
    "D9": "low",
}


URL_LIKE = re.compile(r"""(?xi)
    \b(?:https?://|www\.)\S+          # a URL
  | \b[\w.-]+@[\w.-]+\.[a-z]{2,}\b   # an address
  | \b[\w-]+(?:\.[\w-]+){1,}\.(?:com|net|org|io|ai|co|dev|local|internal)\b
""")


def _prose_only(text: str) -> str:
    """The text with URLs, addresses and hostnames removed.

    Documentation words are judged on prose. `bi.internal.example.com` is a
    hostname, not a document saying "for example" — and reading it as one
    dismissed a live signed token in testing, which is exactly the false
    negative this judge must not produce.
    """
    return URL_LIKE.sub(" ", text)


class DemoJudge:
    """A stand-in judge. Deterministic, offline, and honest about being neither
    a person nor a model."""

    name = NAME

    def __init__(self, **_ignored) -> None:
        # Accepts and ignores the options the real backends take, so the two
        # are interchangeable at the call site.
        pass

    def judge_one(self, candidate: Candidate) -> Verdict:
        verdict, reason = self._decide(candidate)
        return Verdict(
            block_id=candidate.block_id,
            verdict=verdict,
            reason=reason,
            judged_by=self.name,
            severity=FAMILY_SEVERITY.get(candidate.family, "internal"),
            model=None,
            extra={
                "not_a_reading": True,
                "note": "Produced offline by a stand-in. No person and no model "
                        "read this passage.",
            },
        )

    def _decide(self, candidate: Candidate) -> tuple[str, str]:
        text = candidate.text or ""
        prose = _prose_only(text)

        if candidate.likely_placeholder:
            return (VERDICT_FALSE_ALARM,
                    "The matched value has the shape of a placeholder rather than "
                    "a real one.")

        if DOCUMENTATION_CONTEXT.search(prose):
            return (VERDICT_FALSE_ALARM,
                    "The surrounding text reads as documentation or an example "
                    "rather than operational material.")

        precision = candidate.expect_precision

        if precision == "zero-without-context":
            # This class has historically survived at none on its own. Only
            # operational context makes it worth a second look, and even then
            # the honest answer is that this judge cannot tell.
            if OPERATIONAL_CONTEXT.search(prose):
                return (VERDICT_UNSURE,
                        "This pattern means nothing on its own, and the surrounding "
                        "text is operational. It needs a real reading.")
            return (VERDICT_FALSE_ALARM,
                    "This pattern only means something in context, and nothing in "
                    "the surrounding text supplies that context.")

        if precision == "high":
            return (VERDICT_REAL,
                    "The matched shape is one that is rarely accidental, and nothing "
                    "in the surrounding text suggests an example.")

        if precision == "medium":
            if OPERATIONAL_CONTEXT.search(prose):
                return (VERDICT_REAL,
                        "A moderately reliable shape appearing in operational context.")
            return (VERDICT_UNSURE,
                    "A moderately reliable shape with no context either way. Needs "
                    "a real reading.")

        # low, or anything unrecognised
        if OPERATIONAL_CONTEXT.search(prose):
            return (VERDICT_UNSURE,
                    "A weak signal in operational context. Needs a real reading.")
        return (VERDICT_FALSE_ALARM,
                "A weak signal with no supporting context.")

    def close(self) -> None:
        pass
