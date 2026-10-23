# Bounded review agent

[README](../../README.md) · [Stage 07 guide (RU)](../STAGE_07.md) ·
[ADR 0007](../adr/0007-bounded-review-agent.md) · [API usage](usage.md)

The agent helps an employee understand a workflow. A model calls read-only
tools and returns an explanation and a draft next step. Code validates every
call, enforces limits, checks the draft against stored facts and keeps a
trace without document text. The draft never changes the workflow. Stage 07
runs only deterministic offline providers; no real model is connected.

## Run it

Requires `DATABASE_URL` and `alembic upgrade head` (revision `0005`).

```bash
uv run --locked python -m app.agent_demo
uv run --locked python -m app.agent_demo show RUN_ID
```

Over HTTP, with a clerk's or approver's token:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8003/workflows/WF-1/agent-runs
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8003/agent-runs/RUN_ID
```

Starting a run needs the `assist` permission (roles `clerk` and `approver`) in
the workflow's business unit; reading a stored run needs `view` there. The
HTTP API always uses the `demo-rules/1` provider.

## Provider port

`ModelProvider.complete(ModelRequest) -> ModelTurn`. A request has fixed
instructions, tool specs with strict JSON Schemas, the history and the answer
schema. A turn is `tool_calls` (call ID, tool name, JSON arguments), `final`
(an answer object) or `refusal`. All results of one turn go back together,
each linked by call ID and flagged as an error when refused.

| Provider | Use |
| --- | --- |
| `DemoRulesProvider` (`demo-rules/1`) | Rule-based stand-in: reads workflow and reconciliation, fields for discrepancies, then drafts |
| `ScriptedProvider` | Replays fixed turns or functions of the request; used to simulate misbehaving models |

## Tools

| Tool | Returns |
| --- | --- |
| `get_workflow` | State, version, unit, pinned versions, basic-check codes, decider |
| `get_reconciliation` | Latest snapshot: status, freshness and stale reasons, discrepancies; lines with `include_lines` |
| `get_extracted_fields` | Field names, statuses and values (marked as document data, cut to 200 characters); optional `names` filter |
| `get_audit_trail` | Commands, states, versions and actors of the last 20 events |

Every schema requires `workflow_id`, forbids extra keys and bounds strings.
Each call opens its own read-only transaction and re-checks the employee's
`view` permission in the workflow's unit.

## Limits and outcomes

| Limit (`AgentLimits`) | Default | Outcome |
| --- | --- | --- |
| `max_provider_calls` | 6 | stop `provider_call_limit` |
| `max_tool_calls` | 10 | stop `tool_call_limit` |
| `max_refused_calls` | 3 | stop `too_many_refusals` |
| `provider_timeout_seconds` | 20 | stop `provider_timeout` |
| `tool_timeout_seconds` | 5 | stop `tool_timeout` |
| `max_result_bytes` | 8192 | call refused `result_too_large` |

Refused calls (`invalid_call`, `unknown_tool`, `invalid_arguments`,
`out_of_scope`, `not_found`, `missing_permission`, `unauthenticated`,
`result_too_large`, `tool_failed`) go back to the model as errors. Other stops
without a draft: `provider_error`, `provider_refusal`,
`invalid_final_answer`.

## Draft check

The final answer must match `{explanation (1-2000 chars), next_step,
cited_codes?}`. `next_step` is one of `ready_for_approval`,
`request_new_reconciliation`, `recommend_rejection`, `needs_human_review`.
`review_draft` replaces it with `needs_human_review` when the workflow is
closed (`workflow_closed`), a cited code is unknown (`unsupported_citation`),
or `ready_for_approval` lacks support (`workflow_not_awaiting_approval`,
`no_reconciliation`, `reconciliation_stale`, `reconciliation_not_matched`).
Both the proposed and the shown step are stored.

## Trace

`agent_runs` and `agent_steps` (append-only) keep the provider, stop reason,
counters, steps, tool names, error codes, sizes, durations and SHA-256 of
arguments, results and the explanation. They keep no prompts, no document text
and no explanation: the explanation is returned once to the caller.

## Fields proposed by a model

`ModelFieldExtractor(provider)` implements the Stage 04 `FieldExtractor`
port. It sends page texts as data and expects the strict field schema. Valid
output still goes through `assess_extraction`, so a value that does not match
its cited text gives `unverified_evidence`. Invalid output, refusals, timeouts
and exceptions give an all-missing result that needs review. The extraction is
stored as `model:<provider>`.

## Limits of this stage

No real LLM adapter; timed-out threads are abandoned, not killed; a crash in
the middle of a run stores no trace; no per-user quotas. See
[verification](../VERIFICATION.md).
