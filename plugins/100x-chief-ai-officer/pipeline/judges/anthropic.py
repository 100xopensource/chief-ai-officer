"""The judge that actually reads: a Claude model, one passage at a time.

This is the only module in the project that sends conversation content to an
API. Everything about it is arranged so that fact stays visible and bounded.

What it sends
-------------
One flagged passage, plus a short brief about where it appeared. Not the
conversation, not the corpus, not a batch. A judge answering one question about
one passage cannot accidentally summarise an organisation's week.

What it gets back
-----------------
A verdict, a severity and a reason, constrained by a JSON schema so the answer
is machine-readable without parsing prose. The reason is scrubbed before it is
stored: the model is told not to quote values, and the scrubber assumes it
sometimes will anyway.

Structured output rather than a tool call
-----------------------------------------
`output_config.format` constrains the response itself. A tool-call round trip
would work too, but it adds a turn and a shape to unpack for no gain — there is
nothing to execute here, only an answer to read.

Cost
----
One request per passage. A week's corpus on the synthetic company produces a
few hundred candidates, and a real one can produce thousands, so the caller
bounds it — `x3_classify` reads strongest-first up to a limit and records the
fraction read, which is what keeps every published count a floor.
"""

from __future__ import annotations

import json
import os
import re
import time

from pipeline.judges.base import (
    CONTEXT_CHARS,
    JUDGEMENT_RUBRIC,
    SEVERITIES,
    VERDICTS,
    VERDICT_UNSURE,
    Candidate,
    Verdict,
)

NAME = "claude"

# Opus 5 is the default because a wrong judgement here is expensive in both
# directions: a false alarm published as a finding costs credibility, and a real
# exposure cleared costs more than that. The model is configurable for anyone
# who has measured a cheaper one against their own corpus.
DEFAULT_MODEL = "claude-opus-5"

# The answer is three short fields. A large output budget here buys nothing and
# invites the model to explain itself at length into a field that is stored.
MAX_TOKENS = 1024

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": list(VERDICTS),
            "description": "real if this is genuinely what the pattern thought it "
                           "was and it matters here; false_alarm if it is a "
                           "placeholder, an example, or harmless in context; "
                           "unsure if you cannot tell.",
        },
        "severity": {
            "type": "string",
            "enum": [*SEVERITIES, "none"],
            "description": "How bad it would be if confirmed. 'none' unless the "
                           "verdict is real.",
        },
        "reason": {
            "type": "string",
            "description": "One or two sentences. Must not quote the matched "
                           "value, any name, any email address or any filename.",
        },
    },
    "required": ["verdict", "severity", "reason"],
    "additionalProperties": False,
}

# The model is asked not to quote values. This assumes it sometimes will.
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
FILENAME = re.compile(r"\b[\w -]+\.(?:docx|xlsx|pptx|pdf|csv|txt|png|jpg|zip)\b", re.I)
LONG_DIGITS = re.compile(r"(?<![\d,.])\d{5,}(?!\d|[,.]\d)")
QUOTED = re.compile(r"[\"'`]([^\"'`\n]{12,})[\"'`]")


class AnthropicJudge:
    """Reads one passage per request and returns a structured verdict."""

    name = NAME

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 max_retries: int = 4, second_opinion: bool = False,
                 **_ignored) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                "the 'anthropic' package is not installed. This judge is the only "
                "part of the pipeline that needs it:\n"
                "    pip install anthropic\n"
                "Everything else, including the demo and the tests, runs without it."
            ) from None
        import anthropic as sdk

        key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CAIO_API_KEY")
        # A bare client also resolves an `ant auth login` profile, so an unset
        # key is not proof there are no credentials — let the SDK decide.
        self._sdk = sdk
        self._client = sdk.Anthropic(api_key=key) if key else sdk.Anthropic()
        self.model = model
        self.max_retries = max_retries
        # x4 runs the same backend with a different brief. It must never be
        # shown x3's reasoning, so the difference is in the prompt, not the data.
        self.second_opinion = second_opinion
        self.calls = 0

    # ------------------------------------------------------------------

    def judge_one(self, candidate: Candidate) -> Verdict:
        prompt = self._prompt(candidate)
        try:
            data = self._ask(prompt)
        except Exception as exc:  # the run continues; the passage stays unread
            return Verdict(
                block_id=candidate.block_id,
                verdict=VERDICT_UNSURE,
                reason="Could not be judged: the request failed. It stays a "
                       "candidate and is not counted as a finding.",
                judged_by=self.name,
                model=self.model,
                extra={"error": type(exc).__name__, "unjudged": True},
            )

        severity = data.get("severity")
        return Verdict(
            block_id=candidate.block_id,
            verdict=str(data.get("verdict") or VERDICT_UNSURE),
            reason=_scrub(str(data.get("reason") or "")) or "No reason given.",
            judged_by=self.name,
            severity=severity if severity in SEVERITIES else None,
            model=self.model,
            extra={"second_opinion": True} if self.second_opinion else {},
        )

    def _prompt(self, candidate: Candidate) -> str:
        stance = (
            "\nYou are the second reader. An earlier pass has already judged this "
            "passage, and you are deliberately not being shown what it concluded — "
            "your job is to reach your own answer, not to agree. If the honest "
            "answer is that it cannot be told from this passage, say unsure.\n"
            if self.second_opinion else ""
        )
        return (
            f"{JUDGEMENT_RUBRIC}{stance}\n"
            f"--- what is known about this passage ---\n{candidate.brief()}\n\n"
            f"--- the passage ---\n{candidate.text[:CONTEXT_CHARS]}\n"
        )

    def _ask(self, prompt: str) -> dict:
        """One request, with backoff on the errors that are worth retrying."""
        delay = 1.0
        last: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                self.calls += 1
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    # Judgement is the whole task, so let the model think, but
                    # keep the effort low: this is one bounded question, not an
                    # investigation, and it runs once per candidate.
                    thinking={"type": "adaptive"},
                    output_config={
                        "effort": "low",
                        "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
                    },
                    messages=[{"role": "user", "content": prompt}],
                )
                if response.stop_reason == "refusal":
                    # The model declined. That is an answer about the material,
                    # not an error, and it is never a confirmation.
                    return {"verdict": VERDICT_UNSURE, "severity": "none",
                            "reason": "The reader declined to judge this passage."}
                text = next(b.text for b in response.content if b.type == "text")
                return json.loads(text)

            except self._sdk.RateLimitError as exc:
                last = exc
                wait = float(getattr(exc, "response", None)
                             and exc.response.headers.get("retry-after") or delay)
                time.sleep(min(wait, 60.0))
                delay = min(delay * 2, 60.0)
            except self._sdk.APIStatusError as exc:
                last = exc
                if exc.status_code < 500:
                    raise  # a bad request will not get better by being repeated
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
            except self._sdk.APIConnectionError as exc:
                last = exc
                time.sleep(delay)
                delay = min(delay * 2, 60.0)

        raise last or RuntimeError("request failed")

    def close(self) -> None:
        pass


def _scrub(reason: str) -> str:
    """Remove anything the reason should not carry into the stored record."""
    reason = EMAIL.sub("[email removed]", reason)
    reason = FILENAME.sub("[filename removed]", reason)
    reason = LONG_DIGITS.sub("[value removed]", reason)
    reason = QUOTED.sub("[quoted text removed]", reason)
    return reason.strip()
