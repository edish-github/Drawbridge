# One image for every Python service.
#
# The fleet is one codebase — the agents share `shared/`, the services share the agents, and the
# gateway is the single chokepoint all of them pass through. Building one image and selecting the
# entrypoint per deployment keeps that true: a service cannot drift onto a different version of
# the policy module than the agent it talks to, because there is only one copy in the layer.
#
# The alternative, an image per service, saves a few megabytes and costs the property the whole
# security argument rests on.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall the world.
COPY pyproject.toml ./
RUN pip install --no-cache-dir "." && pip install --no-cache-dir uvicorn firebase-admin httpx

COPY shared/ ./shared/
COPY agents/ ./agents/
COPY services/ ./services/
COPY scripts/ ./scripts/
COPY infra/ ./infra/

# Runs as nobody. A container that is root is a container where a remote-code-execution bug in a
# PDF parser becomes a container escape rather than an incident.
RUN useradd --create-home --shell /usr/sbin/nologin drawbridge \
    && chown -R drawbridge:drawbridge /app
USER drawbridge

# Cloud Run supplies PORT. SERVICE selects which entrypoint this revision is.
ENV PORT=8080 SERVICE=hello

# One process, no reload, and a single worker: concurrency is Cloud Run's to manage, and a
# gunicorn pool inside a container that already scales horizontally is two schedulers arguing.
CMD ["sh", "-c", "exec python -m uvicorn services.${SERVICE}.main:app --host 0.0.0.0 --port ${PORT}"]
