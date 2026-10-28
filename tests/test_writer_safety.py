"""The writer never finishes a bundle Specivo would refuse, and a failure leaves nothing half done."""

from __future__ import annotations

import json
import math
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from specivo_migration_sdk import (
    BundleLimits,
    BundleTooLargeError,
    BundleWriter,
    Group,
    RecordError,
    Status,
    User,
    WikiPage,
    WikiVersion,
    is_source_ref,
    placeholders,
    validate_bundle,
)
from specivo_migration_sdk.limits import MIB


def _writer(target: Path, **kwargs: Any) -> BundleWriter:
    return BundleWriter(
        target,
        source_system="sample",
        source_instance="tracker.example.com",
        generator_name="pytest",
        generator_version="1",
        **kwargs,
    )


def _page(ref: str, *version_refs: str) -> WikiPage:
    versions = [WikiVersion(source_ref=v, version=n, text="x") for n, v in enumerate(version_refs, start=1)]
    return WikiPage(source_ref=ref, project_ref="p1", title=ref, versions=versions)


class TestZipEntriesSpecivoAccepts:
    def test_a_file_of_zeros_is_stored_not_deflated(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b.zip") as writer:
            stored = writer.add_file(b"\0" * (4 * MIB))
        with zipfile.ZipFile(tmp_path / "b.zip") as archive:
            info = archive.getinfo(f"files/{stored.sha256}")
        assert info.compress_type == zipfile.ZIP_STORED
        report = validate_bundle(tmp_path / "b.zip")
        assert report.ok, report.format_text()

    def test_a_records_file_that_deflates_too_well_is_stored(self, tmp_path: Path) -> None:
        # Identical lines compress far beyond the 200:1 Specivo accepts.
        with _writer(tmp_path / "b.zip") as writer:
            for number in range(1_000):
                writer.add(User(source_ref=f"u{number}", login=f"u{number}", display_name="x" * 5_000))
        with zipfile.ZipFile(tmp_path / "b.zip") as archive:
            users = archive.getinfo("users.jsonl")
            manifest = archive.getinfo("manifest.json")
        assert users.file_size > MIB
        assert users.compress_type == zipfile.ZIP_STORED
        assert manifest.compress_type == zipfile.ZIP_DEFLATED
        assert validate_bundle(tmp_path / "b.zip").ok

    def test_with_a_low_ratio_limit_the_writer_agrees_with_the_validator(self, tmp_path: Path) -> None:
        limits = BundleLimits(max_compression_ratio=2)
        with _writer(tmp_path / "b.zip", limits=limits) as writer:
            for number in range(5_000):
                writer.add(User(source_ref=f"u{number}", login=f"u{number}", display_name="y" * 300))
        assert validate_bundle(tmp_path / "b.zip", limits=limits).ok


class TestLimits:
    @pytest.mark.parametrize(
        ("limits", "message"),
        [
            (BundleLimits(max_entries=3), "entries"),
            (BundleLimits(max_entry_bytes=1000), "one archive entry"),
            (BundleLimits(max_unpacked_bytes=2500), "unpack to"),
            (BundleLimits(max_json_bytes=100), "manifest.json"),
        ],
    )
    def test_a_zip_over_a_limit_is_not_written(self, tmp_path: Path, limits: BundleLimits, message: str) -> None:
        writer = _writer(tmp_path / "b.zip", limits=limits)
        writer.add(User(source_ref="u1", login="a", display_name="A"))
        writer.add_file(b"x" * 2000)
        writer.add_file(b"y" * 10)
        with pytest.raises(BundleTooLargeError, match=message):
            writer.close()
        assert list(tmp_path.iterdir()) == []

    def test_a_directory_is_left_without_a_manifest(self, tmp_path: Path) -> None:
        writer = _writer(tmp_path / "b", limits=BundleLimits(max_json_bytes=1000))
        for number in range(30):
            writer.add(Status(source_ref=f"s{number}", name=f"Status {number}", category="active"))
        with pytest.raises(BundleTooLargeError, match="lookups.json"):
            writer.close()
        assert not (tmp_path / "b" / "manifest.json").exists()
        assert not (tmp_path / "b" / "lookups.json").exists()

    def test_zip_limits_do_not_apply_to_a_directory(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b", limits=BundleLimits(max_entries=1)) as writer:
            writer.add_file(b"x")
            writer.add_file(b"y")
        assert (tmp_path / "b" / "manifest.json").exists()


class TestFailures:
    def test_a_failure_while_closing_abandons_and_reraises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        writer = _writer(tmp_path / "b.zip")
        writer.add(User(source_ref="u1", login="a", display_name="A"))

        def broken(self: BundleWriter) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(BundleWriter, "_write_zip", broken)
        with pytest.raises(OSError, match="disk full"):
            writer.close()
        assert list(tmp_path.iterdir()) == []
        with pytest.raises(RuntimeError, match="closed"):
            writer.add(User(source_ref="u2", login="b", display_name="B"))

    def test_a_bad_manifest_creates_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        with pytest.raises(RecordError):
            BundleWriter(
                tmp_path / "out" / "b.zip",
                source_system="Bad System",
                source_instance="x",
                generator_name="pytest",
                generator_version="1",
            )
        assert list(tmp_path.iterdir()) == []

    def test_a_bad_export_time_creates_nothing(self, tmp_path: Path) -> None:
        with pytest.raises(RecordError, match="exported_at"):
            _writer(tmp_path / "b", exported_at="2024-03-01T09:30:00")
        assert not (tmp_path / "b").exists()


class TestWikiPageRefs:
    def test_a_revision_ref_used_twice_in_one_page_claims_nothing(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            with pytest.raises(RecordError, match="wiki_version"):
                writer.add(_page("w1", "v1", "v1"))
            # Nothing of the refused page was claimed.
            writer.add(_page("w1", "v1"))

    def test_a_taken_revision_ref_leaves_the_page_ref_free(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            writer.add(_page("w1", "v1"))
            with pytest.raises(RecordError, match="wiki_version"):
                writer.add(_page("w2", "v2", "v1"))
            writer.add(_page("w2", "v2"))
        assert validate_bundle(tmp_path / "b").records["wiki_pages"] == 2


class TestNewlineRefs:
    def test_helpers_refuse_a_trailing_newline(self) -> None:
        assert not is_source_ref("u1\n")
        with pytest.raises(ValueError, match="source_ref"):
            placeholders.user("u1\n")
        with pytest.raises(ValueError, match="source_ref"):
            placeholders.issue("i1\n", "text")

    def test_the_writer_refuses_a_trailing_newline(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            with pytest.raises(RecordError, match="not a valid source_ref"):
                writer.add(User(source_ref="u1\n", login="a", display_name="A"))
            with pytest.raises(RecordError, match="not a valid source_ref"):
                writer.add(Status(source_ref="s1\n", name="Open", category="backlog"))
            with pytest.raises(RecordError, match="not a valid source_ref"):
                writer.add(_page("w1", "v1\n"))


class TestValuesJsonCannotHold:
    @pytest.mark.parametrize("value", [math.nan, math.inf, object(), {1, 2}])
    def test_extra_that_is_not_json_names_the_file_and_record(self, tmp_path: Path, value: object) -> None:
        with _writer(tmp_path / "b") as writer, pytest.raises(RecordError, match=r"groups\.jsonl: 'g1': cannot be"):
            writer.add(Group(source_ref="g1", name="Team", extra={"bad": value}))
        assert not (tmp_path / "b" / "groups.jsonl").exists()

    def test_in_a_lookup_it_is_reported_when_added(self, tmp_path: Path) -> None:
        with _writer(tmp_path / "b") as writer:
            with pytest.raises(RecordError, match=r"lookups\.json: 's1'"):
                writer.add(Status(source_ref="s1", name="Open", category="backlog", extra={"x": math.nan}))
        assert "s1" not in json.dumps(json.loads((tmp_path / "b" / "manifest.json").read_text()))
