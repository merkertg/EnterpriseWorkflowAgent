# API usage

[README](../../README.md) · [Quickstart](quickstart.md)

Start Uvicorn on `127.0.0.1:8003` before these examples. No credentials are
required for the current liveness endpoint.

## Health

```bash
curl -i http://127.0.0.1:8003/health
```

Default body:

```json
{"status": "ok", "service": "enterprise-workflow-agent"}
```

| Field | Contract |
| --- | --- |
| `status` | The literal string `ok` |
| `service` | The application instance's configured `APP_NAME` |

HTTP `200` confirms that the application handled this request. It does not
check a database, document extraction, corporate rules, an LLM, or a complete
approval workflow. Current basic rules run separately through the Python domain.

`POST /health` returns `405 Method Not Allowed`. An unknown route returns
`404 Not Found`.

## API documentation

- [Swagger UI](http://127.0.0.1:8003/docs): execute `GET /health` interactively.
- [ReDoc](http://127.0.0.1:8003/redoc): read the generated API description.
- [OpenAPI JSON](http://127.0.0.1:8003/openapi.json): inspect the API contract.

The `HealthResponse` schema defines the response. There are no invoice,
procurement, approval, upload or agent endpoints in Stage 01.

Stage 02 adds an in-memory Python domain and CLI, rather than additional HTTP
endpoints. See [domain usage](domain.md) for its demonstration and limits.
Stage 03 adds PostgreSQL persistence used through Python and
`python -m app.persistence_demo`; it adds no HTTP endpoints, and `/health` does
not check the database. See [persistence](persistence.md).
Stage 04 document intake is available through `python -m app.documents_cli`;
the HTTP upload endpoint is planned with authorization in Stage 06. See
[documents](documents.md).

For settings and server commands, see [Quickstart](quickstart.md).
