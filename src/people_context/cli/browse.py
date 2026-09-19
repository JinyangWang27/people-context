"""`pctx browse` — a short-lived, loopback-only browser page over the existing use cases (M30.1, M30.2).

It is not a service: it runs until Ctrl-C or until the page says it is done, serves one browser on
127.0.0.1, and leaves nothing behind — no daemon, PID file, or configuration. There is no `--host`,
so nothing but loopback can be bound.

The per-launch token is printed once, in the URL on stdout, and nowhere else. uvicorn's access log
is off and its logging never formats a request path, because the default access line would record
the first `GET /?token=...` before application code runs.
"""

from __future__ import annotations

import argparse
import contextlib
import secrets
import socket
import sys
import threading
import webbrowser
from typing import Any

import uvicorn

from people_context.adapters.runtime import ApplicationRuntime
from people_context.adapters.web import LOOPBACK_HOST, create_browse_app
from people_context.cli.imports import REVIEW_DISCLOSURE_WARNING
from people_context.cli.rendering import import_review_lines
from people_context.cli.sources import SOURCES_DISCLOSURE_WARNING
from people_context.config import SENSITIVE_CONTEXT_ENV, process_elevation_enabled

#: Only uvicorn's own warnings and errors reach stderr, as the bare message; its access logger has
#: no handler and does not propagate, so no request line is ever written anywhere.
_LOG_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(levelname)s: %(message)s"}},
    "handlers": {"stderr": {"class": "logging.StreamHandler", "formatter": "plain", "stream": "ext://sys.stderr"}},
    "loggers": {
        "uvicorn": {"handlers": ["stderr"], "level": "WARNING", "propagate": False},
        "uvicorn.error": {"level": "WARNING"},
        "uvicorn.access": {"handlers": [], "level": "CRITICAL", "propagate": False},
    },
}


def _address_reuse_option() -> int | None:
    """Return the bind option that keeps a busy port refused while allowing a prompt restart.

    On POSIX, `SO_REUSEADDR` lets a port left in TIME_WAIT by the last session be bound again at
    once while a port another socket listens on is still refused. Windows' default bind already
    behaves that way; there `SO_REUSEADDR` would share a busy port and `SO_EXCLUSIVEADDRUSE` would
    delay the restart, so no option is set — the same choice asyncio's `create_server` makes.
    """
    if sys.platform == "win32":
        return None
    return socket.SO_REUSEADDR


def cmd_browse(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Serve the local viewer and batch review page until interrupted."""
    if not 0 <= args.port <= 65535:
        print("Error: --port must be between 0 and 65535.", file=sys.stderr)
        return 2
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reuse_option = _address_reuse_option()
    if reuse_option is not None:
        listener.setsockopt(socket.SOL_SOCKET, reuse_option, 1)
    try:
        listener.bind((LOOPBACK_HOST, args.port))
        listener.listen()
    except OSError as exc:
        listener.close()
        print(f"Error: cannot listen on {LOOPBACK_HOST}:{args.port}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    port = int(listener.getsockname()[1])
    token = secrets.token_urlsafe(32)
    url = f"http://{LOOPBACK_HOST}:{port}/?token={token}"

    # The elevation is the operator's, read once from this process's environment; the page has no
    # control that changes it.
    include_sensitive = process_elevation_enabled(SENSITIVE_CONTEXT_ENV)
    server: uvicorn.Server | None = None

    def stop() -> None:
        if server is not None:
            server.should_exit = True

    app = create_browse_app(
        lambda: contextlib.nullcontext(runtime),
        token=token,
        port=port,
        include_sensitive=include_sensitive,
        on_done=stop,
        review_warning=REVIEW_DISCLOSURE_WARNING,
        sources_warning=SOURCES_DISCLOSURE_WARNING,
        review_lines=import_review_lines,
    )
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_config=_LOG_CONFIG, lifespan="on"))

    print(url, flush=True)
    print(f"Warning: {REVIEW_DISCLOSURE_WARNING}", file=sys.stderr)
    print("Serving on loopback only. Press Ctrl-C, or Done in the page, to stop.", file=sys.stderr, flush=True)
    if args.open:
        # A text-mode browser blocks `webbrowser.open` until it exits, so it must not hold up the
        # server it is about to talk to; the socket is already listening and queues its request.
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
    return 0
