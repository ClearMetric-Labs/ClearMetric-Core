from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from catalogkit.query import __version__
from catalogkit.query.cli import main


def test_cli_text_output(capsys):
    sql_file = Path(__file__).resolve().parents[1] / "examples" / "simple.sql"

    exit_code = main(["--dialect", "postgres", str(sql_file)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "catalogkit-query" in captured.out
    assert "relations:" in captured.out
    assert "relation_usages:" in captured.out


def test_cli_json_output(capsys):
    sql_file = Path(__file__).resolve().parents[1] / "examples" / "simple.sql"

    exit_code = main(["--dialect", "postgres", "--format", "json", str(sql_file)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["version"] == "1"
    assert payload["summary"]["dialect"] == "postgres"
    assert payload["relations"]


def test_module_entrypoint_runs_from_package_root():
    package_root = Path(__file__).resolve().parents[1]
    sql_file = package_root / "examples" / "simple.sql"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "catalogkit.query",
            "--dialect",
            "postgres",
            str(sql_file),
        ],
        cwd=package_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "relations:" in result.stdout


def test_cli_version_output(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.out.strip() == f"catalogkit-query {__version__}"
