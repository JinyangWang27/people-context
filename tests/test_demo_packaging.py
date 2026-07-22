"""Installed-wheel proof for the packaged demonstration seed."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_wheel_installs_demo_seed_and_runs_outside_checkout(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    project_root = Path(__file__).parents[1]
    wheel_dir = tmp_path / "wheel"
    built = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    wheel = next(wheel_dir.glob("people_context-*.whl"))

    environment = tmp_path / "venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", str(environment)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    python = environment / "bin" / "python"
    installed = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    environment_site = subprocess.run(
        [str(python), "-c", "import site; print(site.getsitepackages()[0])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # Reuse the already locked test environment's third-party dependencies without
    # exposing the checkout: the wheel's own site-packages entry remains first.
    dependency_paths = [
        path
        for path in sys.path
        if path and "site-packages" in Path(path).parts and Path(path).resolve() != Path(environment_site).resolve()
    ]
    assert dependency_paths
    Path(environment_site, "locked-test-dependencies.pth").write_text(
        "\n".join(dependency_paths) + "\n",
        encoding="utf-8",
    )

    outside_checkout = tmp_path / "outside"
    outside_checkout.mkdir()
    command_env = os.environ.copy()
    command_env.pop("PYTHONPATH", None)
    command_env["XDG_DATA_HOME"] = str(tmp_path / "data")
    command_env["PEOPLE_CONTEXT_DB"] = str(tmp_path / "must-not-be-used.db")
    demo = subprocess.run(
        [str(environment / "bin" / "people-context"), "demo", "--reset"],
        cwd=outside_checkout,
        env=command_env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert demo.returncode == 0, demo.stderr
    assert "resolve_person" in demo.stdout
    assert (tmp_path / "data" / "people-context" / "demo.db").is_file()
    assert not (tmp_path / "must-not-be-used.db").exists()
