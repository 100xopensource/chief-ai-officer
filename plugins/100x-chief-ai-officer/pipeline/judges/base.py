"""What a judge is given, what it must return, and the rules it applies.

The rubric below is the same one a human reviewer follows — it is lifted from
the `caio-review-candidates` skill deliberately, so the automated pass and the
manual pass are answering the same question in the same order. When they
disagree, that is a real disagreement about the material rather than an
artefact of two differently-worded briefs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

JsonObject = dict[str, Any]

VERDICT_REAL = "real"
VERDICT_FALSE_ALARM = "false_alarm"
VERDICT_UNSURE = "unsure"
VERDICTS = (VERDICT_REAL, VERDICT_FALSE_ALARM, VERDICT_UNSURE)

SEVERITIES = ("high", "internal", "low")

# How much text around the match a judge is given. Enough for context to be
# real, bounded so a judge is never handed a whole conversation to summarise —
# it is answering one question about one passage.
CONTEXT_CHARS = 4000

JUDGEMENT_RUBRIC = """\
You are judging one flagged passage from an organisation's own Claude
conversations. A pattern matched it. Nobody has read it yet. Decide whether it
is really what the pattern thought it was.

Answer three questions, in this order:

1. Is the matched thing what the pattern thought it was? A great many matches
   are placeholders (REDACTED, your-key-here, xxx), test fixtures,
   documentation examples, or a number that merely has the right shape.
2. Does it matter in this context? A credential in a public code sample is not
   a credential in a support ticket. A person's name in a press release is not
   the same name in an employee-relations case.
3. Is there an owner and an action inside the organisation's control? If
   nobody can change anything about it, it is not a finding.

Rules you must follow:

- NEVER test, call, or attempt to use a credential you see. Judge whether it is
  live from context only — where it appeared, when, what it is attached to.
  Attempting to use it is an unauthorised authentication attempt, and it turns
  a key that was already dead into an incident.
- Your reason must NOT quote or reproduce the matched value, any name, any
  email address, or any filename. Describe the shape and the context instead.
  The reason is stored and may be read by people who must not see the value.
- "false_alarm" is a good answer and the most common one. Roughly 44% of
  flagged passages survive being read. Do not stretch to confirm.
- "unsure" is better than a guess. An unsure verdict is never published as a
  finding, so it costs nothing to be honest.

Reply with these fields and nothing else:
  verdict:  real | false_alarm | unsure
  severity: high | internal | low        (only when verdict is real)
  reason:   one or two sentences, no values quoted
"""


@dataclass
class Candidate:
    """One flagged passage, with the context a judge needs to read it."""

    block_id: str
    chat_id: str
    family: str
    family_title: str
    pattern_id: str
    expect_precision: str
    surface: str
    day: str
    text: str
    tool_name: str | None = None
    integration: str | None = None
    is_error: bool = False
    truncated: bool = False
    likely_placeholder: bool = False
    # One passage can match several patterns at once. A reader reads it once and
    # judges the passage, so every family that fired on it travels together and
    # the strongest one drives `family` above.
    also_matched: tuple[str, ...] = ()

    def brief(self) -> str:
        """What the judge is shown, other than the text itself."""
        lines = [
            f"Pattern family: {self.family_title}",
            f"Where it appeared: {SURFACE_ENGLISH.get(self.surface, self.surface)}",
            f"Date: {self.day}",
        ]
        if self.tool_name:
            lines.append(f"Tool involved: {self.tool_name}")
        if self.integration:
            lines.append(f"Data connection: {self.integration}")
        if self.is_error:
            lines.append("This was an error the tool returned.")
        if self.truncated:
            lines.append("This payload was cut off at the source; text past the cut is missing.")
        if self.also_matched:
            lines.append("Other patterns that also matched this passage: "
                         + ", ".join(self.also_matched))
        return "\n".join(lines)


SURFACE_ENGLISH = {
    "typed_prompt": "typed into a prompt by a person",
    "tool_input": "sent into a tool by the assistant",
    "tool_output": "handed back by a tool inside its results",
    "attachment": "carried in an attached file",
}


@dataclass
class Verdict:
    """One judge's answer about one candidate.

    `reason` is stored on disk and travels into review queues, so it is held to
    the same rule as a published report: no values, no names, no filenames.
    """

    block_id: str
    verdict: str
    reason: str
    judged_by: str
    severity: str | None = None
    model: str | None = None
    judged_at: str = field(default_factory=lambda: _now())
    extra: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")
        if self.verdict != VERDICT_REAL:
            # A severity on a non-finding is noise that later reads as a finding.
            self.severity = None
        elif self.severity not in SEVERITIES:
            self.severity = "internal"

    def to_row(self) -> JsonObject:
        row = {
            "block_id": self.block_id,
            "verdict": self.verdict,
            "severity": self.severity,
            "reason": self.reason,
            "judged_by": self.judged_by,
            "model": self.model,
            "judged_at": self.judged_at,
        }
        row.update(self.extra)
        return row


class Judge(Protocol):
    """Anything that can read a candidate and return a verdict."""

    name: str

    def judge_one(self, candidate: Candidate) -> Verdict:
        ...

    def close(self) -> None:
        ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
