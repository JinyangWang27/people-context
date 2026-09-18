"""The loopback Starlette application behind `pctx browse` (M30.1).

The browser is a fourth client of the use cases the CLI and MCP already call: every endpoint here
wraps an existing read and returns what it returns. No use case, validation, or disclosure rule
exists only for the page.

One guard runs before routing on every request. It checks the `Host` against the bound loopback
address, a present `Origin` and `Sec-Fetch-Site` against same-origin, and the per-launch token —
from the query string on the first document load, from a header on every request after it — with
`secrets.compare_digest`. Any failure gets the same generic refusal, which names no check and
carries no data, and nothing is logged. Every response, refusals included, carries a fresh
per-response CSP nonce, `Cache-Control: no-store`, and `Referrer-Policy: no-referrer`.

SQLite connections belong to the thread that opened them, so the runtime is opened in the lifespan
on the event-loop thread and every endpoint is `async`: nothing is handed to a worker thread.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from people_context.adapters.runtime import ApplicationRuntime
from people_context.adapters.web.page import render_page
from people_context.app.exports import (
    DEFAULT_PERSON_PAGE_LIMIT,
    PersonIndexError,
    render_brief_json,
    render_person_index_json,
)
from people_context.app.imports import (
    DEFAULT_SOURCE_PAGE_LIMIT,
    UNKNOWN_SOURCE_SESSION,
    SourceInspectionError,
    import_sources_document,
    render_import_json,
)

LOOPBACK_HOST = "127.0.0.1"
TOKEN_HEADER = "X-Pctx-Token"

_NONCE_KEY = "pctx.nonce"
_REFUSAL = "Forbidden\n"
_SAME_ORIGIN_FETCH_SITES = frozenset({"same-origin", "none"})

RuntimeOpener = Callable[[], AbstractContextManager[ApplicationRuntime]]


class _Guard:
    """Refuse anything that is not this page on this loopback port with this launch's token."""

    def __init__(self, app: ASGIApp, *, token: str, port: int) -> None:
        self._app = app
        self._token = token.encode("utf-8")
        # Browsers omit the default port from both headers, so port 80 is also accepted bare.
        authorities = {f"{LOOPBACK_HOST}:{port}"} | ({LOOPBACK_HOST} if port == 80 else set())
        self._hosts = frozenset(authorities)
        self._origins = frozenset(f"http://{authority}" for authority in authorities)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        nonce = secrets.token_urlsafe(16)
        scope[_NONCE_KEY] = nonce
        started = False

        async def send_secured(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = (
                    f"default-src 'self'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'"
                )
                headers["Cache-Control"] = "no-store"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        if not self._allowed(Request(scope)):
            await PlainTextResponse(_REFUSAL, status_code=403)(scope, receive, send_secured)
            return
        try:
            await self._app(scope, receive, send_secured)
        except Exception:  # noqa: BLE001 - a traceback could carry recorded values into a log
            if not started:
                await PlainTextResponse("Internal error\n", status_code=500)(scope, receive, send_secured)

    def _allowed(self, request: Request) -> bool:
        headers = request.headers
        if headers.get("host") not in self._hosts:
            return False
        origin = headers.get("origin")
        if origin is not None and origin not in self._origins:
            return False
        fetch_site = headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site not in _SAME_ORIGIN_FETCH_SITES:
            return False
        if request.method == "GET" and request.url.path == "/":
            supplied = request.query_params.get("token")
        else:
            supplied = headers.get(TOKEN_HEADER)
        return supplied is not None and secrets.compare_digest(supplied.encode("utf-8"), self._token)


def create_browse_app(
    open_runtime: RuntimeOpener,
    *,
    token: str,
    port: int,
    include_sensitive: bool,
    on_done: Callable[[], None],
    review_warning: str,
    sources_warning: str,
) -> Starlette:
    """Build the page and its read endpoints.

    `include_sensitive` is the operator's process-level elevation, fixed before the process starts;
    no request can change it. `on_done` is called when the page says the user is finished.
    """

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[dict[str, Any]]:
        with open_runtime() as runtime:
            yield {"runtime": runtime}

    async def page(request: Request) -> Response:
        nonce = request.scope[_NONCE_KEY]
        return HTMLResponse(render_page(nonce, review_warning=review_warning, sources_warning=sources_warning))

    async def people(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            document = runtime.use_cases.list_person_index.page(
                limit=_limit(request, DEFAULT_PERSON_PAGE_LIMIT), cursor=request.query_params.get("cursor")
            )
        except PersonIndexError as exc:
            return _error(exc.code, 400)
        return _json(render_person_index_json(document))

    async def person(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        # Ids travel as a query parameter, not a path segment: restored ids are opaque and may
        # contain `/`, which a decoded path would split.
        person_id = request.query_params.get("id", "")
        document = (
            runtime.use_cases.compose_person_brief.execute(person_id, include_sensitive=include_sensitive)
            if person_id
            else None
        )
        if document is None:
            return _error("unknown_person", 404)
        return _json(render_brief_json(document))

    async def sources(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            result = runtime.use_cases.list_import_sources.execute(
                limit=_limit(request, DEFAULT_SOURCE_PAGE_LIMIT), cursor=request.query_params.get("cursor")
            )
        except SourceInspectionError as exc:
            return _error(exc.code, 400)
        return _json(render_import_json(import_sources_document(result)))

    async def source(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            # One mapping is the smallest page the use case reads; none of it leaves the process.
            result = runtime.use_cases.show_import_source.execute(request.query_params.get("id", ""), limit=1)
        except SourceInspectionError as exc:
            return _error(exc.code, 404 if exc.code == UNKNOWN_SOURCE_SESSION else 400)
        # Built here from the receipt and staged counts only: mappings name and count committed
        # records with no disclosure filter, so they never reach the response.
        # A redacted receipt's counts are withheld, not zero, so they are sent as null.
        redacted = result.source.redacted
        body = {
            "source": result.source.model_dump(mode="json"),
            "staged_total": None if redacted else result.counts.staged_total,
            "staged_by_status": None if redacted else result.counts.staged_by_status,
        }
        return _json(json.dumps(body, indent=2, ensure_ascii=False) + "\n")

    async def done(_request: Request) -> Response:
        on_done()
        return _json(json.dumps({"stopped": True}) + "\n")

    app = Starlette(
        routes=[
            Route("/", page, methods=["GET"]),
            Route("/api/people", people, methods=["GET"]),
            Route("/api/person", person, methods=["GET"]),
            Route("/api/sources", sources, methods=["GET"]),
            Route("/api/source", source, methods=["GET"]),
            Route("/api/done", done, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
    app.add_middleware(_Guard, token=token, port=port)
    return app


def _limit(request: Request, default: int) -> int:
    """Read `limit`, turning a non-integer into 0 so the use case refuses it with its own code."""
    raw = request.query_params.get("limit")
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return 0


def _json(text: str) -> Response:
    return Response(text, media_type="application/json")


def _error(code: str, status: int) -> Response:
    return JSONResponse({"error": code}, status_code=status)
