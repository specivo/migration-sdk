"""The specivo-bundle command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specivo_migration_sdk.cli import main
from tests.bundles import issue, manifest, project, user, write_bundle, write_full


@pytest.fixture
def full(tmp_path: Path) -> Path:
    return write_full(tmp_path / "full.zip")


@pytest.fixture
def broken(tmp_path: Path) -> Path:
    return write_bundle(tmp_path / "broken", projects=[project("p1")], issues=[issue("i1", author_ref="ghost")])


class TestValidate:
    def test_a_valid_bundle_exits_0(self, full: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["validate", str(full)]) == 0
        out = capsys.readouterr().out
        assert "valid — 0 error(s), 0 warning(s)" in out
        assert "issues" in out

    def test_an_invalid_bundle_exits_1_and_says_where(self, broken: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["validate", str(broken)]) == 1
        out = capsys.readouterr().out
        assert "INVALID" in out
        assert "issues.jsonl:1: error: author_ref: no user with source_ref 'ghost' [unresolved_ref]" in out

    def test_json(self, broken: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["validate", "--json", str(broken)]) == 1
        data = json.loads(capsys.readouterr().out)
        assert data["valid"] is False
        assert data["problems"][0] | {"message": ""} == {
            "severity": "error",
            "code": "unresolved_ref",
            "file": "issues.jsonl",
            "line": 1,
            "pointer": None,
            "message": "",
        }

    def test_limits_can_be_set(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = write_bundle(tmp_path / "b", users=[user("u1", display_name="x" * 2000)])
        assert main(["validate", "--max-line-size", "1K", str(root)]) == 1
        assert "too_large" in capsys.readouterr().out

    def test_a_bad_limit_is_a_usage_error(self, full: Path) -> None:
        with pytest.raises(SystemExit) as exit_info:
            main(["validate", "--max-line-size", "lots", str(full)])
        assert exit_info.value.code == 2

    def test_a_missing_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["validate", str(tmp_path / "nowhere")]) == 1
        assert "unreadable" in capsys.readouterr().out


class TestStats:
    def test_text(self, full: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["stats", str(full)]) == 0
        out = capsys.readouterr().out
        assert "source     sample tracker.example.com (version 4.2)" in out
        assert "issues               2  (1 with extra)" in out
        assert "wiki_versions        3" in out
        assert "Stored files: 2" in out
        assert "- Sprint boards have no equivalent (2)" in out

    def test_json(self, full: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["stats", "--json", str(full)]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["valid"] is True
        assert data["records"]["attachments"] == 3
        assert data["stored_files"] == 2
        assert data["with_extra"] == {"issues": 1}
        assert data["generator"] == {"name": "pytest", "version": "1"}

    def test_an_invalid_bundle_still_has_stats(self, broken: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["stats", str(broken)]) == 0
        assert "INVALID (1 error(s)" in capsys.readouterr().out

    def test_an_unreadable_bundle(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = write_bundle(tmp_path / "b", manifest_data=manifest(format_version="9.0"))
        assert main(["stats", str(root)]) == 1
        assert "unsupported_version" in capsys.readouterr().out


class TestHostileManifest:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"source": "not an object"},
            {"generator": ["x"]},
            {"not_carried_over": [{"count": 1}]},
            {"not_carried_over": "nothing"},
            {"exported_at": "2024-02-30T10:00:00Z"},
        ],
    )
    @pytest.mark.parametrize("as_json", [False, True])
    def test_stats_gives_a_report_not_a_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], overrides: dict[str, object], as_json: bool
    ) -> None:
        root = write_bundle(tmp_path / "b", manifest_data=manifest(**overrides))
        assert main(["stats", *(["--json"] if as_json else []), str(root)]) == 1
        out = capsys.readouterr().out
        assert "schema" in out
