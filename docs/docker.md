# Docker image

Status: Delivered as part of [M8 — Distribution & reach](roadmap.md#m8--distribution--reach).

The container image is an **optional** convenience distribution of the same local stdio MCP server that
`uvx --from people-context people-context` runs. It is not the default or recommended install path, and it is
**not a security sandbox**: the server runs local Python with the container user's filesystem permissions, exactly
like every other integration.

Prefer `uvx`, `uv tool install`, the MCPB Desktop bundle, or a plugin unless a container specifically fits your
deployment. See the [README quick start](../README.md#quick-start).

## What the image is

- A multi-stage build: dependencies are resolved with `uv` from the committed `uv.lock`, then only the resulting
  virtual environment and application are copied into a minimal `python:3.12-slim-bookworm` runtime.
- Base images are pinned by immutable digest in the [`Dockerfile`](../Dockerfile).
- The container runs as an unprivileged user (`uid 10001`), never `root`.
- The default entrypoint is the **stdio** MCP server. Loopback HTTP stays opt-in through arguments and is never the
  container default.
- Runtime makes no outbound network request. As everywhere else, only the explicit
  `pctx reindex --semantic` command may download the pinned model, and the semantic extra is not
  installed in this image.

## Supported architecture

The published GHCR image is **single-architecture `linux/amd64`**. Multi-platform manifests are intentionally out
of scope. On a different architecture (for example Apple Silicon without amd64 emulation), build the image locally
instead — the `Dockerfile` builds natively for your platform:

```bash
docker build -t people-context:local .
```

## Database volume and environment

The database is never baked into an image layer. The image sets `PEOPLE_CONTEXT_DB=/data/people.db` and declares
`/data` as a volume; mount your own storage there so the SQLite file survives container restarts.

`PEOPLE_CONTEXT_DB` is the standard database-path environment variable (see
[Database location](../README.md#database-location)); the image only sets a container default. Override it to place
the database elsewhere under the mounted volume.

### Named volume (works out of the box)

A named volume inherits the image's `/data` ownership, so the unprivileged user can write to it with no extra
flags:

```bash
docker volume create people-context-data
docker run --rm -i \
  -v people-context-data:/data \
  ghcr.io/jinyangwang27/people-context:latest
```

### Bind mount (match host ownership)

A host bind mount keeps the host directory's ownership. Run as your own user so the mounted directory is writable:

```bash
mkdir -p ./people-data
docker run --rm -i \
  --user "$(id -u):$(id -g)" \
  -v "$(pwd)/people-data:/data" \
  ghcr.io/jinyangwang27/people-context:latest
```

The server reads and writes `/data/people.db` inside the container, which is your `./people-data/people.db` on the
host.

## Running the CLI

The human-operated CLI ships in the same image; override the entrypoint to reach it. For example, to print the
resolved database path:

```bash
docker run --rm \
  -v people-context-data:/data \
  --entrypoint pctx \
  ghcr.io/jinyangwang27/people-context:latest db-path -v
```

## MCP client configuration

Point a stdio-capable client at `docker run`. The container must run interactively (`-i`) so the client can speak
the stdio protocol over the container's standard input and output:

```json
{
  "mcpServers": {
    "people-context": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "people-context-data:/data",
        "ghcr.io/jinyangwang27/people-context:latest"
      ]
    }
  }
}
```

## Building locally

```bash
docker build -t people-context:local .
docker run --rm people-context:local --help
```

## Publishing

The [`docker-publish.yml`](../.github/workflows/docker-publish.yml) workflow builds the image and pushes it to
GHCR from a `v*` release tag. A normal tag push triggers it directly; Release Please explicitly dispatches it at
the new tag because GitHub suppresses workflows caused by `GITHUB_TOKEN`-created tags. The workflow rejects
branch-based dispatches, authenticates with the workflow-scoped `GITHUB_TOKEN` (`packages: write`) — no
long-lived registry secret is used — and publishes both the exact version tag and `latest` for `linux/amd64`.
Base-image digests and the reviewed `uv` version are pinned in the `Dockerfile`.

### One-time: make the package public

GHCR creates a container package as **private** on its first push, and `GITHUB_TOKEN`'s `packages: write` scope
can push but cannot change visibility. So the anonymous `docker run ghcr.io/jinyangwang27/people-context:latest`
commands above only work after an operator makes the package public **once**, after the first release tag
publishes it:

1. Open the package at `https://github.com/users/JinyangWang27/packages/container/package/people-context`.
2. **Package settings → Danger Zone → Change visibility → Public**.

This is deliberately a manual, one-time step rather than an automated one: automating it would require a
longer-lived, higher-scope token than the workflow-scoped `GITHUB_TOKEN`, which this project intentionally avoids.
After the package is public, later releases publish new tags without any further visibility change.
