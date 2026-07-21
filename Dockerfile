# syntax=docker/dockerfile:1

# Optional, non-root, local-stdio container image for people-context.
#
# This image is a convenience distribution of the same stdio MCP server that
# `uvx --from people-context people-context-mcp` runs. It is never the default or
# only supported way to run the project, and it is not a security sandbox: the
# server executes local Python with the container user's filesystem permissions.
#
# The build resolves the locked dependency set with uv into a self-contained
# virtual environment, then copies only that environment and the application into
# a minimal Python runtime. The container runs as an unprivileged user and reads
# and writes a single SQLite database on an explicitly mounted volume.
#
# Base images are pinned by immutable digest. The human-readable tags record the
# reviewed versions (uv 0.9.30 on Python 3.12; Debian bookworm slim). Bumping
# either base image is a reviewed change to the pinned digests below.

# ---- Builder: uv + Python 3.12 --------------------------------------------
FROM ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install the third-party dependencies first, using only the lockfile and project
# metadata, so this expensive layer is cached across source-only changes.
# `--no-dev` omits development tooling and the optional semantic extra, keeping
# the runtime image small and free of any model-download dependencies.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --locked --no-dev --no-install-project

# Install the project itself into the same environment.
COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---- Runtime: minimal Python 3.12 -----------------------------------------
FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime

# Create an unprivileged user and a data directory it owns. The database is never
# baked into an image layer; it lives on the mounted /data volume.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app \
    && install -d -o app -g app /data

# Copy the fully resolved application environment from the builder. The uv build
# image is derived from python:3.12-slim-bookworm, so the interpreter path inside
# the virtual environment matches this runtime image.
COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PEOPLE_CONTEXT_DB=/data/people.db \
    PYTHONUNBUFFERED=1

# The SQLite database is expected on an explicitly mounted volume.
VOLUME ["/data"]

USER app
WORKDIR /app

# Default to the stdio MCP server. Loopback HTTP remains opt-in through arguments
# and is never the container default; the human-operated CLI is available by
# overriding the entrypoint with `people-context`.
ENTRYPOINT ["people-context-mcp"]
