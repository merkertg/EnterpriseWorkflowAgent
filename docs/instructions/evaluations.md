# Evaluation suite

[README](../../README.md) · [Stage 09 guide (RU)](../STAGE_09.md) ·
[ADR 0009](../adr/0009-outbox-delivery-and-evaluations.md) ·
[Outbox delivery](outbox.md) · [Testing](testing.md)

31 named cases with explicit expected outcomes. Each runs the real services
on PostgreSQL and, for delivery, a real sandbox receiver on a free loopback
port in the same process. Providers are deterministic (`demo-rules/1` and
scripted models); no network or real LLM is used.

## Run

```bash
uv run --locked python -m app.evals list                   # no database needed
uv run --locked python -m app.evals run                    # needs DATABASE_URL
uv run --locked python -m app.evals run --case timeout-webhook --reports var/evals
```

`run` writes `eval-<UTC time>-<run>.json` and `.md` to `var/evals/` (ignored by
Git) and prints one line per case. Exit codes: 0 all passed, 1 a case failed
or the database failed, 2 missing `DATABASE_URL` or an unknown case ID.

The cases write fictional workflows with a random run suffix. Before delivery
cases the suite delivers every pending event of the database to its own
temporary receiver; their number is reported as
`earlier_events_sent_to_eval_receiver`. Use a development database, not one
whose events matter.

## Categories

| Category | Cases | Examples |
| --- | --- | --- |
| `injection` | 3 | Instruction text is data; a model obeying a field is refused; a fabricated total fails the evidence check |
| `wrong_fields` | 3 | Wrong declared total; unit price 0.10 below the order; comma decimal |
| `ambiguous_data` | 3 | Two totals; missing currency; duplicate invoice in two workflows |
| `provider_failure` | 3 | Provider error in the agent and in extraction; provider refusal |
| `timeout` | 3 | Slow model; PDF worker that never answers; receiver slower than the worker |
| `retry` | 4 | Replayed approval; lost receiver answer; exhaustion and requeue; wrong signing secret |
| `concurrency` | 2 | Two approvals at once; two delivery workers at once |
| `malicious_document` | 6 | Renamed PNG; control characters; oversized; encrypted, malformed and text-expanding PDFs |
| `access` | 4 | Another unit; viewer approval; agent quota; request body limit |

A case passes only if its observation equals the expected dictionary
exactly. An exception is a failed case with its type and message, not a
crash. The definitions are in `app/evals/cases.py`.

## Report

The JSON and Markdown reports contain per-category totals, every case's
expected and observed outcome and duration, a fixed list of limitations and
the statement that results are not evidence of production readiness.
`tests/persistence/test_evals.py` runs the whole suite when
`TEST_DATABASE_URL` is set.

## Limits

Deterministic providers only; few fictional cases written by the code's
authors; one machine; the PDF memory limit is enforced on Linux only; timing
cases use margins of 0.2–1 s.
