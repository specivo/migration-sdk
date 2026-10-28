"""The README's example runs and writes a valid bundle."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from specivo_migration_sdk import validate_bundle

README = Path(__file__).resolve().parents[1] / "README.md"


def test_the_example_writes_a_valid_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blocks = re.findall(r"^```python\n(.*?)^```", README.read_text(), re.DOTALL | re.MULTILINE)
    example = blocks[0]
    assert len(example.strip().splitlines()) <= 25

    monkeypatch.chdir(tmp_path)
    exec(compile(example, str(README), "exec"), {})

    assert "valid — 0 error(s), 0 warning(s)" in capsys.readouterr().out
    report = validate_bundle(tmp_path / "export")
    assert report.records["users"] == 2
    assert report.records["journals"] == 1
