"""`pctx browse`: loopback-only binding, the printed URL, and the launched server's output (M30.1)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import uvicorn

from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, SqliteRecordStore, open_db
from people_context.app.records import RecordFact, RecordFactInput
from people_context.cli import main
from people_context.config import SENSITIVE_CONTEXT_ENV
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity

_SENSITIVE_VALUE = "Private recovery detail"


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _seed(db_file: Path) -> str:
    conn = open_db(db_file)
    try:
        repository = SqlitePeopleRepository(conn)
        elena = Person(canonical_name="Elena Marsh")
        repository.save_person(elena)
        RecordFact(repository, SqliteRecordStore(conn), SqliteAuditLog(conn), _Clock()).execute(
            RecordFactInput(
                person_id=elena.id,
                predicate="health",
                value=_SENSITIVE_VALUE,
                sensitivity=Sensitivity.SENSITIVE,
                source="cli",
            )
        )
        return elena.id
    finally:
        conn.close()


def test_there_is_no_way_to_ask_for_another_host(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["--db", str(tmp_path / "people.db"), "browse", "--host", "0.0.0.0"])
    assert exited.value.code == 2


@pytest.mark.parametrize("port", ["-1", "65536"])
def test_an_impossible_port_is_refused(tmp_path: Path, port: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--db", str(tmp_path / "people.db"), "browse", "--port", port]) == 2
    assert "--port" in capsys.readouterr().err


def test_a_busy_port_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert main(["--db", str(tmp_path / "people.db"), "browse", "--port", str(port)]) == 1
    assert "cannot listen" in capsys.readouterr().err


def test_open_hands_the_printed_loopback_url_to_the_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opened: list[str] = []
    bound: list[tuple[str, int]] = []

    def fake_run(_server: uvicorn.Server, sockets: list[socket.socket]) -> None:
        bound.extend(sock.getsockname() for sock in sockets)

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)

    assert main(["--db", str(tmp_path / "people.db"), "browse", "--open"]) == 0

    out, err = capsys.readouterr()
    url = out.strip()
    parts = urlsplit(url)
    assert opened == [url]
    assert parts.hostname == "127.0.0.1" and parts.path == "/"
    assert bound == [("127.0.0.1", parts.port)]
    assert parse_qs(parts.query)["token"][0] not in err
    assert "distilled personal data" in err


def _request(url: str, token: str, *, method: str = "GET") -> tuple[int, str]:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    request = urllib.request.Request(url, method=method, headers={"X-Pctx-Token": token, "Origin": origin})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _serve_and_read(db_file: Path, person_id: str, *, elevated: bool) -> tuple[str, str, str, str]:
    """Launch the real server, use it, stop it from the page, and return what it printed."""
    env = {key: value for key, value in os.environ.items() if key != SENSITIVE_CONTEXT_ENV}
    if elevated:
        env[SENSITIVE_CONTEXT_ENV] = "1"
    pctx = Path(sys.executable).parent / "pctx"
    process = subprocess.Popen(
        [str(pctx), "--db", str(db_file), "browse"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert process.stdout is not None
        url = process.stdout.readline().strip()
        token = parse_qs(urlsplit(url).query)["token"][0]
        base = url.split("?", 1)[0].rstrip("/")

        status, page = _request(url, "")
        assert status == 200 and "<script nonce=" in page
        assert _request(f"{base}/?token=wrong", "")[0] == 403
        assert _request(f"{base}/api/people", "wrong")[0] == 403
        status, people = _request(f"{base}/api/people", token)
        assert status == 200 and json.loads(people)["people"][0]["id"] == person_id
        status, brief = _request(f"{base}/api/person?{urlencode({'id': person_id})}", token)
        assert status == 200
        assert _request(f"{base}/api/done", token, method="POST")[0] == 200
        rest, err = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert process.returncode == 0
    return token, url + "\n" + rest, err, brief


def test_the_launched_server_prints_its_token_once_and_nowhere_else(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    person_id = _seed(db_file)

    token, out, err, brief = _serve_and_read(db_file, person_id, elevated=False)

    # Refused requests, the page load, and the API reads all happened: none of them logged it.
    assert (out + err).count(token) == 1
    assert out.startswith("http://127.0.0.1:")
    assert "GET /" not in err
    assert _SENSITIVE_VALUE not in brief


def test_elevation_comes_from_the_browse_process_environment(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    person_id = _seed(db_file)

    token, out, err, brief = _serve_and_read(db_file, person_id, elevated=True)

    assert (out + err).count(token) == 1
    assert _SENSITIVE_VALUE in brief
