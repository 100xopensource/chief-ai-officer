# The data contract

What lives in the lake, what shape it has, and which stage is allowed to touch
it. This is the seam between the modules that talk to a network and everything
above them. Anything reading the lake reads it through here; nothing above the
pull stage talks to an API.

## The shape on disk

```
data/
  raw/compliance/<chat-id>.json     every chat's full API response, before parsing
  <dataset>/week=YYYY-Www/part.jsonl   partitioned rows, one JSON object per line
  <dataset>/snapshot.jsonl             point-in-time datasets with no time axis
  _state.json                          watermarks and what each pull last did
  _reports/<run-id>/candidates.jsonl   what the scan matched
  _reports/ledger.json                 findings and how they moved between runs
```

Partitions are by ISO week because that is the unit reports are produced in.
A row's `day` field is the authority; the partition it sits in is an index.

## Raw before parsed, always

Every chat's full response is written to `raw/` before anything parses it.
This is the most important rule in the contract.

A parsing bug should cost a local re-parse, not another full pull. Pulling
conversation content is slow, rate-limited, and touches material somebody had
to consent to reading. Re-parsing is free and offline. The predecessor flattened
conversations to plain text at pull time and discarded tool calls, tool results
and attachments — which meant its security checks could only ever be proxies for
the thing they wanted to check, and there was no way to fix that without pulling
everything again.

## The datasets

### From the analytics API — cost and usage

| Dataset | Grain | Carries |
|---|---|---|
| `analytics_cost` | day × product × model | `amount_usd`, `requests` |
| `analytics_user_cost` | day × person | `amount_usd`, `requests` |
| `analytics_usage` | day × product × model | token counts, including cache |
| `analytics_users` | day × person | per-product activity counts |
| `analytics_connectors` | day × connection | call counts, error counts |
| `analytics_skills` | day × capability | invocations, distinct people |
| `analytics_summaries` | day | seats assigned, active people |

### From the directory API — who exists

| Dataset | Grain | Carries |
|---|---|---|
| `directory_users` | snapshot | `user_id`, email, `created_at`, role |
| `directory_groups` | snapshot | group name, member count |
| `directory_group_members` | snapshot | membership |
| `directory_roles` | snapshot | role names |

`created_at` on a person matters more than it looks: it is what makes it
possible to say a silent seat was silent rather than new.

### From the compliance API — what was said

Parsed out of `raw/`, never fetched directly into these shapes.

| Dataset | Grain | Carries |
|---|---|---|
| `dim_chat` | conversation | owner, model, timestamps, message count |
| `fact_message` | message | role, turn, sizes, flags |
| `fact_block` | piece of a message | `surface`, `tool_name`, `is_error`, `truncated` |
| `fact_attachment` | attached file | `display_name`, `has_name`, whether the body is retrievable |

## The four surfaces

`fact_block.surface` is the field the whole exposure side turns on, because it
answers "who can fix this":

| Surface | What it is | Who owns a problem found here |
|---|---|---|
| `typed_prompt` | a person typed it | policy and training |
| `tool_input` | the assistant sent it into a tool | engineering |
| `tool_output` | a tool handed it back | engineering |
| `attachment` | it arrived in an attached file | policy and retention |

Errors thrown by tools are **not** a fifth surface. An error is a property of
what a tool returned, so it is a flag (`is_error`) on `tool_output`. Making it
a surface once caused the same material to be counted twice.

## Truncation

Source payloads are cut off at about 10,000 characters. `raw.SOURCE_TRUNCATION_CHARS`
holds the exact figure, and `fact_block.truncated` marks anything at the limit.
Nothing past the cut can be examined by anything, which is why every exposure
count is published as a floor rather than a total.

## Reading it

```python
from pipeline.lake import datalake as lake     # streaming, standard library only
rows = lake.iter_dataset(root, "analytics_cost")

from pipeline.lake import lake_read            # pandas, for analysis
frame = lake_read.read_dataset("analytics_cost", data_dir)
```

Metric stages use the first. It costs nothing to import and works on a machine
that has just cloned the repository.

## Writing it

Only `pipeline/fetch/*` writes datasets, and only `pipeline/lake/raw.py` writes
the raw store. Writes are atomic: a partition is written to a temporary file and
moved into place, so a run interrupted halfway leaves the previous partition
intact rather than half of a new one.

`_state.json` records watermarks so the next pull is incremental. It records
what a run believed it wrote; the directory records what exists. When they
disagree the directory is right, and the gap check reports the disagreement.
