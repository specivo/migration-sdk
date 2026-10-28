"""Every record type turns into the JSON object the format expects."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from specivo_migration_sdk import (
    Activity,
    Attachment,
    Category,
    Credential,
    CustomField,
    CustomValue,
    Group,
    Issue,
    Journal,
    JournalDetail,
    Membership,
    NotCarriedOver,
    Priority,
    Project,
    PublicAccess,
    Record,
    Relation,
    Role,
    Status,
    TimeEntry,
    Tracker,
    User,
    Version,
    Watcher,
    WikiPage,
    WikiRedirect,
    WikiVersion,
)
from specivo_migration_sdk.format import LOOKUP_LISTS, RECORD_FILES
from specivo_migration_sdk.validate import validator

BANGKOK = ZoneInfo("Asia/Bangkok")
WHEN = datetime(2024, 3, 1, 9, 30, tzinfo=BANGKOK)

# One record of each type with most fields set, and the JSON it must become.
CASES: list[tuple[Record, dict[str, Any]]] = [
    (
        Tracker(source_ref="1", name="Bug", default_status_ref="10", is_in_roadmap=False, position=2),
        {"source_ref": "1", "name": "Bug", "default_status_ref": "10", "is_in_roadmap": False, "position": 2},
    ),
    (
        Status(source_ref="10", name="Open", category="backlog", default_done_ratio=0),
        {"source_ref": "10", "name": "Open", "category": "backlog", "default_done_ratio": 0},
    ),
    (
        Priority(source_ref="3", name="Normal", is_default=True, active=False),
        {"source_ref": "3", "name": "Normal", "is_default": True, "active": False},
    ),
    (Activity(source_ref="9", name="Development"), {"source_ref": "9", "name": "Development"}),
    (
        Role(source_ref="4", name="Developer", permissions=["edit_issues"], users_visibility="all"),
        {"source_ref": "4", "name": "Developer", "permissions": ["edit_issues"], "users_visibility": "all"},
    ),
    (
        User(
            source_ref="u1",
            login="alice",
            display_name="Alice",
            email="alice@example.com",
            status="locked",
            is_admin=True,
            language="th",
            created_at=WHEN,
            last_login_at="2024-03-02T08:00:00.000+0700",
            credential=Credential(algorithm="sample_sha1", fields={"hash": "abc", "salt": "def"}),
            external_auth=True,
            extra={"timezone": "Asia/Bangkok"},
        ),
        {
            "source_ref": "u1",
            "login": "alice",
            "display_name": "Alice",
            "email": "alice@example.com",
            "status": "locked",
            "is_admin": True,
            "language": "th",
            "last_login_at": "2024-03-02T08:00:00+07:00",
            "created_at": "2024-03-01T09:30:00+07:00",
            "credential": {"algorithm": "sample_sha1", "fields": {"hash": "abc", "salt": "def"}},
            "external_auth": True,
            "extra": {"timezone": "Asia/Bangkok"},
        },
    ),
    (
        Group(source_ref="g1", name="Team", member_refs=["u1"]),
        {"source_ref": "g1", "name": "Team", "member_refs": ["u1"]},
    ),
    (
        Project(source_ref="p1", identifier="web", name="Website", status=5, modules=["wiki"], updated_at=WHEN),
        {
            "source_ref": "p1",
            "identifier": "web",
            "name": "Website",
            "status": 5,
            "modules": ["wiki"],
            "updated_at": "2024-03-01T09:30:00+07:00",
        },
    ),
    (
        Project(source_ref="p2", identifier="api", name="API", modules=[]),
        {"source_ref": "p2", "identifier": "api", "name": "API", "modules": []},
    ),
    (
        Membership(source_ref="m1", project_ref="p1", principal_kind="group", principal_ref="g1", role_refs=["4"]),
        {"source_ref": "m1", "project_ref": "p1", "principal_kind": "group", "principal_ref": "g1", "role_refs": ["4"]},
    ),
    (
        Version(source_ref="v1", project_ref="p1", name="1.0", effective_date=date(2024, 6, 30), sharing="tree"),
        {"source_ref": "v1", "project_ref": "p1", "name": "1.0", "effective_date": "2024-06-30", "sharing": "tree"},
    ),
    (
        Category(source_ref="c1", project_ref="p1", name="UI", assigned_to_ref="u1"),
        {"source_ref": "c1", "project_ref": "p1", "name": "UI", "assigned_to_ref": "u1"},
    ),
    (
        CustomField(source_ref="cf1", name="Reviewer", key="reviewer", field_format="user", multiple=True),
        {"source_ref": "cf1", "name": "Reviewer", "key": "reviewer", "field_format": "user", "multiple": True},
    ),
    (
        Issue(
            source_ref="i1",
            project_ref="p1",
            tracker_ref="1",
            status_ref="10",
            priority_ref="3",
            subject="Login fails",
            due_date="2024-04-01",
            estimated_hours=Decimal("1.333333"),
            done_ratio=50,
            is_private=True,
            custom_values=[
                CustomValue(field_ref="cf1", key="reviewer", value=["u1", "u2"], value_kind="user_ref"),
                CustomValue(field_ref="cf2", key="points", value=3),
            ],
            closed_at=datetime(2024, 3, 5, tzinfo=UTC),
        ),
        {
            "source_ref": "i1",
            "project_ref": "p1",
            "tracker_ref": "1",
            "status_ref": "10",
            "priority_ref": "3",
            "subject": "Login fails",
            "due_date": "2024-04-01",
            "estimated_hours": "1.333333",
            "done_ratio": 50,
            "is_private": True,
            "custom_values": [
                {"field_ref": "cf1", "key": "reviewer", "value": ["u1", "u2"], "value_kind": "user_ref"},
                {"field_ref": "cf2", "key": "points", "value": 3},
            ],
            "closed_at": "2024-03-05T00:00:00Z",
        },
    ),
    (
        Journal(
            source_ref="j1",
            issue_ref="i1",
            notes="Done.",
            created_at=WHEN,
            details=[JournalDetail(property="attr", prop_key="status_id", old_value="10", new_value="11")],
        ),
        {
            "source_ref": "j1",
            "issue_ref": "i1",
            "notes": "Done.",
            "created_at": "2024-03-01T09:30:00+07:00",
            "details": [{"property": "attr", "prop_key": "status_id", "old_value": "10", "new_value": "11"}],
        },
    ),
    (
        Relation(source_ref="r1", from_ref="i1", to_ref="i2", relation_type="precedes", delay=0),
        {"source_ref": "r1", "from_ref": "i1", "to_ref": "i2", "relation_type": "precedes", "delay": 0},
    ),
    (
        WikiPage(
            source_ref="w1",
            project_ref="p1",
            title="Home",
            protected=True,
            versions=[WikiVersion(source_ref="w1-1", version=1, text="", comments="New", created_at=WHEN)],
        ),
        {
            "source_ref": "w1",
            "project_ref": "p1",
            "title": "Home",
            "protected": True,
            "versions": [
                {
                    "source_ref": "w1-1",
                    "version": 1,
                    "text": "",
                    "comments": "New",
                    "created_at": "2024-03-01T09:30:00+07:00",
                }
            ],
        },
    ),
    (
        WikiRedirect(project_ref="p1", title="Start", redirects_to="Home"),
        {"project_ref": "p1", "title": "Start", "redirects_to": "Home"},
    ),
    (
        Watcher(container_kind="wiki_page", container_ref="w1", user_ref="u1"),
        {"container_kind": "wiki_page", "container_ref": "w1", "user_ref": "u1"},
    ),
    (
        Attachment(
            source_ref="a1",
            container_kind="issue",
            container_ref="i1",
            filename="log.txt",
            sha256="a" * 64,
            filesize=0,
            content_type="text/plain",
        ),
        {
            "source_ref": "a1",
            "container_kind": "issue",
            "container_ref": "i1",
            "filename": "log.txt",
            "sha256": "a" * 64,
            "content_type": "text/plain",
            "filesize": 0,
        },
    ),
    (
        TimeEntry(source_ref="t1", project_ref="p1", hours=1.5, spent_on=date(2024, 3, 1), comments="Review"),
        {"source_ref": "t1", "project_ref": "p1", "hours": 1.5, "spent_on": "2024-03-01", "comments": "Review"},
    ),
]


class TestEveryRecordType:
    def test_every_type_has_a_case(self) -> None:
        covered = {type(record) for record, _ in CASES}
        assert {f.model for f in RECORD_FILES} | {lookup.model for lookup in LOOKUP_LISTS} <= covered

    @pytest.mark.parametrize(("record", "expected"), CASES, ids=lambda value: type(value).__name__)
    def test_to_dict(self, record: Record, expected: dict[str, Any]) -> None:
        assert record.to_dict() == expected

    @pytest.mark.parametrize(("record", "expected"), CASES, ids=lambda value: type(value).__name__)
    def test_what_it_writes_matches_its_schema(self, record: Record, expected: dict[str, Any]) -> None:
        record_file = next((f for f in RECORD_FILES if f.model is type(record)), None)
        if record_file is not None:
            assert list(validator(record_file.schema).iter_errors(record.to_dict())) == []
        else:
            lookup = next(lookup for lookup in LOOKUP_LISTS if lookup.model is type(record))
            data = {lookup.name: [record.to_dict()]}
            assert list(validator("lookups.schema.json").iter_errors(data)) == []


class TestValues:
    def test_defaults_are_left_out(self) -> None:
        assert User(source_ref="u1", login="a", display_name="A", status="active", is_admin=False).to_dict() == {
            "source_ref": "u1",
            "login": "a",
            "display_name": "A",
        }

    def test_a_value_that_differs_from_its_default_in_type_is_kept(self) -> None:
        record = Project(source_ref="p1", identifier="x", name="X", is_public=0)  # type: ignore[arg-type]
        assert record.to_dict()["is_public"] == 0

    def test_a_naive_timestamp_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"Journal\.created_at"):
            Journal(source_ref="j1", issue_ref="i1", created_at=datetime(2024, 3, 1, 9, 30)).to_dict()

    def test_a_bad_date_names_its_field(self) -> None:
        with pytest.raises(ValueError, match=r"Version\.effective_date"):
            Version(source_ref="v1", project_ref="p1", name="1.0", effective_date="30/06/2024").to_dict()

    def test_a_decimal_is_written_exactly(self) -> None:
        entry = TimeEntry(source_ref="t1", project_ref="p1", hours=Decimal("0.1"), spent_on="2024-03-01")
        assert entry.to_dict()["hours"] == "0.1"

    def test_extra_may_hold_any_json_and_common_python_values(self) -> None:
        extra = {"when": datetime(2024, 3, 1, 9, 30), "day": date(2024, 3, 1), "cost": Decimal("1.50"), "n": [1, 2]}
        record = Group(source_ref="g1", name="Team", extra=extra)
        assert record.to_dict()["extra"] == {
            "when": "2024-03-01T09:30:00",
            "day": "2024-03-01",
            "cost": "1.50",
            "n": [1, 2],
        }

    def test_public_access_and_not_carried_over(self) -> None:
        assert PublicAccess().to_dict() == {}
        assert PublicAccess(non_member_permissions=["view_issues"]).to_dict() == {
            "non_member_permissions": ["view_issues"]
        }
        assert NotCarriedOver(what="Boards", count=0).to_dict() == {"what": "Boards", "count": 0}

    def test_a_credential_never_shows_its_fields(self) -> None:
        credential = Credential(algorithm="sample_sha1", fields={"hash": "secret-hash"})
        assert "secret-hash" not in repr(credential)
        user = User(source_ref="u1", login="a", display_name="A", credential=credential)
        assert "secret-hash" not in repr(user)
