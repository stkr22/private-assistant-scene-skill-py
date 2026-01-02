# Build stage: Python 3.13.9-trixie
FROM docker.io/library/python:3.14.2-trixie@sha256:8d4749e029b5ba3311b080d349e6b6fe8bebece42b824b0e897b752c60eff2fc AS build-python

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:0.9.21@sha256:15f68a476b768083505fe1dbfcc998344d0135f0ca1b8465c4760b323904f05a /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependencies and pre-built wheel
COPY dist/*.whl /app/dist/

RUN --mount=type=cache,target=/root/.cache \
    uv venv && \
    uv pip install dist/*.whl

# runtime stage: Python 3.13.9-slim-trixie
FROM docker.io/library/python:3.14.2-slim-trixie@sha256:3955a7dd66ccf92b68d0232f7f86d892eaf75255511dc7e98961bdc990dc6c9b

ENV PYTHONUNBUFFERED=1

# Create non-root user
RUN addgroup --system --gid 1001 appuser && adduser --system --uid 1001 --no-create-home --ingroup appuser appuser

WORKDIR /app
COPY --from=build-python /app /app

ENV PATH="/app/.venv/bin:$PATH"
# Set the user to 'appuser'
USER appuser

ENTRYPOINT ["private-assistant-scene-skill"]
