"""validate_bundle(): the checks Specivo makes before it loads a bundle.

The bad cases are the ones Specivo's own reader is tested with, so the two
cannot quietly disagree about what a valid bundle is.
"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from specivo_migration_sdk import BundleLimits, Severity, ValidationReport, inspect_bundle, validate_bundle
from specivo_migration_sdk import container as container_module
from specivo_migration_sdk import validate as validate_module
from specivo_migration_sdk.limits import MIB
from tests.bundles import (
    bundle_entries,
    issue,
    make_zip,
    manifest,
    project,
    stored,
    user,
    within_seconds,
    write_bundle,
    write_full,
)

type Codes = list[tuple[str, int | None, str]]


def _codes(report: ValidationReport, severity: Severity = Severity.ERROR) -> Codes:
    return [(p.file, p.line, str(p.code)) for p in report.problems if p.severity is severity]


@pytest.fixture
def private_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where zips are unpacked during the test, so leftovers can be seen."""
    directory = tmp_path / "unpacked"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


@pytest.fixture
def full(tmp_path: Path) -> Path:
    return write_full(tmp_path / "full")


def _small_bundle(**extra: bytes) -> dict[str, bytes]:
    return {
        "manifest.json": json.dumps(manifest()).encode(),
        "users.jsonl": (json.dumps(user("u1")) + "\n").encode(),
        **extra,
    }


class TestAValidBundle:
    def test_the_full_bundle_is_valid(self, full: Path) -> None:
        report = validate_bundle(full)
        assert report.ok, report.format_text()
        assert report.problems == []
        assert report.records["issues"] == 2
        assert report.records["wiki_versions"] == 3

    def test_a_zip_of_it_is_valid_too(self, tmp_path: Path, full: Path) -> None:
        archive = make_zip(tmp_path / "full.zip", bundle_entries(full))
        report = validate_bundle(archive)
        assert report.ok, report.format_text()

    def test_only_the_manifest_is_required(self, tmp_path: Path) -> None:
        root = tmp_path / "b"
        root.mkdir()
        (root / "manifest.json").write_text(json.dumps(manifest()))
        assert validate_bundle(root).ok

    def test_a_path_may_be_a_string(self, full: Path) -> None:
        assert validate_bundle(str(full)).ok


class TestVersions:
    def test_an_unknown_major_version_is_refused(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", manifest_data=manifest(format_version="2.0"), users="not json at all\n")
        report = validate_bundle(root)
        # Refused on the manifest alone: nothing else is read.
        assert _codes(report) == [("manifest.json", None, "unsupported_version")]
        assert "version 1.x" in report.problems[0].message

    def test_a_newer_minor_version_is_valid_with_its_new_fields_ignored(self, tmp_path: Path) -> None:
        root = write_bundle(
            tmp_path / "b",
            manifest_data=manifest(format_version="1.4"),
            users=[user("u1", pronouns="they/them")],
        )
        report = validate_bundle(root)
        assert report.ok
        assert ("users.jsonl", 1, "schema") in _codes(report, Severity.WARNING)

    def test_an_unknown_field_is_an_error_in_the_same_minor_version(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", users=[user("u1", pronouns="they/them")])
        assert _codes(validate_bundle(root)) == [("users.jsonl", 1, "schema")]

    def test_something_that_is_not_a_bundle(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", manifest_data=manifest(format="other-format"))
        assert _codes(validate_bundle(root)) == [("manifest.json", None, "unsupported_version")]

    def test_no_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "b").mkdir()
        assert _codes(validate_bundle(tmp_path / "b")) == [("manifest.json", None, "missing_file")]

    def test_a_manifest_that_is_not_an_object(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b")
        (root / "manifest.json").write_text("[]")
        assert _codes(validate_bundle(root)) == [("manifest.json", None, "schema")]

    def test_a_path_that_is_neither_a_directory_nor_a_zip(self, tmp_path: Path) -> None:
        (tmp_path / "bundle.tar").write_bytes(b"not an archive")
        assert _codes(validate_bundle(tmp_path / "bundle.tar")) == [("bundle.tar", None, "unreadable")]

    def test_the_instance_is_required(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", manifest_data=manifest(source={"system": "sample"}))
        report = validate_bundle(root)
        assert _codes(report) == [("manifest.json", None, "schema")]
        assert "'instance' is a required property" in report.problems[0].message


class TestRecords:
    def test_invalid_json_names_its_line(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", users=json.dumps(user("u1")) + "\n{oops\n")
        assert _codes(validate_bundle(root)) == [("users.jsonl", 2, "invalid_json")]

    def test_a_schema_error_names_the_line_and_the_property(self, tmp_path: Path) -> None:
        root = write_bundle(
            tmp_path / "b",
            projects=[project("p1")],
            issues=[issue("i1"), issue("i2", created_at="2024-02-01 10:00")],
        )
        report = validate_bundle(root)
        assert _codes(report) == [("issues.jsonl", 2, "schema")]
        assert report.problems[0].message.startswith("/created_at:")

    def test_a_timestamp_without_an_offset_is_rejected(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", users=[user("u1", created_at="2024-02-01T10:00:00")])
        assert _codes(validate_bundle(root)) == [("users.jsonl", 1, "schema")]

    def test_a_missing_required_property(self, tmp_path: Path) -> None:
        record = issue("i1")
        del record["subject"]
        root = write_bundle(tmp_path / "b", projects=[project("p1")], issues=[record])
        report = validate_bundle(root)
        assert _codes(report) == [("issues.jsonl", 1, "schema")]
        assert "'subject' is a required property" in report.problems[0].message

    def test_a_source_ref_with_whitespace_is_rejected(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", users=[user("has space")])
        assert _codes(validate_bundle(root)) == [("users.jsonl", 1, "schema")]

    def test_blank_lines_are_ignored(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", users="\n" + json.dumps(user("u1")) + "\n\n")
        report = validate_bundle(root)
        assert report.ok and report.records["users"] == 1

    @pytest.mark.parametrize(
        ("stem", "records", "message"),
        [
            ("users", [user("u1", created_at="2024-02-30T10:00:00Z")], "/created_at: '2024-02-30T10:00:00Z'"),
            ("users", [user("u1", last_login_at="2024-02-01T25:00:00+07:00")], "/last_login_at:"),
            ("issues", [issue("i1", start_date="2024-13-01")], "/start_date: '2024-13-01' is not a valid date"),
        ],
    )
    def test_a_date_that_does_not_exist_is_a_schema_error(
        self, tmp_path: Path, stem: str, records: list[dict[str, Any]], message: str
    ) -> None:
        by_file: dict[str, Any] = {"projects": [project("p1")], stem: records}
        report = validate_bundle(write_bundle(tmp_path / "b", **by_file))
        assert _codes(report) == [(f"{stem}.jsonl", 1, "schema")]
        assert report.problems[0].message.startswith(message)

    def test_a_bad_date_inside_a_nested_record(self, tmp_path: Path) -> None:
        page = {
            "source_ref": "w1",
            "project_ref": "p1",
            "title": "Home",
            "versions": [{"source_ref": "v1", "version": 1, "text": "x", "created_at": "2024-02-31T00:00:00Z"}],
        }
        report = validate_bundle(write_bundle(tmp_path / "b", projects=[project("p1")], wiki_pages=[page]))
        assert _codes(report) == [("wiki_pages.jsonl", 1, "schema")]
        assert report.problems[0].message.startswith("/versions/0/created_at:")

    def test_a_bad_export_time(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", manifest_data=manifest(exported_at="2024-02-30T10:00:00Z"))
        assert _codes(validate_bundle(root)) == [("manifest.json", None, "schema")]

    def test_nan_is_not_a_number(self, tmp_path: Path) -> None:
        entry = '{"source_ref": "t1", "project_ref": "p1", "hours": NaN, "spent_on": "2024-01-01"}\n'
        root = write_bundle(tmp_path / "b", projects=[project("p1")], time_entries=entry)
        assert _codes(validate_bundle(root)) == [("time_entries.jsonl", 1, "invalid_json")]


class TestReferences:
    def test_an_unresolved_ref_names_its_line_and_field(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", projects=[project("p1")], issues=[issue("i1", author_ref="ghost")])
        report = validate_bundle(root)
        assert _codes(report) == [("issues.jsonl", 1, "unresolved_ref")]
        assert "author_ref: no user with source_ref 'ghost'" in report.problems[0].message

    def test_a_parent_further_down_the_file_resolves(self, tmp_path: Path) -> None:
        root = write_bundle(
            tmp_path / "b", projects=[project("p1")], issues=[issue("child", parent_ref="parent"), issue("parent")]
        )
        assert validate_bundle(root).ok

    def test_a_missing_parent_is_reported(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", projects=[project("p1")], issues=[issue("child", parent_ref="parent")])
        assert _codes(validate_bundle(root)) == [("issues.jsonl", 1, "unresolved_ref")]

    def test_a_lookup_ref_is_checked(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", projects=[project("p1")], issues=[issue("i1", status_ref="closed")])
        assert _codes(validate_bundle(root)) == [("issues.jsonl", 1, "unresolved_ref")]

    def test_a_reference_inside_lookups_gives_a_pointer(self, tmp_path: Path) -> None:
        lookups = {"trackers": [{"source_ref": "bug", "name": "Bug", "default_status_ref": "nope"}]}
        report = validate_bundle(write_bundle(tmp_path / "b", lookups=lookups))
        assert [(p.file, p.pointer, str(p.code)) for p in report.errors] == [
            ("lookups.json", "/trackers/0/default_status_ref", "unresolved_ref")
        ]

    def test_a_duplicate_ref_names_both_lines(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", users=[user("u1"), user("u2"), user("u1")])
        report = validate_bundle(root)
        assert _codes(report) == [("users.jsonl", 3, "duplicate_ref")]
        assert "(first at line 1)" in report.problems[0].message

    def test_a_duplicate_lookup_ref_gives_a_pointer(self, tmp_path: Path) -> None:
        lookups = {"priorities": [{"source_ref": "n", "name": "Normal"}, {"source_ref": "n", "name": "Low"}]}
        report = validate_bundle(write_bundle(tmp_path / "b", lookups=lookups))
        assert [(p.file, p.pointer, str(p.code)) for p in report.errors] == [
            ("lookups.json", "/priorities/1", "duplicate_ref")
        ]

    def test_the_same_ref_in_two_entity_types_is_fine(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", users=[user("1")], projects=[project("1")], issues=[issue("1", "1")])
        assert validate_bundle(root).ok

    def test_a_placeholder_naming_a_missing_record(self, tmp_path: Path) -> None:
        root = write_bundle(
            tmp_path / "b",
            projects=[project("p1")],
            issues=[issue("i1", description="See {{issue:i9}} and {{wiki:w1}}.")],
        )
        report = validate_bundle(root)
        # An issue may be in an earlier bundle of the same source; any other
        # record must be in this one.
        assert _codes(report) == [("issues.jsonl", 1, "unresolved_ref")]
        assert report.errors[0].message.startswith("description {{wiki:…}}: no wiki_page")
        assert _codes(report, Severity.WARNING) == [("issues.jsonl", None, "external_ref")]

    def test_an_issue_placeholder_outside_the_bundle_is_a_warning(self, tmp_path: Path) -> None:
        """The load looks it up among the source's earlier imports, which validation cannot see."""
        root = write_bundle(
            tmp_path / "b",
            projects=[project("p1")],
            issues=[
                issue("i1", description="Follows {{issue:a-7}} and {{issue:i2}}."),
                issue("i2", description="Also {{issue:a-7|the old bug}}, {{issue:a-8}}."),
            ],
            journals=[{"source_ref": "j1", "issue_ref": "i1", "notes": "See {{issue:a-9}}."}],
            wiki_pages=[
                {
                    "source_ref": "w1",
                    "project_ref": "p1",
                    "title": "Home",
                    "versions": [{"source_ref": "wv1", "version": 1, "text": "{{issue:a-7}}"}],
                }
            ],
        )
        report = validate_bundle(root)
        assert report.ok, report.format_text()
        assert [(p.file, p.line, p.pointer, str(p.code)) for p in report.warnings] == [
            ("issues.jsonl", None, None, "external_ref"),
            ("journals.jsonl", None, None, "external_ref"),
            ("wiki_pages.jsonl", None, None, "external_ref"),
        ]
        assert [p.message for p in report.warnings] == [
            "3 issue placeholder(s) name no issue in this bundle, e.g. 'a-7' (line 1), 'a-8' (line 2); "
            "loading looks them up in earlier imports of the same source and writes any it cannot find as plain text",
            "1 issue placeholder(s) name no issue in this bundle, e.g. 'a-9' (line 1); "
            "loading looks them up in earlier imports of the same source and writes any it cannot find as plain text",
            "1 issue placeholder(s) name no issue in this bundle, e.g. 'a-7' (line 1); "
            "loading looks them up in earlier imports of the same source and writes any it cannot find as plain text",
        ]

    def test_an_outside_issue_warning_gives_at_most_five_examples(self, tmp_path: Path) -> None:
        text = " ".join(f"{{{{issue:x-{n}}}}}" for n in range(7))
        root = write_bundle(tmp_path / "b", projects=[project("p1")], issues=[issue("i1", description=text)])
        (warning,) = validate_bundle(root).warnings
        assert warning.message.startswith(
            "7 issue placeholder(s) name no issue in this bundle, e.g. 'x-0' (line 1), 'x-1' (line 1), "
            "'x-2' (line 1), 'x-3' (line 1), 'x-4' (line 1); "
        )

    def test_examples_are_distinct_refs_in_line_order(self, tmp_path: Path) -> None:
        root = write_bundle(
            tmp_path / "b",
            projects=[project("p1")],
            issues=[
                issue("i1", description="{{issue:b}} {{issue:a}}"),
                issue("i2", description="{{issue:a}} {{issue:c}}"),
            ],
        )
        (warning,) = validate_bundle(root).warnings
        assert warning.message.startswith(
            "4 issue placeholder(s) name no issue in this bundle, e.g. 'b' (line 1), 'a' (line 1), 'c' (line 2); "
        )

    @pytest.mark.parametrize(
        ("text", "kind"),
        [("{{user:u9}}", "user"), ("[f]({{attachment:a9}})", "attachment"), ("{{wiki:w9}}", "wiki_page")],
    )
    def test_other_placeholders_outside_the_bundle_are_errors(self, tmp_path: Path, text: str, kind: str) -> None:
        root = write_bundle(tmp_path / "b", projects=[project("p1")], issues=[issue("i1", description=text)])
        report = validate_bundle(root)
        assert _codes(report) == [("issues.jsonl", 1, "unresolved_ref")]
        assert f": no {kind} with source_ref" in report.errors[0].message
        assert report.warnings == []

    def test_a_placeholder_may_name_a_record_in_a_later_file(self, tmp_path: Path) -> None:
        data = b"bytes"
        root = write_bundle(
            tmp_path / "b",
            projects=[project("p1")],
            issues=[issue("i1", description="[log]({{attachment:a1}}) {{wiki:w1}}")],
            wiki_pages=[{"source_ref": "w1", "project_ref": "p1", "title": "Home"}],
            attachments=[
                {
                    "source_ref": "a1",
                    "container_kind": "issue",
                    "container_ref": "i1",
                    "filename": "log.txt",
                    "sha256": stored(data),
                }
            ],
            files={stored(data): data},
        )
        assert validate_bundle(root).ok

    def test_a_placeholder_in_code_is_not_checked(self, tmp_path: Path) -> None:
        root = write_bundle(
            tmp_path / "b", projects=[project("p1")], issues=[issue("i1", description="```\n{{issue:nope}}\n```")]
        )
        assert validate_bundle(root).ok

    def test_a_user_placeholder_takes_no_label(self, tmp_path: Path) -> None:
        root = write_bundle(
            tmp_path / "b",
            users=[user("u1")],
            projects=[project("p1")],
            issues=[issue("i1", description="{{user:u1|Alex}}")],
        )
        assert _codes(validate_bundle(root)) == [("issues.jsonl", 1, "invalid_ref")]

    @pytest.mark.parametrize("label", ["the runbook]]", "x]](javascript:alert(1))"])
    def test_a_wiki_label_may_not_close_the_link(self, tmp_path: Path, label: str) -> None:
        page = {"source_ref": "w1", "project_ref": "p1", "title": "Home"}
        root = write_bundle(
            tmp_path / "b",
            projects=[project("p1")],
            issues=[issue("i1", description=f"{{{{wiki:w1|{label}}}}}")],
            wiki_pages=[page],
        )
        report = validate_bundle(root)
        assert _codes(report) == [("issues.jsonl", 1, "invalid_ref")]
        assert "']]'" in report.problems[0].message

    def test_an_issue_assigned_to_a_group_is_a_warning(self, tmp_path: Path) -> None:
        root = write_bundle(
            tmp_path / "b",
            groups=[{"source_ref": "g1", "name": "Team"}],
            projects=[project("p1")],
            issues=[issue("i1", assigned_to_ref="g1")],
        )
        report = validate_bundle(root)
        assert report.ok
        assert _codes(report, Severity.WARNING) == [("issues.jsonl", 1, "invalid_ref")]

    def test_a_custom_value_must_use_its_fields_key(self, tmp_path: Path) -> None:
        field = {"source_ref": "cf1", "name": "Severity", "key": "severity", "field_format": "list"}
        value = {"field_ref": "cf1", "key": "sev", "value": "High"}
        root = write_bundle(
            tmp_path / "b", projects=[project("p1")], custom_fields=[field], issues=[issue("i1", custom_values=[value])]
        )
        assert _codes(validate_bundle(root)) == [("issues.jsonl", 1, "invalid_ref")]

    def test_two_custom_fields_cannot_share_a_key(self, tmp_path: Path) -> None:
        fields = [
            {"source_ref": "cf1", "name": "Severity", "key": "severity", "field_format": "list"},
            {"source_ref": "cf2", "name": "Severity (old)", "key": "severity", "field_format": "list"},
        ]
        assert _codes(validate_bundle(write_bundle(tmp_path / "b", custom_fields=fields))) == [
            ("custom_fields.jsonl", 2, "invalid_ref")
        ]

    def test_a_user_ref_custom_value_is_checked(self, tmp_path: Path) -> None:
        field = {"source_ref": "cf1", "name": "Reviewer", "key": "reviewer", "field_format": "user"}
        value = {"field_ref": "cf1", "key": "reviewer", "value": "ghost", "value_kind": "user_ref"}
        root = write_bundle(
            tmp_path / "b", projects=[project("p1")], custom_fields=[field], issues=[issue("i1", custom_values=[value])]
        )
        assert _codes(validate_bundle(root)) == [("issues.jsonl", 1, "unresolved_ref")]

    def test_a_version_ref_custom_value_must_be_a_string(self, tmp_path: Path) -> None:
        field = {"source_ref": "cf1", "name": "Found in", "key": "found_in", "field_format": "version"}
        value = {"field_ref": "cf1", "key": "found_in", "value": [7], "value_kind": "version_ref"}
        root = write_bundle(
            tmp_path / "b", projects=[project("p1")], custom_fields=[field], issues=[issue("i1", custom_values=[value])]
        )
        assert _codes(validate_bundle(root)) == [("issues.jsonl", 1, "invalid_ref")]

    @pytest.mark.parametrize(
        ("stem", "records"),
        [
            ("projects", [project("a", parent_ref="b"), project("b", parent_ref="a")]),
            ("issues", [issue("a", parent_ref="b"), issue("b", parent_ref="a")]),
        ],
    )
    def test_a_parent_cycle(self, tmp_path: Path, stem: str, records: list[dict[str, Any]]) -> None:
        records_by_file: dict[str, Any] = {stem: records}
        if stem != "projects":
            records_by_file["projects"] = [project("p1")]
        root = write_bundle(tmp_path / "b", **records_by_file)
        assert _codes(validate_bundle(root)) == [(f"{stem}.jsonl", None, "parent_cycle")]

    def test_an_issue_cannot_relate_to_itself(self, tmp_path: Path) -> None:
        relation = {"source_ref": "r1", "from_ref": "i1", "to_ref": "i1", "relation_type": "relates"}
        root = write_bundle(tmp_path / "b", projects=[project("p1")], issues=[issue("i1")], relations=[relation])
        assert _codes(validate_bundle(root)) == [("relations.jsonl", 1, "invalid_ref")]

    def test_wiki_revisions_must_be_oldest_first(self, tmp_path: Path) -> None:
        page = {
            "source_ref": "w1",
            "project_ref": "p1",
            "title": "Home",
            "versions": [
                {"source_ref": "v2", "version": 2, "text": "new"},
                {"source_ref": "v1", "version": 1, "text": "old"},
            ],
        }
        root = write_bundle(tmp_path / "b", projects=[project("p1")], wiki_pages=[page])
        assert _codes(validate_bundle(root)) == [("wiki_pages.jsonl", 1, "schema")]

    def test_a_wiki_parent_in_another_project(self, tmp_path: Path) -> None:
        pages = [
            {"source_ref": "w1", "project_ref": "p1", "title": "Home"},
            {"source_ref": "w2", "project_ref": "p2", "title": "Child", "parent_ref": "w1"},
        ]
        root = write_bundle(tmp_path / "b", projects=[project("p1"), project("p2")], wiki_pages=pages)
        assert _codes(validate_bundle(root)) == [("wiki_pages.jsonl", 2, "invalid_ref")]

    def test_a_redirect_listed_twice(self, tmp_path: Path) -> None:
        redirect = {"project_ref": "p1", "title": "Old", "redirects_to": "New"}
        root = write_bundle(tmp_path / "b", projects=[project("p1")], wiki_redirects=[redirect, redirect])
        assert _codes(validate_bundle(root)) == [("wiki_redirects.jsonl", 2, "duplicate_ref")]

    def test_a_watcher_listed_twice_is_a_warning(self, tmp_path: Path) -> None:
        watcher = {"container_kind": "issue", "container_ref": "i1", "user_ref": "u1"}
        root = write_bundle(
            tmp_path / "b",
            users=[user("u1")],
            projects=[project("p1")],
            issues=[issue("i1")],
            watchers=[watcher, watcher],
        )
        report = validate_bundle(root)
        assert report.ok
        assert _codes(report, Severity.WARNING) == [("watchers.jsonl", 2, "duplicate_ref")]

    def test_public_access_names_projects(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b")
        (root / "public_access.json").write_text(json.dumps({"anonymous_permissions": {"p9": ["view_issues"]}}))
        report = validate_bundle(root)
        assert [(p.file, p.pointer, str(p.code)) for p in report.errors] == [
            ("public_access.json", "/anonymous_permissions/p9", "unresolved_ref")
        ]


class TestFiles:
    def _attachment(self, sha256: str, **fields: object) -> dict[str, object]:
        return {
            "source_ref": "a1",
            "container_kind": "issue",
            "container_ref": "i1",
            "filename": "log.txt",
            "sha256": sha256,
            **fields,
        }

    def _bundle(self, root: Path, attachment: dict[str, object], files: dict[str, bytes] | None) -> Path:
        return write_bundle(root, projects=[project("p1")], issues=[issue("i1")], attachments=[attachment], files=files)

    def test_a_missing_file(self, tmp_path: Path) -> None:
        root = self._bundle(tmp_path / "b", self._attachment(stored(b"x")), None)
        assert _codes(validate_bundle(root)) == [("attachments.jsonl", 1, "missing_file")]

    def test_a_file_whose_bytes_do_not_match_its_name(self, tmp_path: Path) -> None:
        root = self._bundle(tmp_path / "b", self._attachment(stored(b"x")), {stored(b"x"): b"tampered"})
        assert _codes(validate_bundle(root)) == [("attachments.jsonl", 1, "hash_mismatch")]

    def test_a_wrong_size_is_only_a_warning(self, tmp_path: Path) -> None:
        root = self._bundle(tmp_path / "b", self._attachment(stored(b"x"), filesize=99), {stored(b"x"): b"x"})
        report = validate_bundle(root)
        assert report.ok
        assert _codes(report, Severity.WARNING) == [("attachments.jsonl", 1, "size_mismatch")]

    def test_an_unreferenced_file_is_a_warning(self, tmp_path: Path) -> None:
        files = {stored(b"x"): b"x", stored(b"y"): b"y"}
        report = validate_bundle(self._bundle(tmp_path / "b", self._attachment(stored(b"x")), files))
        assert report.ok
        assert _codes(report, Severity.WARNING) == [(f"files/{stored(b'y')}", None, "unreferenced_file")]

    def test_a_symlink_in_files_is_refused(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_bytes(b"server secret")
        root = self._bundle(tmp_path / "b", self._attachment(stored(b"server secret")), None)
        (root / "files").mkdir()
        os.symlink(secret, root / "files" / stored(b"server secret"))
        report = validate_bundle(root)
        assert _codes(report) == [(f"files/{stored(b'server secret')}", None, "symlink")]
        # Refused outright: no record was read.
        assert report.records == {}

    def test_a_symlinked_record_file_is_refused(self, tmp_path: Path) -> None:
        elsewhere = tmp_path / "users.jsonl"
        elsewhere.write_text(json.dumps(user("u1")) + "\n")
        root = write_bundle(tmp_path / "b")
        os.symlink(elsewhere, root / "users.jsonl")
        assert _codes(validate_bundle(root)) == [("users.jsonl", None, "symlink")]

    def test_unknown_files_are_ignored_with_a_warning(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b")
        (root / "README.txt").write_text("hello")
        (root / "files").mkdir()
        (root / "files" / "notes.txt").write_text("hello")
        report = validate_bundle(root)
        assert report.ok
        assert _codes(report, Severity.WARNING) == [
            ("README.txt", None, "unknown_file"),
            ("files/notes.txt", None, "unknown_file"),
        ]


class TestEntriesThatAreNotRegularFiles:
    """A pipe, device, socket or directory under a name the reader opens refuses the bundle unopened.

    Opening a FIFO for reading blocks until something writes to it, so a
    bundle could otherwise hang the validator; each case must end within 5 s.
    """

    @pytest.mark.parametrize("name", ["issues.jsonl", "manifest.json", "lookups.json"])
    def test_a_fifo_under_a_known_name(self, tmp_path: Path, name: str) -> None:
        root = write_bundle(tmp_path / "b")
        (root / name).unlink(missing_ok=True)
        os.mkfifo(root / name)
        report = within_seconds(5, lambda: validate_bundle(root), root / name)
        assert _codes(report) == [(name, None, "unsafe_entry")]
        assert report.records == {}

    def test_a_directory_under_a_known_name(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b")
        (root / "users.jsonl").mkdir()
        report = within_seconds(5, lambda: validate_bundle(root))
        assert _codes(report) == [("users.jsonl", None, "unsafe_entry")]
        assert report.records == {}

    def test_a_fifo_in_files(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", files={stored(b"x"): b"x"})
        fifo = root / "files" / stored(b"y")
        os.mkfifo(fifo)
        report = within_seconds(5, lambda: validate_bundle(root), fifo)
        assert _codes(report) == [(f"files/{stored(b'y')}", None, "unsafe_entry")]
        assert report.records == {}

    def test_a_fifo_in_a_separate_files_directory(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b")
        files = tmp_path / "store"
        files.mkdir()
        fifo = files / stored(b"x")
        os.mkfifo(fifo)
        report = within_seconds(5, lambda: validate_bundle(root, files_path=files), fifo)
        assert _codes(report) == [(f"files/{stored(b'x')}", None, "unsafe_entry")]
        assert report.records == {}

    def test_a_fifo_with_an_unknown_name_is_only_ignored(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b")
        os.mkfifo(root / "notes")
        report = within_seconds(5, lambda: validate_bundle(root), root / "notes")
        assert report.ok
        assert _codes(report, Severity.WARNING) == [("notes", None, "unknown_file")]

    def test_a_record_file_swapped_for_a_fifo_after_the_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = write_bundle(tmp_path / "b", users=[user("u1")])
        check = container_module._check_directory

        def check_then_swap(directory: Path, report: ValidationReport) -> bool:
            safe = check(directory, report)
            (directory / "users.jsonl").unlink()
            os.mkfifo(directory / "users.jsonl")
            return safe

        monkeypatch.setattr(container_module, "_check_directory", check_then_swap)
        report = within_seconds(5, lambda: validate_bundle(root), root / "users.jsonl")
        assert _codes(report) == [("users.jsonl", None, "unreadable")]


class TestOpenRegular:
    def test_a_regular_file_is_read_in_blocking_mode(self, tmp_path: Path) -> None:
        path = tmp_path / "data"
        path.write_bytes(b"data")
        with container_module.open_regular(path) as handle:
            assert os.get_blocking(handle.fileno())
            assert handle.read() == b"data"

    def test_a_fifo_is_refused_without_blocking(self, tmp_path: Path) -> None:
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)

        def open_it() -> None:
            with pytest.raises(OSError, match="Not a regular file"):
                container_module.open_regular(fifo)

        within_seconds(5, open_it, fifo)

    def test_a_directory_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(OSError, match="Not a regular file"):
            container_module.open_regular(tmp_path)

    def test_a_symlink_is_not_followed(self, tmp_path: Path) -> None:
        (tmp_path / "target").write_bytes(b"secret")
        os.symlink(tmp_path / "target", tmp_path / "link")
        with pytest.raises(OSError):
            container_module.open_regular(tmp_path / "link")


SECRET_HASH = "$2y$10$Q9mWkzVtH3secretvaluexyzAbCdEfGhIjKlMnOpQrStUvWxYz012"


class TestSecretsStayOutOfTheReport:
    """A credential that fails its schema is reported without its value: the report is printed and logged."""

    @pytest.mark.parametrize(
        "credential",
        [
            {"algorithm": "", "fields": {"hash": SECRET_HASH}},
            {"algorithm": "bcrypt", "fields": {"hash": [SECRET_HASH]}},
            {"algorithm": "bcrypt", "fields": {"hash": "x"}, "salt": SECRET_HASH},
            SECRET_HASH,
        ],
        ids=["empty-algorithm", "hash-not-a-string", "unknown-property", "not-an-object"],
    )
    def test_a_bad_credential(self, tmp_path: Path, credential: Any) -> None:
        root = write_bundle(tmp_path / "b", users=[user("u1", credential=credential)])
        report = validate_bundle(root)
        assert _codes(report) == [("users.jsonl", 1, "schema")]
        assert [p.message for p in report.problems] == ["/credential: does not match the schema (anyOf)"]
        assert SECRET_HASH not in report.format_text()
        assert SECRET_HASH not in json.dumps(report.as_dict())

    def test_other_values_are_still_shown(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", users=[user("u1", status="gone")])
        [problem] = validate_bundle(root).problems
        assert "'gone'" in problem.message


class TestZipSafety:
    def _entries(self, tmp_path: Path) -> dict[str, bytes]:
        return bundle_entries(write_bundle(tmp_path / "src", users=[user("u1")]))

    @pytest.mark.parametrize(
        "name",
        ["../escape.txt", "files/../../escape.txt", "/etc/cron.d/escape", "C:/escape.txt", "files\\..\\escape.txt"],
    )
    def test_a_path_leaving_the_bundle_refuses_the_archive(self, tmp_path: Path, name: str) -> None:
        archive = make_zip(tmp_path / "b.zip", {**self._entries(tmp_path), name: b"payload"})
        report = validate_bundle(archive)
        assert _codes(report) == [(name, None, "unsafe_entry")]
        assert report.records == {}
        assert not (tmp_path / "escape.txt").exists()

    def test_a_symlink_entry_refuses_the_archive(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "b.zip", self._entries(tmp_path), links={"files/" + "a" * 64: "/etc/passwd"})
        report = validate_bundle(archive)
        assert _codes(report) == [("files/" + "a" * 64, None, "symlink")]
        assert report.records == {}

    def test_a_duplicate_entry_refuses_the_archive(self, tmp_path: Path) -> None:
        archive = tmp_path / "b.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            for name, data in self._entries(tmp_path).items():
                handle.writestr(name, data)
            with pytest.warns(UserWarning):
                handle.writestr("users.jsonl", json.dumps(user("evil")) + "\n")
        assert ("users.jsonl", None, "unsafe_entry") in _codes(validate_bundle(archive))

    def test_an_encrypted_entry_refuses_the_archive(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "b.zip", self._entries(tmp_path))
        data = bytearray(archive.read_bytes())
        # Set the "encrypted" flag on the first entry's local and central headers.
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            position = data.index(signature) + offset
            data[position] |= 0x1
        archive.write_bytes(bytes(data))
        assert [code for _, _, code in _codes(validate_bundle(archive))] == ["unsafe_entry"]

    def test_the_unpacked_copy_is_removed(self, tmp_path: Path, private_tmp: Path) -> None:
        report = validate_bundle(make_zip(tmp_path / "b.zip", _small_bundle()))
        assert report.ok
        assert list(private_tmp.iterdir()) == []


class TestZipLimits:
    def test_a_zip_bomb_is_refused_before_anything_is_unpacked(self, tmp_path: Path, private_tmp: Path) -> None:
        bomb = make_zip(tmp_path / "bomb.zip", _small_bundle(**{"files/" + "0" * 64: b"\0" * (8 * MIB)}))
        report = validate_bundle(bomb)
        assert _codes(report) == [("files/" + "0" * 64, None, "too_large")]
        assert "zip bomb" in report.problems[0].message
        assert list(private_tmp.iterdir()) == []

    def test_bytes_are_counted_while_unpacking_not_taken_from_the_headers(
        self, tmp_path: Path, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An archive can understate its sizes; what is written is what counts."""
        monkeypatch.setattr(container_module, "_check_declared_sizes", lambda *args: True)
        bomb = make_zip(tmp_path / "bomb.zip", _small_bundle(**{"files/" + "0" * 64: b"\0" * (8 * MIB)}))
        report = validate_bundle(bomb)
        assert _codes(report) == [("files/" + "0" * 64, None, "too_large")]
        assert list(private_tmp.iterdir()) == []

    def test_too_many_entries(self, tmp_path: Path, private_tmp: Path) -> None:
        archive = make_zip(tmp_path / "b.zip", _small_bundle(**{f"files/{i:064x}": b"x" for i in range(5)}))
        report = validate_bundle(archive, limits=BundleLimits(max_entries=4))
        assert _codes(report) == [("b.zip", None, "too_large")]

    def test_too_much_in_total(self, tmp_path: Path, private_tmp: Path) -> None:
        entries = _small_bundle(**{f"files/{i:064x}": bytes(range(256)) * 40 for i in range(3)})
        archive = make_zip(tmp_path / "b.zip", entries, compression=zipfile.ZIP_STORED)
        report = validate_bundle(archive, limits=BundleLimits(max_unpacked_bytes=20_000))
        assert _codes(report) == [("b.zip", None, "too_large")]

    def test_too_much_in_total_counted_while_unpacking(
        self, tmp_path: Path, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(container_module, "_check_declared_sizes", lambda *args: True)
        entries = _small_bundle(**{f"files/{i:064x}": bytes(range(256)) * 40 for i in range(3)})
        archive = make_zip(tmp_path / "b.zip", entries, compression=zipfile.ZIP_STORED)
        report = validate_bundle(archive, limits=BundleLimits(max_unpacked_bytes=20_000))
        assert [code for _, _, code in _codes(report)] == ["too_large"]
        assert list(private_tmp.iterdir()) == []

    def test_one_entry_too_large(self, tmp_path: Path, private_tmp: Path) -> None:
        archive = make_zip(tmp_path / "b.zip", _small_bundle(**{"files/" + "1" * 64: bytes(range(256)) * 8}))
        report = validate_bundle(archive, limits=BundleLimits(max_entry_bytes=1024))
        assert _codes(report) == [("files/" + "1" * 64, None, "too_large")]


class TestRecordLimits:
    def test_an_oversized_line_is_reported_and_the_rest_still_read(self, tmp_path: Path) -> None:
        lines = [json.dumps(user("u1")), json.dumps(user("u2", display_name="x" * 5000)), json.dumps(user("u3"))]
        root = write_bundle(tmp_path / "b", users="\n".join(lines) + "\n")
        report = validate_bundle(root, limits=BundleLimits(max_line_bytes=1000))
        assert _codes(report) == [("users.jsonl", 2, "too_large")]
        # The lines either side are read normally, at their own line numbers.
        assert report.records["users"] == 2

    def test_an_oversized_line_without_a_newline(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", users=json.dumps(user("u1", display_name="x" * 5000)))
        report = validate_bundle(root, limits=BundleLimits(max_line_bytes=1000))
        assert _codes(report) == [("users.jsonl", 1, "too_large")]

    def test_an_oversized_json_file(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b")
        (root / "lookups.json").write_text(json.dumps({"trackers": [], "pad": "x" * 5000}))
        report = validate_bundle(root, limits=BundleLimits(max_json_bytes=1000))
        assert _codes(report) == [("lookups.json", None, "too_large")]


class TestCounts:
    def test_a_count_that_does_not_match(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", manifest_data=manifest(counts={"users": 3}), users=[user("u1")])
        report = validate_bundle(root)
        assert [(p.file, p.pointer, str(p.code)) for p in report.errors] == [
            ("manifest.json", "/counts/users", "count_mismatch")
        ]

    def test_a_count_for_an_absent_file_must_be_zero(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", manifest_data=manifest(counts={"issues": 0, "users": 0}))
        assert validate_bundle(root).ok


class TestFailures:
    def test_an_unexpected_error_becomes_a_report_and_the_copy_is_removed(
        self, tmp_path: Path, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(self: object) -> None:
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(validate_module._Scanner, "run", explode)
        report = validate_bundle(make_zip(tmp_path / "b.zip", _small_bundle()))
        assert _codes(report) == [("b.zip", None, "unreadable")]
        assert "disk on fire" in report.problems[0].message
        assert list(private_tmp.iterdir()) == []


class TestStats:
    def test_what_the_bundle_holds(self, full: Path) -> None:
        stats = inspect_bundle(full)
        assert stats.report.ok
        assert stats.manifest["source"]["instance"] == "tracker.example.com"
        assert stats.extra == {"issues": 1}
        # Three attachments, two distinct contents.
        assert stats.stored_files == 2


class TestReviewedGaps:
    def test_a_source_ref_ending_in_a_newline(self, tmp_path: Path) -> None:
        root = write_bundle(tmp_path / "b", users=[user("u1\n")])
        report = validate_bundle(root)
        assert _codes(report) == [("users.jsonl", 1, "schema")]
        assert "ends in a newline" in report.problems[0].message

    def test_a_lookup_ref_ending_in_a_newline(self, tmp_path: Path) -> None:
        lookups = {"priorities": [{"source_ref": "n\n", "name": "Normal"}]}
        report = validate_bundle(write_bundle(tmp_path / "b", lookups=lookups))
        assert [(p.file, p.pointer, str(p.code)) for p in report.errors] == [
            ("lookups.json", "/priorities/0", "schema")
        ]

    def test_placeholders_in_a_description_change_are_checked(self, tmp_path: Path) -> None:
        journal = {
            "source_ref": "j1",
            "issue_ref": "i1",
            "details": [
                {
                    "property": "attr",
                    "prop_key": "description",
                    "old_value": "{{issue:i1}}",
                    "new_value": "{{user:ghost}}",
                },
                # Any other field's values are stored as written.
                {"property": "attr", "prop_key": "subject", "new_value": "{{user:ghost}}"},
            ],
        }
        root = write_bundle(tmp_path / "b", projects=[project("p1")], issues=[issue("i1")], journals=[journal])
        report = validate_bundle(root)
        assert _codes(report) == [("journals.jsonl", 1, "unresolved_ref")]
        assert report.problems[0].message.startswith("details.new_value {{user:…}}: no user")
