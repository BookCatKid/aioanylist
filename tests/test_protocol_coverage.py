from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_protocol_coverage_reports_are_current_and_have_no_unknowns(tmp_path: Path) -> None:
    markdown_out = tmp_path / "protocol-coverage.md"

    subprocess.run(
        [
            sys.executable,
            "tools/protocol_coverage.py",
            "--markdown-out",
            str(markdown_out),
            "--fail-on-unknown",
        ],
        cwd=ROOT,
        check=True,
    )

    assert markdown_out.read_text() == (ROOT / "docs/protocol-coverage.md").read_text()
