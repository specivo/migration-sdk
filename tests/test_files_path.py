"""Attachment bytes outside the bundle: ``BundleWriter(files_path=...)`` and ``validate_bundle(files_path=...)``.

A large migration keeps its attachments in a plain directory, named by their
SHA-256 as under a bundle's ``files/``, so they can be copied with ``rsync``
and a later export only adds new files. The bundle then holds the records only,
as a directory or a zip.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest

from specivo_migration_sdk import (
    Attachment,
    BundleWriter,
    Issue,
    Priority,
    Project,
    Severity,
    Status,
    Tracker,
    ValidationReport,
    inspect_bundle,
    validate_bundle,
)
from specivo_migration_sdk.cli import main
from tests.bundles import LOG, SCREENSHOT, issue, project, stored, write_bundle, write_full

type Codes = list[tuple[str, int | None, str]]


def _codes(report: ValidationReport, severity: Severity = Severity.ERROR) -> Codes:
    return [(p.file, p.line, str(p.code)) for p in report.problems if p.severity is severity]


def _writer(target: Path, **kwargs: Any) -> BundleWriter:
    return BundleWriter(
        target,
        source_system="sample",
        source_instance="tracker.example.com",
        generator_name="pytest",
        generator_version="1",
        **kwargs,
    )


def _export(target: Path, files: Path, *contents: bytes) -> Path:
    """Write a bundle with one issue and an attachment per item of *contents*, bytes in *files*."""
    with _writer(target, files_path=files) as writer:
        writer.add(Tracker(source_ref="bug", name="Bug", default_status_ref="open"))
        writer.add(Status(source_ref="open", name="Open", category="backlog"))
        writer.add(Priority(source_ref="normal", name="Normal", is_default=True))
        writer.add(Project(source_ref="p1", identifier="web", name="Website"))
        writer.add(
            Issue(
                source_ref="i1",
                project_ref="p1",
                tracker_ref="bug",
                status_ref="open",
                priority_ref="normal",
                subject="Login fails",
            )
        )
        for number, content in enumerate(contents, start=1):
            file = writer.add_file(content)
            writer.add(
                Attachment(
                    source_ref=f"a{number}",
                    container_kind="issue",
                    container_ref="i1",
                    filename=f"file{number}.bin",
                    sha256=file.sha256,
                    filesize=file.size,
                )
            )
    return target


def _attachment(sha256: str) -> dict[str, object]:
    return {
        "source_ref": "a1",
        "container_kind": "issue",
        "container_ref": "i1",
        "filename": "log.txt",
        "sha256": sha256,
    }


def _data_only(root: Path, sha256: str) -> Path:
    return write_bundle(root, projects=[project("p1")], issues=[issue("i1")], attachments=[_attachment(sha256)])


class TestDirectoryIsTheDefault:
    def test_a_target_without_zip_is_a_directory(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "export") as writer:
            assert writer.zip is False
        assert (tmp_path / "export").is_dir()
        assert (tmp_path / "export" / "manifest.json").is_file()

    def test_a_zip_is_opt_in(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "export", zip=True) as writer:
            assert writer.zip is True
        assert zipfile.is_zipfile(tmp_path / "export")
        with _writer(tmp_path / "small.zip") as writer:
            assert writer.zip is True
        assert zipfile.is_zipfile(tmp_path / "small.zip")


class TestWriterFilesPath:
    def test_the_bytes_go_to_the_files_directory_and_the_bundle_has_no_files(self, tmp_path: Path) -> None:
        root = _export(tmp_path / "export", tmp_path / "files", LOG, SCREENSHOT, LOG)
        assert not (root / "files").exists()
        assert sorted(path.name for path in (tmp_path / "files").iterdir()) == sorted([stored(LOG), stored(SCREENSHOT)])
        assert (tmp_path / "files" / stored(LOG)).read_bytes() == LOG
        # Without the files directory the attachments are missing; with it the bundle is valid.
        assert ("attachments.jsonl", 1, "missing_file") in _codes(validate_bundle(root))
        report = validate_bundle(root, files_path=tmp_path / "files")
        assert report.ok, report.format_text()
        assert report.problems == []

    def test_the_manifest_records_nothing_about_where_the_files_are(self, tmp_path: Path) -> None:
        manifest = json.loads((_export(tmp_path / "export", tmp_path / "files", LOG) / "manifest.json").read_text())
        assert set(manifest) == {"format", "format_version", "source", "generator", "exported_at", "counts"}
        assert manifest["counts"]["attachments"] == 1

    def test_a_zipped_data_only_bundle(self, tmp_path: Path) -> None:
        archive = _export(tmp_path / "export.zip", tmp_path / "files", LOG, SCREENSHOT)
        with zipfile.ZipFile(archive) as handle:
            names = handle.namelist()
        assert not any(name.startswith("files/") for name in names)
        assert (tmp_path / "files" / stored(SCREENSHOT)).read_bytes() == SCREENSHOT
        report = validate_bundle(archive, files_path=tmp_path / "files")
        assert report.ok, report.format_text()
        assert report.records["attachments"] == 2

    def test_a_re_export_reuses_files_already_there(self, tmp_path: Path) -> None:
        files = tmp_path / "files"
        _export(tmp_path / "first", files, LOG)
        kept = files / stored(LOG)
        before = kept.stat()
        os.utime(kept, ns=(before.st_atime_ns, before.st_mtime_ns - 10**9))
        before = kept.stat()

        _export(tmp_path / "second", files, LOG, SCREENSHOT)
        after = kept.stat()
        # The file already there is not rewritten, so rsync skips it; only the new one is added.
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert sorted(path.name for path in files.iterdir()) == sorted([stored(LOG), stored(SCREENSHOT)])
        assert validate_bundle(tmp_path / "second", files_path=files).ok

    def test_a_file_already_there_with_the_wrong_bytes_is_replaced(self, tmp_path: Path) -> None:
        files = tmp_path / "files"
        files.mkdir()
        (files / stored(LOG)).write_bytes(b"corrupted")
        _export(tmp_path / "export", files, LOG)
        assert (files / stored(LOG)).read_bytes() == LOG

    def test_a_symlink_already_there_is_replaced_not_followed(self, tmp_path: Path) -> None:
        files = tmp_path / "files"
        files.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"keep me")
        os.symlink(outside, files / stored(LOG))
        _export(tmp_path / "export", files, LOG)
        assert not (files / stored(LOG)).is_symlink()
        assert (files / stored(LOG)).read_bytes() == LOG
        assert outside.read_bytes() == b"keep me"

    def test_a_failed_download_leaves_nothing_half_written(self, tmp_path: Path) -> None:
        files = tmp_path / "files"

        def download() -> Any:
            yield b"first chunk"
            raise ConnectionError("reset by peer")

        with pytest.raises(ConnectionError), _writer(tmp_path / "export", files_path=files) as writer:
            writer.add_file(LOG)
            writer.add_file(download())
        # The complete file stays (it is named by its content, so it is reused); no partial one.
        assert [path.name for path in files.iterdir()] == [stored(LOG)]
        assert not (tmp_path / "export" / "manifest.json").exists()

    def test_the_files_directory_may_not_be_a_file(self, tmp_path: Path) -> None:
        (tmp_path / "files").write_text("not a directory")
        with pytest.raises(NotADirectoryError):
            _writer(tmp_path / "export", files_path=tmp_path / "files")
        assert not (tmp_path / "export").exists()

    @pytest.mark.parametrize(("target", "files"), [("export", "export/files"), ("files/export", "files")])
    def test_the_bundle_and_the_files_directory_may_not_nest(self, tmp_path: Path, target: str, files: str) -> None:
        with pytest.raises(ValueError, match="inside"):
            _writer(tmp_path / target, files_path=tmp_path / files)
        assert list(tmp_path.iterdir()) == []


class TestValidateFilesPath:
    def test_the_full_bundle_with_its_files_moved_out(self, tmp_path: Path) -> None:
        root = write_full(tmp_path / "full")
        (root / "files").rename(tmp_path / "files")
        stats = inspect_bundle(root, files_path=tmp_path / "files")
        assert stats.report.ok, stats.report.format_text()
        assert stats.stored_files == 2

    def test_a_missing_file(self, tmp_path: Path) -> None:
        (tmp_path / "files").mkdir()
        root = _data_only(tmp_path / "b", stored(b"x"))
        report = validate_bundle(root, files_path=tmp_path / "files")
        assert _codes(report) == [("attachments.jsonl", 1, "missing_file")]
        assert report.problems[0].message == f"files/{stored(b'x')} is not in the bundle"

    def test_a_wrong_hash(self, tmp_path: Path) -> None:
        (tmp_path / "files").mkdir()
        (tmp_path / "files" / stored(b"x")).write_bytes(b"tampered")
        report = validate_bundle(_data_only(tmp_path / "b", stored(b"x")), files_path=tmp_path / "files")
        assert _codes(report) == [("attachments.jsonl", 1, "hash_mismatch")]
        assert report.problems[0].message == f"files/{stored(b'x')} does not hash to its name"

    def test_a_wrong_size_is_only_a_warning(self, tmp_path: Path) -> None:
        (tmp_path / "files").mkdir()
        (tmp_path / "files" / stored(b"x")).write_bytes(b"x")
        root = write_bundle(
            tmp_path / "b",
            projects=[project("p1")],
            issues=[issue("i1")],
            attachments=[{**_attachment(stored(b"x")), "filesize": 99}],
        )
        report = validate_bundle(root, files_path=tmp_path / "files")
        assert report.ok
        assert _codes(report, Severity.WARNING) == [("attachments.jsonl", 1, "size_mismatch")]

    def test_a_symlink_is_refused(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_bytes(b"server secret")
        (tmp_path / "files").mkdir()
        os.symlink(secret, tmp_path / "files" / stored(b"server secret"))
        root = _data_only(tmp_path / "b", stored(b"server secret"))
        report = validate_bundle(root, files_path=tmp_path / "files")
        assert _codes(report) == [(f"files/{stored(b'server secret')}", None, "symlink")]
        assert report.problems[0].message == "Symbolic links are not allowed in a bundle"
        # Refused outright: no record was read.
        assert report.records == {}

    def test_a_symlinked_directory_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "files").mkdir()
        os.symlink(tmp_path, tmp_path / "files" / "up")
        report = validate_bundle(_data_only(tmp_path / "b", stored(b"x")), files_path=tmp_path / "files")
        assert _codes(report) == [("files/up", None, "symlink")]

    def test_a_sha256_that_climbs_out_is_not_followed(self, tmp_path: Path) -> None:
        (tmp_path / "files").mkdir()
        (tmp_path / "secret").write_bytes(b"server secret")
        stats = inspect_bundle(_data_only(tmp_path / "b", "../secret"), files_path=tmp_path / "files")
        assert _codes(stats.report) == [("attachments.jsonl", 1, "schema")]
        assert stats.stored_files == 0

    def test_unknown_entries_are_ignored_with_a_warning(self, tmp_path: Path) -> None:
        files = tmp_path / "files"
        files.mkdir()
        (files / stored(b"x")).write_bytes(b"x")
        (files / stored(b"y")).write_bytes(b"y")
        (files / "notes.txt").write_text("hello")
        report = validate_bundle(_data_only(tmp_path / "b", stored(b"x")), files_path=files)
        assert report.ok
        assert _codes(report, Severity.WARNING) == [
            ("files/notes.txt", None, "unknown_file"),
            (f"files/{stored(b'y')}", None, "unreferenced_file"),
        ]

    def test_a_files_directory_that_does_not_exist(self, tmp_path: Path) -> None:
        report = validate_bundle(_data_only(tmp_path / "b", stored(b"x")), files_path=tmp_path / "nowhere")
        assert _codes(report) == [("nowhere", None, "unreadable")]
        assert report.records == {}

    def test_the_bundles_own_files_are_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "files").mkdir()
        root = write_bundle(
            tmp_path / "b",
            projects=[project("p1")],
            issues=[issue("i1")],
            attachments=[_attachment(stored(b"x"))],
            files={stored(b"x"): b"x"},
        )
        report = validate_bundle(root, files_path=tmp_path / "files")
        assert _codes(report) == [("attachments.jsonl", 1, "missing_file")]
        assert _codes(report, Severity.WARNING) == [("files", None, "unknown_file")]

    def test_a_zipped_data_only_bundle_with_a_symlink_in_the_files_directory(self, tmp_path: Path) -> None:
        files = tmp_path / "files"
        archive = _export(tmp_path / "export.zip", files, LOG)
        (files / stored(LOG)).unlink()
        os.symlink(tmp_path / "export.zip", files / stored(LOG))
        assert _codes(validate_bundle(archive, files_path=files)) == [(f"files/{stored(LOG)}", None, "symlink")]


class TestCli:
    def test_validate_with_a_files_directory(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = _export(tmp_path / "export", tmp_path / "files", LOG)
        assert main(["validate", str(root)]) == 1
        assert "missing_file" in capsys.readouterr().out
        assert main(["validate", "--files-path", str(tmp_path / "files"), str(root)]) == 0
        assert "valid — 0 error(s), 0 warning(s)" in capsys.readouterr().out

    def test_validate_reports_a_bad_file_with_its_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _export(tmp_path / "export", tmp_path / "files", LOG)
        (tmp_path / "files" / stored(LOG)).write_bytes(b"tampered")
        assert main(["validate", "--files-path", str(tmp_path / "files"), str(root)]) == 1
        out = capsys.readouterr().out
        assert f"attachments.jsonl:1: error: files/{stored(LOG)} does not hash to its name [hash_mismatch]" in out

    def test_stats_counts_the_files_in_the_files_directory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _export(tmp_path / "export.zip", tmp_path / "files", LOG, SCREENSHOT)
        assert main(["stats", "--json", "--files-path", str(tmp_path / "files"), str(root)]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["valid"] is True
        assert data["stored_files"] == 2
