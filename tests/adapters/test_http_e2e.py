"""End-to-end proof for loopback Streamable HTTP and shared stdio persistence."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


def _free_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_server(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _, stderr = process.communicate()
            raise AssertionError(f"HTTP server exited early ({process.returncode}): {stderr}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("HTTP server did not start within 10 seconds")


def test_real_http_security_round_trip_and_shared_stdio_database(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    project_root = Path(__file__).parents[2]
    db_path = tmp_path / "http.db"
    port = _free_loopback_port()
    process = subprocess.Popen(
        [
            uv,
            "run",
            "people-context-mcp",
            "--db",
            str(db_path),
            "--http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=project_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_server(port, process)
        person_id = anyio.run(_exercise_http, port)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    parameters = StdioServerParameters(
        command=uv,
        args=["run", "people-context-mcp", "--db", str(db_path)],
        cwd=project_root,
    )

    async def resolve_over_stdio() -> dict[str, Any]:
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as client,
        ):
            await client.initialize()
            result = await client.call_tool("resolve_person", {"query": "Loopback Alice"})
            return result.structuredContent

    resolved = anyio.run(resolve_over_stdio)
    assert resolved["candidates"][0]["person_id"] == person_id


async def _exercise_http(port: int) -> str:
    endpoint = f"http://127.0.0.1:{port}/mcp"
    async with httpx.AsyncClient() as http_client:
        invalid_host = await http_client.post(endpoint, headers={"Host": "attacker.example"}, json={})
        assert invalid_host.status_code == 421
        invalid_origin = await http_client.post(
            endpoint,
            headers={"Origin": "https://attacker.example"},
            json={},
        )
        assert invalid_origin.status_code == 403

    async with (
        streamable_http_client(endpoint) as (read_stream, write_stream, _),
        ClientSession(read_stream, write_stream) as client,
    ):
        await client.initialize()
        tools = await client.list_tools()
        assert {"remember_person", "resolve_person"} <= {tool.name for tool in tools.tools}
        remembered = await client.call_tool("remember_person", {"name": "Loopback Alice"})
        person_id = remembered.structuredContent["person"]["id"]
        resolved = await client.call_tool("resolve_person", {"query": "Loopback Alice"})
        assert resolved.structuredContent["candidates"][0]["person_id"] == person_id
        return person_id
