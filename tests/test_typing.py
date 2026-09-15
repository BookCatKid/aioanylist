from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generated_proto_stubs_match_official_schema() -> None:
    result = subprocess.run(
        [sys.executable, "tools/generate_proto_stubs.py", "--check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def test_public_consumer_surface_passes_strict_mypy() -> None:
    env = os.environ.copy()
    env["MYPYPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--follow-imports=silent",
            "--no-error-summary",
            "tests/typing/consumer.py",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def test_generated_proto_stubs_are_current() -> None:
    result = subprocess.run(
        [sys.executable, "tools/generate_proto_stubs.py", "--check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def test_internal_source_passes_strict_mypy() -> None:
    env = os.environ.copy()
    env["MYPYPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--disable-error-code",
            "attr-defined",
            "--no-error-summary",
            "src/aioanylist",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0, result.stdout
