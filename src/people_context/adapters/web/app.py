"""The loopback Starlette application behind `pctx browse` (M30).

The browser is a fourth client of the use cases the CLI and MCP already call: every endpoint here
wraps an existing use case and returns what it returns. No use case, validation, or disclosure rule
exists only for the page. The writes are batch amendment, withdrawal, and commit, through the same
use cases `pctx import` calls, always with the digest of the review the page displayed; alias addition,
correction, and removal on a person page, through the same use cases as `pctx add-alias`,
`pctx update-alias`, and `pctx remove-alias`; a rename or summary change through the same `EditPerson` as
`pctx edit`; a new affiliation, fact, or interaction through the same `SetAffiliation`, `RecordFact`, and
`RecordInteraction` as the `set_affiliation`, `record_fact`, and `record_interaction` MCP tools; in-place
correction of an affiliation, fact, interaction, trait, or reminder the page displays, through the same
`CorrectRecord` as the `correct_record` MCP tool; and removal of an affiliation, fact, or interaction the page
displays, through the same `Forget` as the `forget` MCP tool.

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
from datetime import UTC, date, datetime, time
from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from people_context.adapters.runtime import ApplicationRuntime
from people_context.adapters.web.fields import ALIAS_FIELDS, CANDIDATE_FIELDS, RECORD_FIELDS
from people_context.adapters.web.page import render_page
from people_context.app.exports import (
    DEFAULT_PERSON_PAGE_LIMIT,
    PersonIndexError,
    render_brief_json,
    render_person_index_json,
)
from people_context.app.imports import (
    CLI_IMPORT_BUDGET,
    DEFAULT_SOURCE_PAGE_LIMIT,
    MAX_CLI_STAGED_PAYLOAD_BYTES,
    UNKNOWN_SOURCE_SESSION,
    ImportPipelineError,
    ImportReviewResult,
    ImportReviewRow,
    SourceInspectionError,
    amendment_refusals,
    import_commit_document,
    import_review_document,
    import_sources_document,
    render_import_json,
)
from people_context.app.people import (
    AddAliasInput,
    AliasConflictError,
    AliasNotFoundError,
    EditPersonInput,
    ForgetError,
    PersonNameCollisionError,
    RemoveAliasInput,
    UpdateAliasInput,
)
from people_context.app.records import (
    CorrectRecordInput,
    InvalidCorrectionError,
    OrganizationNotFoundError,
    PersonNotFoundError,
    RecordFactInput,
    RecordInteractionInput,
    RecordNotFoundError,
    SetAffiliationInput,
)
from people_context.domain.person import ALIAS_EMAIL_NOT_HANDLE, NAME_IS_EMAIL, AliasKind
from people_context.domain.shared import Sensitivity, ValidityPeriod

LOOPBACK_HOST = "127.0.0.1"
TOKEN_HEADER = "X-Pctx-Token"

_NONCE_KEY = "pctx.nonce"
_REFUSAL = "Forbidden\n"
_SAME_ORIGIN_FETCH_SITES = frozenset({"same-origin", "none"})

RuntimeOpener = Callable[[], AbstractContextManager[ApplicationRuntime]]
ReviewLines = Callable[[list[ImportReviewRow]], list[str]]

#: Bytes one batch-action body may carry. Its ids name rows of one batch the CLI ceiling already
#: bounds, so the same ceiling bounds the request that selects them.
MAX_BATCH_ACTION_BYTES = MAX_CLI_STAGED_PAYLOAD_BYTES

_NonBlank = Annotated[str, StringConstraints(min_length=1)]
#: Text that needs one visible character; the value itself is stored as typed.
_Visible = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
_ActionT = TypeVar("_ActionT", bound=BaseModel)


class _BatchAction(BaseModel):
    """One withdraw or commit request: which rows, of which batch, as the page displayed it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    batch_id: _NonBlank
    candidate_ids: list[str]
    expected_batch_digest: _NonBlank


class _AmendAction(BaseModel):
    """One edit of one staged candidate, of the batch as the page displayed it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    batch_id: _NonBlank
    candidate_id: _NonBlank
    patch: dict[str, Any]
    expected_batch_digest: _NonBlank


class _AddAliasAction(BaseModel):
    """Add one alias to the person the page displayed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    person_id: _NonBlank
    # A name needs one visible character; the value itself is stored as typed.
    value: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
    kind: AliasKind = AliasKind.OTHER
    lang: _NonBlank | None = None
    script: _NonBlank | None = None


class _EditPersonAction(BaseModel):
    """Rename the person the page displayed or replace their summary; an absent field is unchanged."""

    model_config = ConfigDict(extra="forbid", strict=True)

    person_id: _NonBlank
    name: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")] | None = None
    # An empty summary clears it.
    summary: str | None = None


class _AddAffiliationAction(BaseModel):
    """Record one role at an organization, named by name, for the person the page displayed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    person_id: _NonBlank
    org: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
    role: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
    valid_from: date | None = None
    valid_to: date | None = None


class _AddFactAction(BaseModel):
    """Record one fact, of ordinary sensitivity, about the person the page displayed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    person_id: _NonBlank
    predicate: _Visible
    value: _Visible
    valid_from: date | None = None
    valid_to: date | None = None


class _AddInteractionAction(BaseModel):
    """Record one interaction, of ordinary sensitivity, with the person the page displayed as its participant."""

    model_config = ConfigDict(extra="forbid", strict=True)

    person_id: _NonBlank
    summary: _Visible
    # A day, as a date picker gives it; absent means now, as for `record_interaction`.
    occurred_on: date | None = None
    channel: _Visible | None = None


class _CorrectRecordAction(BaseModel):
    """Correct fields of one record the person view lists; the use case decides which fields and values hold."""

    model_config = ConfigDict(extra="forbid", strict=True)

    entity_type: Literal["affiliation", "fact", "interaction", "trait", "reminder"]
    entity_id: _NonBlank
    fields: dict[str, Any]


class _RemoveRecordAction(BaseModel):
    """Permanently remove one record the person view lists."""

    model_config = ConfigDict(extra="forbid", strict=True)

    entity_type: Literal["affiliation", "fact", "interaction"]
    entity_id: _NonBlank


class _RemoveAliasAction(BaseModel):
    """Remove one alias, named by value, from the person the page displayed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    person_id: _NonBlank
    value: _NonBlank


class _UpdateAliasAction(BaseModel):
    """Correct one alias, named by value; an absent field leaves that part unchanged."""

    model_config = ConfigDict(extra="forbid", strict=True)

    person_id: _NonBlank
    value: _NonBlank
    new_value: _NonBlank | None = None
    kind: AliasKind | None = None
    lang: _NonBlank | None = None
    script: _NonBlank | None = None


class _RequestRefused(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


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
    review_lines: ReviewLines,
    language: str = "en",
) -> Starlette:
    """Build the page and its endpoints.

    `include_sensitive` is the operator's process-level elevation, fixed before the process starts;
    no request can change it. `on_done` is called when the page says the user is finished.
    `review_lines` is the CLI's own one-line rendering of review rows, so the batch page and
    `pctx import review` describe a candidate identically. `language` is only the one the page
    opens in; its toggle switches between every language it offers without a request.
    """

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[dict[str, Any]]:
        with open_runtime() as runtime:
            yield {"runtime": runtime}

    async def page(request: Request) -> Response:
        nonce = request.scope[_NONCE_KEY]
        text = render_page(nonce, review_warning=review_warning, sources_warning=sources_warning, language=language)
        return HTMLResponse(text)

    async def people(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            params = request.query_params
            document = runtime.use_cases.list_person_index.page(
                limit=_limit(request, DEFAULT_PERSON_PAGE_LIMIT),
                cursor=params.get("cursor"),
                query=params.get("q"),
                sort=params.get("sort", "name"),
                organization_id=params.get("organization"),
                group_id=params.get("group"),
                include_sensitive=include_sensitive,
            )
        except PersonIndexError as exc:
            return _error(exc.code, 400)
        # The choices the page's filters offer ride beside the index document rather than inside it.
        body = json.loads(render_person_index_json(document))
        body["filters"] = runtime.use_cases.list_person_index.filter_options(include_sensitive=include_sensitive)
        body["organization_names"] = runtime.use_cases.list_person_index.organization_names(document)
        return _json(json.dumps(body, indent=2, ensure_ascii=False) + "\n")

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
        # The brief names aliases by value only, by design; the page's alias editor also needs each
        # alias's kind, language, and script, so they ride beside the brief rather than inside it.
        stored = runtime.repo.get(document.person.id)
        body = json.loads(render_brief_json(document))
        body["alias_details"] = [
            alias.model_dump(mode="json", exclude={"id"}) for alias in (stored.aliases if stored else [])
        ]
        body["alias_kinds"] = [kind.value for kind in AliasKind]
        body["record_fields"] = RECORD_FIELDS
        return _json(json.dumps(body, indent=2, ensure_ascii=False) + "\n")

    async def add_alias(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _AddAliasAction)
            data = AddAliasInput(**action.model_dump(), source="browser")
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        except ValidationError:
            # The action already passed its strict schema, so the input's one remaining rule failed:
            # an email address offered with a kind other than `handle`.
            return JSONResponse({"error": "alias_conflict", "reason": ALIAS_EMAIL_NOT_HANDLE}, status_code=409)
        before = runtime.repo.get(data.person_id)
        try:
            person = runtime.use_cases.add_alias.execute(data)
        except PersonNotFoundError as exc:
            return _alias_refusal(exc)
        # The use case adds nothing for a value the person already carries; the page says so.
        added = before is not None and len(person.aliases) > len(before.aliases)
        return _json(json.dumps({"person_id": person.id, "alias": data.value, "added": added}) + "\n")

    async def remove_alias(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _RemoveAliasAction)
            result = runtime.use_cases.remove_alias.execute(
                RemoveAliasInput(person_id=action.person_id, value=action.value, source="browser")
            )
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        except (PersonNotFoundError, AliasNotFoundError, AliasConflictError) as exc:
            return _alias_refusal(exc)
        return _json(result.model_dump_json() + "\n")

    async def update_alias(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _UpdateAliasAction)
            result = runtime.use_cases.update_alias.execute(
                UpdateAliasInput(**action.model_dump(), source="browser")
            )
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        except (PersonNotFoundError, AliasNotFoundError, AliasConflictError) as exc:
            return _alias_refusal(exc)
        return _json(result.model_dump_json() + "\n")

    async def edit_person(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _EditPersonAction)
            data = EditPersonInput(**action.model_dump(), source="browser")
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        except ValidationError:
            # The action already passed its strict schema, so the input's one remaining rule failed.
            return _error(NAME_IS_EMAIL, 400)
        if data.name is None and data.summary is None:
            return _error("invalid_request", 400)
        try:
            person = runtime.use_cases.edit_person.execute(data)
        except PersonNotFoundError:
            return _error("unknown_person", 404)
        except PersonNameCollisionError:
            # Which person already holds the name is not echoed: it may be one the view never showed.
            return _error("name_conflict", 409)
        return _json(json.dumps({"person_id": person.id}) + "\n")

    async def add_affiliation(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _AddAffiliationAction)
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        try:
            # Checked before the write: the use case would otherwise create a new organization
            # and only then refuse the period.
            ValidityPeriod(valid_from=action.valid_from, valid_to=action.valid_to)
        except ValidationError:
            return _error("invalid_period", 400)
        try:
            affiliation = runtime.use_cases.set_affiliation.execute(
                SetAffiliationInput(**action.model_dump(), source="browser")
            )
        except PersonNotFoundError:
            return _error("unknown_person", 404)
        except OrganizationNotFoundError:
            # An organization given by an id that is not stored, never created under that id.
            return _error("unknown_organization", 404)
        return _json(json.dumps({"affiliation_id": affiliation.id}) + "\n")

    async def add_fact(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _AddFactAction)
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        try:
            fact = runtime.use_cases.record_fact.execute(RecordFactInput(**action.model_dump(), source="browser"))
        except PersonNotFoundError:
            return _error("unknown_person", 404)
        except ValidationError:
            # The action passed its strict schema, so the one rule left is the period's order.
            return _error("invalid_period", 400)
        return _json(json.dumps({"fact_id": fact.id}) + "\n")

    async def add_interaction(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _AddInteractionAction)
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        occurred_at = None if action.occurred_on is None else datetime.combine(action.occurred_on, time(), UTC)
        try:
            interaction = runtime.use_cases.record_interaction.execute(
                RecordInteractionInput(
                    summary=action.summary,
                    participant_ids=[action.person_id],
                    occurred_at=occurred_at,
                    channel=action.channel,
                    source="browser",
                )
            )
        except PersonNotFoundError:
            return _error("unknown_person", 404)
        return _json(json.dumps({"interaction_id": interaction.id}) + "\n")

    def displayed(runtime: ApplicationRuntime, entity_type: str, entity_id: str) -> bool:
        """Whether the person view could have shown this record under the process's disclosure."""
        current = runtime.records.get_record(entity_type, entity_id)
        sensitivity = getattr(current, "sensitivity", Sensitivity.PERSONAL)
        return current is not None and (include_sensitive or sensitivity in (Sensitivity.PUBLIC, Sensitivity.PERSONAL))

    async def correct_record(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _CorrectRecordAction)
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        # The page edits only what it can show: a record the view's disclosure hides is refused as
        # unknown, the same answer as a record that does not exist, so neither is confirmed.
        if not displayed(runtime, action.entity_type, action.entity_id):
            return _error("record_not_found", 404)
        try:
            runtime.use_cases.correct_record.execute(
                CorrectRecordInput(**action.model_dump(), source="browser")
            )
        except InvalidCorrectionError as exc:
            # Only declared field names, which the page already holds: an unknown key is the caller's
            # own text, so it is never echoed, and neither is any submitted value.
            known = {field["name"] for field in RECORD_FIELDS[action.entity_type]}
            named = [field for field in exc.fields if field in known]
            return JSONResponse({"error": "invalid_correction", "fields": named}, status_code=400)
        except RecordNotFoundError:
            return _error("record_not_found", 404)
        except PersonNotFoundError:
            return _error("unknown_person", 404)
        # The corrected record is not echoed: the page reloads the brief, which applies disclosure.
        return _json(json.dumps({"corrected": sorted(action.fields)}) + "\n")

    async def remove_record(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _RemoveRecordAction)
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        # As for a correction: the page removes only what it can show.
        if not displayed(runtime, action.entity_type, action.entity_id):
            return _error("record_not_found", 404)
        try:
            runtime.use_cases.forget.execute(f"{action.entity_type}:{action.entity_id}", "record", source="browser")
        except (RecordNotFoundError, ForgetError):
            # The schema already fixes the type and a non-empty id, so either means the record is gone.
            return _error("record_not_found", 404)
        return _json(json.dumps({"removed": action.entity_type}) + "\n")

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

    async def batch(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            review = _bounded_review(runtime, request.query_params.get("id", ""))
        except ImportPipelineError as exc:
            return _refusal(exc)
        # The field lists are static and small; sending them with the batch spares the page a
        # second request and a cache of its own for something that cannot change while it runs.
        body = {
            "review": import_review_document(review).model_dump(mode="json"),
            "lines": review_lines(review.candidates),
            "fields": CANDIDATE_FIELDS,
            "alias_fields": ALIAS_FIELDS,
        }
        return _json(json.dumps(body, indent=2, ensure_ascii=False) + "\n")

    async def amend(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _AmendAction)
            runtime.use_cases.preflight_import_batch.execute(action.batch_id)
            revised = runtime.use_cases.amend_staged_candidate.execute(
                action.batch_id,
                action.candidate_id,
                action.patch,
                expected_batch_digest=action.expected_batch_digest,
            )
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        except ImportPipelineError as exc:
            return _amend_refusal(exc)
        # As for a withdrawal: the digest this edit produced lets the page tell its own change
        # from another client's when it reloads.
        return _json(json.dumps({"batch_digest": revised.batch_digest}) + "\n")

    async def withdraw(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _BatchAction)
            runtime.use_cases.preflight_import_batch.execute(action.batch_id)
            revised = runtime.use_cases.withdraw_staged_candidates.execute(
                action.batch_id, action.candidate_ids, expected_batch_digest=action.expected_batch_digest
            )
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        except ImportPipelineError as exc:
            return _refusal(exc)
        # The digest after this withdrawal lets the page tell its own change from another client's.
        body = {"withdrawn": len(set(action.candidate_ids)), "batch_digest": revised.batch_digest}
        return _json(json.dumps(body) + "\n")

    async def commit(request: Request) -> Response:
        runtime: ApplicationRuntime = request.state.runtime
        try:
            action = await _read_action(request, _BatchAction)
            runtime.use_cases.preflight_import_batch.execute(action.batch_id)
            result = runtime.use_cases.commit_import.execute(
                action.batch_id, action.candidate_ids, expected_batch_digest=action.expected_batch_digest
            )
        except _RequestRefused as exc:
            return _error(exc.code, exc.status)
        except ImportPipelineError as exc:
            return _refusal(exc)
        return _json(render_import_json(import_commit_document(result)))

    async def done(_request: Request) -> Response:
        on_done()
        return _json(json.dumps({"stopped": True}) + "\n")

    app = Starlette(
        routes=[
            Route("/", page, methods=["GET"]),
            Route("/api/people", people, methods=["GET"]),
            Route("/api/person", person, methods=["GET"]),
            Route("/api/person/alias/add", add_alias, methods=["POST"]),
            Route("/api/person/alias/remove", remove_alias, methods=["POST"]),
            Route("/api/person/alias/update", update_alias, methods=["POST"]),
            Route("/api/person/edit", edit_person, methods=["POST"]),
            Route("/api/person/affiliation/add", add_affiliation, methods=["POST"]),
            Route("/api/person/fact/add", add_fact, methods=["POST"]),
            Route("/api/person/interaction/add", add_interaction, methods=["POST"]),
            Route("/api/record/correct", correct_record, methods=["POST"]),
            Route("/api/record/remove", remove_record, methods=["POST"]),
            Route("/api/sources", sources, methods=["GET"]),
            Route("/api/source", source, methods=["GET"]),
            Route("/api/batch", batch, methods=["GET"]),
            Route("/api/batch/amend", amend, methods=["POST"]),
            Route("/api/batch/withdraw", withdraw, methods=["POST"]),
            Route("/api/batch/commit", commit, methods=["POST"]),
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


def _bounded_review(runtime: ApplicationRuntime, batch_id: str) -> ImportReviewResult:
    """Read one batch under the ceilings `pctx import review` applies, and no others."""
    runtime.use_cases.preflight_import_batch.execute(batch_id)
    return runtime.use_cases.review_import.execute(batch_id, budget=CLI_IMPORT_BUDGET)


async def _read_action(request: Request, model: type[_ActionT]) -> _ActionT:
    """Read a bounded JSON body and validate it, refusing without echoing any of it."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BATCH_ACTION_BYTES:
        raise _RequestRefused("request_too_large", 413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BATCH_ACTION_BYTES:
            raise _RequestRefused("request_too_large", 413)
    try:
        return model.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise _RequestRefused("invalid_request", 400) from exc


def _refusal(exc: ImportPipelineError) -> Response:
    """Report a use-case refusal by its code alone: never its message, details, or the refused ids."""
    return _error(exc.code, _refusal_status(exc))


def _amend_refusal(exc: ImportPipelineError) -> Response:
    """Report an amendment refusal by its code and the fields it named, and nothing else.

    Only the amendment reports fields, and only this endpoint calls this. `amendment_refusals`
    returns the declared field each rule was about and the fixed text the amendment use case
    wrote for it, so an edit form can put a message against the control that failed without any
    of the submitted patch coming back. Every other endpoint keeps the code-only refusal above.
    """
    fields = amendment_refusals(exc)
    if not fields:
        return _refusal(exc)
    return JSONResponse({"error": exc.code, "fields": fields}, status_code=_refusal_status(exc))


def _alias_refusal(exc: PersonNotFoundError | AliasNotFoundError | AliasConflictError) -> Response:
    """Report an alias refusal by its code and, for a conflict, its fixed reason; never the value."""
    if isinstance(exc, AliasConflictError):
        return JSONResponse({"error": "alias_conflict", "reason": exc.reason}, status_code=409)
    code = "unknown_person" if isinstance(exc, PersonNotFoundError) else "alias_not_found"
    return _error(code, 404)


def _refusal_status(exc: ImportPipelineError) -> int:
    return {"batch_changed": 409, "batch_not_found": 404}.get(exc.code, 400)


def _json(text: str) -> Response:
    return Response(text, media_type="application/json")


def _error(code: str, status: int) -> Response:
    return JSONResponse({"error": code}, status_code=status)
