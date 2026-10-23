# ADR 0007: A bounded review agent behind a provider port

- Status: Accepted
- Date: 2026-10-23
- Scope: Stage 07 agent, tools, limits, traces and model-proposed fields

## Context

The roadmap asks for an agent that proposes fields, calls permitted read and
check tools, explains and drafts the next step, without deciding for a person.
Each tool needs a strict schema, a server-side rights check, a timeout and a
result limit. Steps and provider calls are bounded; the trace holds no
document text or secrets. Invalid arguments, unknown tools, an attempt to obey
an instruction from a document, and exceeded limits must end in a controlled
refusal, and a reproducible demo must run without network. A live provider,
if ever connected, is verified and described separately.

## Decision

1. **Provider port** (`app/application/agent_protocol.py`): one method,
   `complete(ModelRequest) -> ModelTurn`. Turns are tool calls (ID, name, JSON
   arguments), a final answer, or a refusal; results return per call ID with
   an error flag. This mirrors common tool-use APIs, so a real adapter can be
   added without changing the agent. Stage 07 ships two deterministic
   providers only: a rule-based `DemoRulesProvider` and a `ScriptedProvider`
   for misbehaving-model scenarios.
2. **Read-only tools** with strict JSON Schemas (closed objects, bounded
   strings, no bool-as-int), validated by the application before execution.
   Tools are bound to the run's workflow (`out_of_scope` otherwise) and run in
   their own transactions with the employee's rights re-checked. There is no
   approval, rejection, payment or code-execution tool.
3. **Limits** on provider calls, tool calls, refused calls, per-call wall
   time (thread with timeout) and result bytes. A refused call is reported to
   the model by code; three refusals or any exceeded limit stop the run.
4. **Draft check**: the final answer has a fixed schema; `review_draft`
   compares the proposed next step and cited codes with stored facts and can
   only replace it with `needs_human_review`, recording the reason.
5. **Trace**: migration 0005 adds append-only `agent_runs` and `agent_steps`
   holding names, codes, counts, durations, sizes and SHA-256 values. Prompts,
   document text and the explanation are not stored; the explanation is
   returned once.
6. **Permission** `assist` (clerk, approver) starts a run; `view` reads it.
7. **Model-proposed fields**: `ModelFieldExtractor` implements the Stage 04
   `FieldExtractor` port; invalid output becomes an all-missing result, and
   valid output still passes the existing evidence check.

## Consequences

Model behaviour can be wrong without changing workflow state or bypassing a
check, and the tests can show this with scripted bad models. A real model
adapter remains to be written and verified. Timed-out threads are abandoned
rather than killed, and a process crash in the middle of a run loses that
run's trace. Explanations cannot be shown again later.

## Alternatives considered

- **Let the model call write commands with a confirmation flag:** a flag set
  by the model is still the model's decision; decisions stay with Stage 06
  commands.
- **Store full prompts and responses for debugging:** they would contain
  document text; hashes and sizes are enough to correlate.
- **Process-based timeouts for tools:** stronger isolation, but tools need
  database sessions; a statement timeout bounds the database side.
- **A vendor SDK now:** would add a dependency and network access that this
  offline stage cannot verify.
