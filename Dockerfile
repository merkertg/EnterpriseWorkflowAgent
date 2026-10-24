# syntax=docker/dockerfile:1
# Reproducible images for the local learning stack (Stage 10).
#
# Base images are pinned by digest and Python packages come only from
# uv.lock (--locked), so a rebuild resolves exactly the same software.
# Two targets share one source tree:
#   app - API, migrations, outbox worker, sandbox receiver and CLI tools
#   ui  - the same code plus the Streamlit "ui" dependency group
# Images run as an unprivileged user and contain no secrets; settings come
# from the environment at run time.

ARG PYTHON_IMAGE=python:3.12.15-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
# Use the image's Python, compile bytecode once, copy instead of hard-linking.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM build AS build-ui
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --group ui

FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --create-home --uid 10001 --user-group app \
    && mkdir -p /app/var /data \
    && chown app:app /app/var /data
WORKDIR /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app .streamlit ./.streamlit
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app
COPY --chown=app:app sandbox ./sandbox
COPY --chown=app:app ui ./ui
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER app

FROM runtime AS app
COPY --from=build /opt/venv /opt/venv
EXPOSE 8003
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]

FROM runtime AS ui
COPY --from=build-ui /opt/venv /opt/venv
EXPOSE 8501
CMD ["streamlit", "run", "ui/app.py", "--server.address=0.0.0.0"]
