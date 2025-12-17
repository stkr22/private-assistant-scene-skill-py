# Build stage: Python 3.13.9-trixie
FROM docker.io/library/python:3.14.2-trixie@sha256:dadbe13e4d921ffd7f10dfe21c4b5bd18f7d322740309f42a8b7a22480f3bdff AS build-python

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:0.9.18@sha256:5713fa8217f92b80223bc83aac7db36ec80a84437dbc0d04bbc659cae030d8c9 /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependencies and pre-built wheel
COPY dist/*.whl /app/dist/

RUN --mount=type=cache,target=/root/.cache \
    uv venv && \
    uv pip install dist/*.whl

# runtime stage: Python 3.13.9-slim-trixie
FROM docker.io/library/python:3.14.2-slim-trixie@sha256:2751cbe93751f0147bc1584be957c6dd4c5f977c3d4e0396b56456a9fd4ed137

ENV PYTHONUNBUFFERED=1

# Create non-root user
RUN addgroup --system --gid 1001 appuser && adduser --system --uid 1001 --no-create-home --ingroup appuser appuser

WORKDIR /app
COPY --from=build-python /app /app

ENV PATH="/app/.venv/bin:$PATH"
# Set the user to 'appuser'
USER appuser

ENTRYPOINT ["private-assistant-scene-skill"]
