"""Lightweight HTTP bridge for people-context-mcp.

Runs a localhost HTTP server that wraps the core people_context use cases.
This bridge is intended as a temporary adapter until the upstream MCP HTTP
transport (M4) is available; the OpenClaw plugin speaks the same endpoints.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Allow running from a checkout where people_context lives next door or above.
_POSSIBLE_SRC_PATHS = [
    Path(__file__).parent.parent / "src",
    Path(__file__).parent.parent.parent / "people-context-mcp" / "src",
    Path.cwd() / "src",
]
for _path in _POSSIBLE_SRC_PATHS:
    if (_path / "people_context").is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteContextReader,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.get_communication_guidance import GetCommunicationGuidance
from people_context.app.get_person_context import GetPersonContext
from people_context.app.record import RememberPerson, RememberPersonInput
from people_context.app.resolve_person import ResolvePerson, ResolutionHints
from people_context.ports.clock import SystemClock

_LOGGER = logging.getLogger("people_context_bridge")


class _Dependencies:
    def __init__(self, db_path: str) -> None:
        self.conn = open_db(db_path)
        self.repo = SqlitePeopleRepository(self.conn)
        self.records = SqliteRecordStore(self.conn)
        self.context = SqliteContextReader(self.conn)
        self.audit = SqliteAuditLog(self.conn)
        self.clock = SystemClock()


class _Handler(BaseHTTPRequestHandler):
    deps: _Dependencies | None = None

    def log_message(self, format: str, *args: object) -> None:  # noqa: ARG002
        _LOGGER.info(format, *args)

    def _send_json(self, status: int, body: object) -> None:
        data = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_POST(self) -> None:  # noqa: N802
        assert self.deps is not None
        path = self.path.rstrip("/")
        body = self._read_json()

        try:
            if path == "/resolve":
                result = self._resolve(body)
            elif path == "/context":
                result = self._context(body)
            elif path == "/guidance":
                result = self._guidance(body)
            elif path == "/remember":
                result = self._remember(body)
            else:
                self._send_json(404, {"error": "not_found", "path": self.path})
                return
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Request failed")
            self._send_json(500, {"error": "internal_error", "message": str(exc)})
            return

        self._send_json(200, result)

    def _resolve(self, body: dict) -> dict:
        resolver = ResolvePerson(self.deps.repo)
        hints = body.get("hints") or {}
        result = resolver.execute(
            query=body["query"],
            limit=body.get("limit", 5),
            hints=ResolutionHints(
                org=hints.get("org"),
                role=hints.get("role"),
                relationship=hints.get("relationship"),
            ),
        )
        return {
            "query": result.query,
            "ambiguous": result.ambiguous,
            "candidates": [
                {
                    "person_id": c.person_id,
                    "canonical_name": c.canonical_name,
                    "score": c.score,
                    "match_reason": c.match_reason,
                    "aliases": c.aliases,
                    "summary": c.summary,
                }
                for c in result.candidates
            ],
        }

    def _context(self, body: dict) -> dict:
        context = GetPersonContext(self.deps.repo, self.deps.context, self.deps.clock)
        result = context.execute(
            person_id=body["person_id"],
            purpose=body.get("purpose", "communication"),
            max_items=body.get("max_items", 10),
            include_sensitive=body.get("include_sensitive", False),
        )
        if not result.found:
            return {"found": False}
        return {
            "found": True,
            "identity": {
                "id": result.identity.id if result.identity else None,
                "name": result.identity.canonical_name if result.identity else None,
                "aliases": result.identity.aliases if result.identity else [],
                "summary": result.identity.summary if result.identity else None,
                "is_self": result.identity.is_self if result.identity else False,
            },
            "relationships": [
                {
                    "type": r.relationship.type,
                    "with_id": r.other_person_id,
                    "with_name": r.other_person_name,
                }
                for r in result.relationships
            ],
            "facts": [
                {"predicate": f.predicate, "value": f.value} for f in result.facts
            ],
            "traits": [
                {"category": t.category.value, "value": t.value}
                for t in result.traits
            ],
            "reminders": [
                {"text": r.text, "kind": r.kind.value} for r in result.reminders
            ],
        }

    def _guidance(self, body: dict) -> dict:
        guidance = GetCommunicationGuidance(
            self.deps.repo, self.deps.context, self.deps.clock
        )
        result = guidance.execute(
            person_id=body["person_id"],
            situation=body.get("situation"),
        )
        return {
            "found": result.found,
            "traits": {
                k: [{"value": t.value, "evidence_note": t.evidence_note} for t in v]
                for k, v in result.traits.items()
            },
            "friction_notes": result.friction_notes,
            "reminders": [r.text for r in result.reminders],
            "communication_philosophy": result.communication_philosophy,
            "philosophy_set": result.philosophy_set,
        }

    def _remember(self, body: dict) -> dict:
        remember = RememberPerson(self.deps.repo, self.deps.repo, self.deps.audit, self.deps.clock)
        result = remember.execute(
            RememberPersonInput(
                name=body["name"],
                summary=body.get("summary"),
                aliases=body.get("aliases", []),
                is_self=body.get("is_self", False),
                source="openclaw-plugin",
            )
        )
        return {
            "person_id": result.person.id,
            "canonical_name": result.person.canonical_name,
            "created": result.created,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="people-context HTTP bridge")
    parser.add_argument("--db", required=True, help="Path to the people-context SQLite database.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=8765, help="Bind port.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _Handler.deps = _Dependencies(args.db)
    server = HTTPServer((args.host, args.port), _Handler)
    _LOGGER.info("people-context bridge listening on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _LOGGER.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
