# ADR 0010: Pinned containers, a loopback-only compose stack and logical backups

- Status: Accepted
- Date: 2026-10-24
- Scope: Stage 10 reproducible release

## Context

The roadmap's last stage asks for Docker, a local compose stack, migration,
start and check instructions, CI extended with integration checks and
evaluations, and a local backup export and restore with a verified result.
A clean environment must come up from the instructions. Local results, real
provider checks and remote CI must be listed separately, and publishing or
production deployment are not part of the stage.

## Decision

1. **One Dockerfile, two targets.**
   - `app` carries runtime dependencies; `ui` adds the `ui` group.
   - Both install from `uv.lock` with `--locked`.
   - The Python 3.12.15 slim and uv 0.12.23 base images are pinned by digest.
   - Processes run as an unprivileged user.
   - The build context excludes Git history, `.env`, `var/`, docs and tests.
2. **Compose for local learning only.**
   - PostgreSQL uses the same digest as CI.
   - A one-shot `migrate` service runs first; `api` starts after it with a
     health check, then `receiver`, `worker` and `ui`.
   - Published ports bind `127.0.0.1`.
   - Passwords and the webhook secret are required variables (`:?`), never
     stored values.
3. **Worker in the receiver's network namespace.**
   - Stage 09 allows only loopback destinations, and each container has its
     own loopback.
   - Sharing the receiver's namespace keeps that rule without a
     container-specific exception.
4. **An HTTP smoke check.**
   - `app.smoke` prepares fictional data through the database, then drives
     the scenario with the interface's HTTP client and waits for delivery.
   - The same command checks native and container setups and runs in CI.
5. **Logical backups with verification.**
   - `app.backup` exports canonical JSON Lines per table plus a manifest with
     counts and SHA-256, from one `REPEATABLE READ` snapshot.
   - It restores only into an empty database at the same revision.
   - Identity values are kept with `OVERRIDING SYSTEM VALUE` and the sequence
     is moved forward.
   - Nullable foreign keys that close a cycle are filled in after loading.
   - Before commit, a fresh in-memory export must equal the manifest.
   - `check` re-verifies hashes and reproduces reconciliations.
6. **CI jobs.** `evaluations` runs the suite and uploads the report. `compose`
   builds images, starts the stack, runs the smoke check and a backup, wipe,
   restore and verify cycle with per-run random secrets. Actions are pinned
   by SHA.
7. **Version 0.10.0.** It names the state after Stage 10, not production
   readiness.

## Consequences

- One command builds and starts the stack, and one command checks it. CI
  repeats both.
- The backup is independent of the PostgreSQL client version and checks
  itself. However:
  - it is slower than `pg_dump` for large data and is not encrypted or
    signed;
  - it supports only empty-target restores at the same revision.
- The worker's shared namespace couples its lifetime to the receiver.
- The `ui` image is large.
- Verification ran on arm64 only; amd64 builds rely on CI, which is not
  confirmed.

## Alternatives considered

- **`pg_dump` and `pg_restore`.** Standard and fast, but they need a client
  whose version matches the server inside the app image. They also verify
  nothing at the application level.
- **Allow the service name `receiver` as a webhook host.** This would weaken
  the Stage 09 loopback-only rule for containers.
- **One image with Streamlit for everything.** Simpler, but the API, worker
  and receiver would carry hundreds of megabytes they never use.
- **Tags without digests.** Readable, but a tag can move and change a
  rebuild silently.
