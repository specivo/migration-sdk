"""BundleWriter: streaming records, content-addressed files, the manifest, zip output."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from specivo_migration_sdk import (
    Attachment,
    BundleLimits,
    BundleWriter,
    Credential,
    Issue,
    Priority,
    Project,
    PublicAccess,
    RecordError,
    Status,
    Tracker,
    User,
    WikiPage,
    WikiVersion,
    placeholders,
    validate_bundle,
)
from specivo_migration_sdk.format import COUNT_KEYS
from tests.bundles import LOG, SCREENSHOT, write_full


def _writer(target: Path, **kwargs: Any) -> BundleWriter:
    return BundleWriter(
        target,
        source_system="sample",
        source_instance="tracker.example.com",
        generator_name="pytest",
        generator_version="1",
        **kwargs,
    )


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _manifest(root: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((root / "manifest.json").read_text())
    return data


class TestADirectoryBundle:
    def test_the_full_bundle(self, tmp_path: Path) -> None:
        root = write_full(tmp_path / "full")
        report = validate_bundle(root)
        assert report.ok, report.format_text()
        assert report.problems == []

    def test_the_manifest_counts_every_file(self, tmp_path: Path) -> None:
        manifest = _manifest(write_full(tmp_path / "full"))
        assert manifest["format"] == "specivo-import-bundle"
        assert manifest["format_version"] == "1.0"
        assert manifest["source"] == {"system": "sample", "instance": "tracker.example.com", "version": "4.2"}
        assert manifest["generator"] == {"name": "pytest", "version": "1"}
        assert manifest["exported_at"] == "2024-03-01T09:30:00Z"
        counts = manifest["counts"]
        assert counts["issues"] == 2
        assert counts["wiki_versions"] == 3
        assert counts["statuses"] == 3
        assert set(counts) == set(COUNT_KEYS)
        assert manifest["not_carried_over"] == [{"what": "Sprint boards have no equivalent", "count": 2}]

    def test_records_are_one_line_each_with_defaults_left_out(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            writer.add(User(source_ref="u1", login="alice", display_name="Alice\nExample", status="active"))
        assert (tmp_path / "b" / "users.jsonl").read_text().count("\n") == 1
        assert _lines(tmp_path / "b" / "users.jsonl") == [
            {"source_ref": "u1", "login": "alice", "display_name": "Alice\nExample"}
        ]

    def test_text_is_written_as_utf8(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            writer.add(User(source_ref="u1", login="somchai", display_name="สมชาย"))
        assert "สมชาย" in (tmp_path / "b" / "users.jsonl").read_text(encoding="utf-8")

    def test_lookups_and_public_access_are_json_files(self, tmp_path: Path) -> None:
        root = write_full(tmp_path / "full")
        lookups = json.loads((root / "lookups.json").read_text())
        assert [s["source_ref"] for s in lookups["statuses"]] == ["open", "doing", "done"]
        assert "roles" in lookups and "activities" in lookups
        access = json.loads((root / "public_access.json").read_text())
        assert access == {"anonymous_allowed": True, "anonymous_permissions": {"p1": ["view_issues", "view_wiki"]}}

    def test_a_bundle_without_lookups_has_no_lookups_file(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            writer.add(User(source_ref="u1", login="alice", display_name="Alice"))
        assert not (tmp_path / "b" / "lookups.json").exists()
        assert validate_bundle(tmp_path / "b").ok
        # Every count is written, files the bundle does not have as zero.
        assert _manifest(tmp_path / "b")["counts"]["issues"] == 0

    def test_an_empty_directory_is_accepted(self, tmp_path: Path) -> None:
        (tmp_path / "b").mkdir()
        _writer(tmp_path / "b").close()
        assert validate_bundle(tmp_path / "b").ok

    def test_a_directory_with_something_in_it_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "notes.txt").write_text("keep me")
        with pytest.raises(FileExistsError):
            _writer(tmp_path / "b")

    def test_a_failed_export_leaves_no_manifest(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError), _writer(tmp_path / "b") as writer:
            writer.add(User(source_ref="u1", login="alice", display_name="Alice"))
            raise RuntimeError("source went away")
        assert not (tmp_path / "b" / "manifest.json").exists()
        assert not validate_bundle(tmp_path / "b").ok

    def test_close_returns_the_path_and_can_be_called_twice(self, tmp_path: Path) -> None:
        writer = _writer(tmp_path / "b")
        assert writer.close() == tmp_path / "b"
        assert writer.close() == tmp_path / "b"

    def test_nothing_can_be_added_once_closed(self, tmp_path: Path) -> None:
        writer = _writer(tmp_path / "b")
        writer.close()
        with pytest.raises(RuntimeError, match="closed"):
            writer.add(User(source_ref="u1", login="alice", display_name="Alice"))

    def test_counts_so_far(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            writer.add(Project(source_ref="p1", identifier="web", name="Website"))
            writer.add(
                WikiPage(
                    source_ref="w1",
                    project_ref="p1",
                    title="Home",
                    versions=[WikiVersion(source_ref="v1", version=1, text="a")],
                )
            )
            assert writer.counts["projects"] == 1
            assert writer.counts["wiki_versions"] == 1
            assert writer.counts["issues"] == 0

    def test_an_issue_placeholder_may_name_an_issue_of_an_earlier_bundle(self, tmp_path: Path) -> None:
        """A source exported one project at a time refers to issues a previous bundle carried."""
        with _writer(tmp_path / "b") as writer:
            writer.add_all(
                [
                    Tracker(source_ref="bug", name="Bug", default_status_ref="open"),
                    Status(source_ref="open", name="Open", category="backlog"),
                    Priority(source_ref="normal", name="Normal", is_default=True),
                    Project(source_ref="p2", identifier="api", name="API"),
                    Issue(
                        source_ref="20",
                        project_ref="p2",
                        tracker_ref="bug",
                        status_ref="open",
                        priority_ref="normal",
                        subject="Follow-up",
                        description=f"Follows {placeholders.issue('10', 'the web bug')}.",
                    ),
                ]
            )
        report = validate_bundle(tmp_path / "b")
        assert report.ok, report.format_text()
        assert [(p.file, str(p.code)) for p in report.warnings] == [("issues.jsonl", "external_ref")]


class TestAZipBundle:
    def test_a_zip_target_writes_an_archive(self, tmp_path: Path) -> None:
        archive = write_full(tmp_path / "export.zip")
        assert zipfile.is_zipfile(archive)
        with zipfile.ZipFile(archive) as handle:
            names = set(handle.namelist())
        assert {"manifest.json", "lookups.json", "issues.jsonl", f"files/{hashlib.sha256(LOG).hexdigest()}"} <= names
        report = validate_bundle(archive)
        assert report.ok, report.format_text()
        # Nothing is left next to it.
        assert [path.name for path in tmp_path.iterdir()] == ["export.zip"]

    def test_zip_can_be_asked_for_whatever_the_name(self, tmp_path: Path) -> None:
        _writer(tmp_path / "export.bundle", zip=True).close()
        assert zipfile.is_zipfile(tmp_path / "export.bundle")

    def test_an_existing_archive_is_not_overwritten(self, tmp_path: Path) -> None:
        (tmp_path / "export.zip").write_bytes(b"precious")
        with pytest.raises(FileExistsError):
            _writer(tmp_path / "export.zip")
        assert (tmp_path / "export.zip").read_bytes() == b"precious"

    def test_a_failed_export_writes_no_archive(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError), _writer(tmp_path / "export.zip") as writer:
            writer.add_file(b"bytes")
            raise RuntimeError("source went away")
        assert list(tmp_path.iterdir()) == []


class TestFiles:
    def test_a_file_is_named_by_its_hash(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            stored = writer.add_file(LOG)
        assert stored.sha256 == hashlib.sha256(LOG).hexdigest()
        assert stored.size == len(LOG)
        assert (tmp_path / "b" / "files" / stored.sha256).read_bytes() == LOG

    def test_the_same_content_is_stored_once(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            first = writer.add_file(LOG)
            second = writer.add_file(LOG)
            writer.add_file(SCREENSHOT)
        assert first == second
        assert sorted(path.name for path in (tmp_path / "b" / "files").iterdir()) == sorted(
            [hashlib.sha256(LOG).hexdigest(), hashlib.sha256(SCREENSHOT).hexdigest()]
        )

    def test_every_kind_of_source(self, tmp_path: Path) -> None:
        on_disk = tmp_path / "log.txt"
        on_disk.write_bytes(LOG)
        expected = hashlib.sha256(LOG).hexdigest()
        with _writer(tmp_path / "b") as writer:
            assert writer.add_file(on_disk).sha256 == expected
            assert writer.add_file(str(on_disk)).sha256 == expected
            assert writer.add_file(io.BytesIO(LOG)).sha256 == expected
            assert writer.add_file(iter([LOG[:3], LOG[3:]])).sha256 == expected
            assert writer.add_file(bytearray(LOG)).sha256 == expected

    def test_an_empty_file(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            assert writer.add_file(b"").size == 0

    def test_a_source_that_fails_leaves_nothing_behind(self, tmp_path: Path) -> None:
        def broken() -> Any:
            yield b"partial"
            raise OSError("connection reset")

        with _writer(tmp_path / "b") as writer:
            with pytest.raises(OSError, match="connection reset"):
                writer.add_file(broken())
        assert list((tmp_path / "b" / "files").iterdir()) == []

    def test_chunks_must_be_bytes(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer, pytest.raises(TypeError):
            writer.add_file(iter(["text"]))  # type: ignore[arg-type]

    def test_an_attachment_needs_its_file_first(self, tmp_path: Path) -> None:
        attachment = Attachment(
            source_ref="a1", container_kind="issue", container_ref="i1", filename="log.txt", sha256="0" * 64
        )
        with _writer(tmp_path / "b") as writer, pytest.raises(RecordError, match="add_file"):
            writer.add(attachment)


class TestRecordsAreChecked:
    def test_a_schema_error_names_the_record_and_property(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            with pytest.raises(RecordError, match=r"issues\.jsonl: 'i1': /done_ratio: 150 is greater than"):
                writer.add(
                    Issue(
                        source_ref="i1",
                        project_ref="p1",
                        tracker_ref="bug",
                        status_ref="open",
                        priority_ref="normal",
                        subject="Too done",
                        done_ratio=150,
                    )
                )
        assert not (tmp_path / "b" / "issues.jsonl").exists()

    def test_a_bad_credential_is_reported_without_its_value(self, tmp_path: Path) -> None:
        secret = "$2y$10$Q9mWkzVtH3secretvaluexyzAbCdEfGhIjKlMnOpQrStUvWxYz012"
        user = User(
            source_ref="u1", login="a", display_name="A", credential=Credential(algorithm="", fields={"hash": secret})
        )
        with _writer(tmp_path / "b") as writer, pytest.raises(RecordError) as caught:
            writer.add(user)
        assert str(caught.value) == "users.jsonl: 'u1': /credential: does not match the schema (anyOf)"
        assert secret not in repr(caught.value)

    def test_a_bad_source_ref(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer, pytest.raises(RecordError, match="source_ref"):
            writer.add(User(source_ref="has space", login="a", display_name="A"))

    def test_a_timestamp_without_an_offset(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer, pytest.raises(RecordError, match="User.created_at: .*no UTC offset"):
            writer.add(User(source_ref="u1", login="a", display_name="A", created_at="2024-02-01T10:00:00"))

    def test_a_duplicate_source_ref(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            writer.add(User(source_ref="u1", login="a", display_name="A"))
            with pytest.raises(RecordError, match="Duplicate source_ref 'u1' for user"):
                writer.add(User(source_ref="u1", login="b", display_name="B"))
            # Another entity type has its own namespace.
            writer.add(Project(source_ref="u1", identifier="x", name="X"))

    def test_a_duplicate_lookup_ref(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            writer.add(Status(source_ref="s1", name="Open", category="backlog"))
            with pytest.raises(RecordError, match="Duplicate"):
                writer.add(Status(source_ref="s1", name="Closed", category="closed"))

    def test_a_duplicate_wiki_revision_ref(self, tmp_path: Path) -> None:
        def page(ref: str) -> WikiPage:
            return WikiPage(
                source_ref=ref, project_ref="p1", title=ref, versions=[WikiVersion(source_ref="v1", version=1, text="")]
            )

        with _writer(tmp_path / "b") as writer:
            writer.add(page("w1"))
            with pytest.raises(RecordError, match="wiki_version"):
                writer.add(page("w2"))

    def test_a_lookup_schema_error(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer, pytest.raises(RecordError, match="category"):
            writer.add(Status(source_ref="s1", name="Open", category="later"))  # type: ignore[arg-type]

    def test_a_line_over_the_limit(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b", limits=BundleLimits(max_line_bytes=200)) as writer:
            with pytest.raises(RecordError, match="over the 200"):
                writer.add(User(source_ref="u1", login="a", display_name="x" * 500))

    def test_text_that_is_not_valid_unicode(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer, pytest.raises(RecordError, match="not valid Unicode"):
            writer.add(User(source_ref="u1", login="a", display_name="broken \udc80 surrogate"))

    def test_something_that_is_not_a_record(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer, pytest.raises(TypeError):
            writer.add(PublicAccess())  # type: ignore[arg-type]

    def test_a_bad_manifest_is_refused_before_anything_is_written(self, tmp_path: Path) -> None:
        with pytest.raises(RecordError, match="system"):
            BundleWriter(
                tmp_path / "b",
                source_system="Not A System",
                source_instance="x",
                generator_name="pytest",
                generator_version="1",
            )

    def test_a_bad_not_carried_over_item(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer, pytest.raises(RecordError):
            writer.not_carried_over("")

    def test_a_bad_public_access(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer, pytest.raises(RecordError):
            writer.set_public_access(PublicAccess(anonymous_permissions={"p1": ["edit_everything"]}))  # type: ignore[list-item]
