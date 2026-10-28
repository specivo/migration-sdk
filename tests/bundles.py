"""Bundles for tests.

:func:`write_bundle` writes a small bundle by hand, from records given as
dicts, for checking one validation rule at a time without going through the
writer. :func:`write_full` uses the writer to produce a bundle with every file
of the format.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import zipfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from specivo_migration_sdk import (
    Activity,
    Attachment,
    BundleWriter,
    Category,
    CustomField,
    CustomValue,
    Group,
    Issue,
    Journal,
    JournalDetail,
    Membership,
    Priority,
    Project,
    PublicAccess,
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
    placeholders,
)

# The smallest set of lookups an issue and a time entry need.
LOOKUPS: dict[str, Any] = {
    "trackers": [{"source_ref": "bug", "name": "Bug", "default_status_ref": "open"}],
    "statuses": [{"source_ref": "open", "name": "Open", "category": "backlog"}],
    "priorities": [{"source_ref": "normal", "name": "Normal", "is_default": True}],
    "activities": [{"source_ref": "dev", "name": "Development", "is_default": True}],
}


def manifest(**overrides: Any) -> dict[str, Any]:
    """Return a valid manifest, with *overrides* applied."""
    data: dict[str, Any] = {
        "format": "specivo-import-bundle",
        "format_version": "1.0",
        "source": {"system": "sample", "instance": "tracker.example.com"},
        "generator": {"name": "pytest", "version": "1"},
        "exported_at": "2024-03-01T09:30:00Z",
    }
    data.update(overrides)
    return data


def user(ref: str = "u1", **fields: Any) -> dict[str, Any]:
    return {"source_ref": ref, "login": f"login_{ref}", "display_name": f"User {ref}", **fields}


def project(ref: str = "p1", **fields: Any) -> dict[str, Any]:
    return {"source_ref": ref, "identifier": f"proj-{ref}", "name": f"Project {ref}", **fields}


def issue(ref: str = "i1", project_ref: str = "p1", **fields: Any) -> dict[str, Any]:
    return {
        "source_ref": ref,
        "project_ref": project_ref,
        "tracker_ref": "bug",
        "status_ref": "open",
        "priority_ref": "normal",
        "subject": f"Issue {ref}",
        **fields,
    }


def write_bundle(
    root: Path,
    *,
    manifest_data: dict[str, Any] | None = None,
    lookups: dict[str, Any] | None = None,
    files: dict[str, bytes] | None = None,
    **records: list[dict[str, Any]] | str,
) -> Path:
    """Write a bundle directory at *root* and return it.

    Each keyword names a record file without ``.jsonl`` and gives its records;
    a string is written verbatim instead, for lines that are not valid JSON.
    *files* maps a name under ``files/`` to its bytes; use :func:`stored` to
    name a file by its hash.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest_data or manifest()))
    (root / "lookups.json").write_text(json.dumps(LOOKUPS if lookups is None else lookups))
    for stem, content in records.items():
        if isinstance(content, str):
            text = content
        else:
            text = "".join(json.dumps(record) + "\n" for record in content)
        (root / f"{stem}.jsonl").write_text(text)
    if files:
        (root / "files").mkdir()
        for name, data in files.items():
            (root / "files" / name).write_bytes(data)
    return root


def stored(data: bytes) -> str:
    """Return the name a file with *data* has under ``files/``."""
    return hashlib.sha256(data).hexdigest()


def bundle_entries(root: Path) -> dict[str, bytes]:
    """Every file of the bundle directory at *root*, by its path inside the bundle."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def make_zip(
    path: Path,
    entries: dict[str, bytes],
    *,
    links: dict[str, str] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> Path:
    """Write a zip with *entries*, and *links* as symbolic-link entries."""
    with zipfile.ZipFile(path, "w", compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        for name, target in (links or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, target)
    return path


SCREENSHOT = b"\x89PNG not really a picture"
LOG = b"line 1\nline 2\n"


def write_full(target: Path) -> Path:
    """Write, with the writer, a bundle that uses every file of the format; return its path."""
    with BundleWriter(
        target,
        source_system="sample",
        source_instance="tracker.example.com",
        source_version="4.2",
        generator_name="pytest",
        generator_version="1",
        exported_at=datetime(2024, 3, 1, 9, 30, tzinfo=UTC),
    ) as writer:
        writer.add_all(
            [
                Tracker(source_ref="bug", name="Bug", default_status_ref="open"),
                Tracker(source_ref="chore", name="Chore", is_in_roadmap=False, disabled_core_fields=["due_date"]),
                Status(source_ref="open", name="Open", category="backlog"),
                Status(source_ref="doing", name="In Progress", category="active", position=1),
                Status(source_ref="done", name="Done", category="closed", position=2, default_done_ratio=100),
                Priority(source_ref="normal", name="Normal", is_default=True),
                Priority(source_ref="high", name="High", position=1),
                Activity(source_ref="dev", name="Development", is_default=True),
                Role(source_ref="dev", name="Developer", permissions=["edit_issues"]),
                Role(source_ref="nm", name="Non member", builtin=1),
            ]
        )
        writer.add(User(source_ref="u1", login="alice", display_name="Alice Example", email="alice@example.com"))
        writer.add(
            User(
                source_ref="u2",
                login="bob",
                display_name="Bob Example",
                status="locked",
                language="th",
                created_at="2023-01-10T08:00:00+07:00",
            )
        )
        writer.add(Group(source_ref="g1", name="Developers", member_refs=["u1", "u2"]))
        writer.add(
            Project(
                source_ref="p1",
                identifier="web",
                name="Website",
                is_public=True,
                modules=["issue_tracking", "wiki", "time_tracking"],
                created_at="2023-01-10T08:00:00Z",
            )
        )
        writer.add(Project(source_ref="p2", identifier="web-api", name="Website API", parent_ref="p1"))
        writer.add(
            Membership(source_ref="m1", project_ref="p1", principal_kind="user", principal_ref="u1", role_refs=["dev"])
        )
        writer.add(
            Membership(source_ref="m2", project_ref="p1", principal_kind="group", principal_ref="g1", role_refs=["dev"])
        )
        writer.add(Version(source_ref="v1", project_ref="p1", name="1.0", effective_date=date(2024, 6, 30)))
        writer.add(Category(source_ref="c1", project_ref="p1", name="Frontend", assigned_to_ref="u1"))
        writer.add(
            CustomField(
                source_ref="cf1",
                name="Severity",
                key="severity",
                field_format="list",
                json_schema={"type": "string", "enum": ["Low", "High"]},
                tracker_refs=["bug"],
                project_refs=["p1"],
            )
        )
        writer.add(CustomField(source_ref="cf2", name="Reviewer", key="reviewer", field_format="user", is_for_all=True))

        screenshot = writer.add_file(SCREENSHOT)
        log = writer.add_file(LOG)
        writer.add(
            Issue(
                source_ref="i1",
                project_ref="p1",
                tracker_ref="bug",
                status_ref="doing",
                priority_ref="high",
                subject="Login fails",
                author_ref="u1",
                assigned_to_ref="u2",
                description=f"Steps are in {placeholders.attachment_image('a1', 'the screenshot')}.",
                category_ref="c1",
                fixed_version_ref="v1",
                start_date="2024-02-01",
                estimated_hours=Decimal("2.5"),
                done_ratio=30,
                custom_values=[
                    CustomValue(field_ref="cf1", key="severity", value="High"),
                    CustomValue(field_ref="cf2", key="reviewer", value="u2", value_kind="user_ref"),
                ],
                created_at=datetime(2024, 2, 1, 10, 0, tzinfo=UTC),
                extra={"source_key": "10001"},
            )
        )
        writer.add(
            Issue(
                source_ref="i2",
                project_ref="p1",
                tracker_ref="bug",
                status_ref="open",
                priority_ref="normal",
                subject="Session expires early",
                parent_ref="i1",
                description=f"Split from {placeholders.issue('i1')}.",
            )
        )
        writer.add(
            Journal(
                source_ref="j1",
                issue_ref="i1",
                user_ref="u2",
                notes=f"Reproduced, {placeholders.user('u1')}. Log: {placeholders.attachment_link('a2', 'log')}",
                created_at="2024-02-02T10:00:00Z",
                details=[JournalDetail(property="attr", prop_key="status_id", old_value="open", new_value="doing")],
            )
        )
        writer.add(Relation(source_ref="r1", from_ref="i1", to_ref="i2", relation_type="blocks"))
        writer.add(
            WikiPage(
                source_ref="w1",
                project_ref="p1",
                title="Home",
                versions=[
                    WikiVersion(source_ref="w1-1", version=1, text="Welcome.", author_ref="u1"),
                    WikiVersion(
                        source_ref="w1-2",
                        version=2,
                        text=f"Welcome. Start with {placeholders.wiki('w2', 'the guide')}.",
                        comments="Link the guide",
                        author_ref="u2",
                    ),
                ],
            )
        )
        writer.add(
            WikiPage(
                source_ref="w2",
                project_ref="p1",
                title="Guide",
                parent_ref="w1",
                versions=[WikiVersion(source_ref="w2-1", version=1, text="Read this first.")],
            )
        )
        writer.add(WikiRedirect(project_ref="p1", title="Start", redirects_to="Home"))
        writer.add(Watcher(container_kind="issue", container_ref="i1", user_ref="u2"))
        writer.add(Watcher(container_kind="wiki_page", container_ref="w1", user_ref="u1"))
        writer.add(
            Attachment(
                source_ref="a1",
                container_kind="issue",
                container_ref="i1",
                filename="screenshot.png",
                sha256=screenshot.sha256,
                filesize=screenshot.size,
                content_type="image/png",
                author_ref="u1",
            )
        )
        writer.add(
            Attachment(
                source_ref="a2",
                container_kind="issue",
                container_ref="i1",
                filename="log.txt",
                sha256=log.sha256,
                content_type="text/plain",
            )
        )
        writer.add(
            Attachment(
                source_ref="a3",
                container_kind="wiki_page",
                container_ref="w1",
                filename="screenshot copy.png",
                sha256=screenshot.sha256,
            )
        )
        writer.add(
            TimeEntry(
                source_ref="t1",
                project_ref="p1",
                issue_ref="i1",
                user_ref="u1",
                activity_ref="dev",
                hours=Decimal("1.25"),
                spent_on="2024-02-02",
            )
        )
        writer.set_public_access(
            PublicAccess(anonymous_allowed=True, anonymous_permissions={"p1": ["view_issues", "view_wiki"]})
        )
        writer.not_carried_over("Sprint boards have no equivalent", count=2)
    return target


def within_seconds[T](seconds: float, call: Callable[[], T], *fifos: Path) -> T:
    """Return ``call()``, failing the test if it takes more than *seconds*.

    A reader that opens one of *fifos* without ``O_NONBLOCK`` blocks until a
    writer appears; on a timeout each is opened for writing and closed again,
    so the stuck thread gets end-of-file and ends instead of outliving the test.
    """
    outcome: list[T] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            outcome.append(call())
        except BaseException as exc:  # noqa: BLE001 - re-raised in the test's thread
            failure.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        for fifo in fifos:
            try:
                os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            except OSError:
                pass
        thread.join(seconds)
        pytest.fail(f"Still running after {seconds} s: a non-regular file was opened and blocked")
    if failure:
        raise failure[0]
    return outcome[0]
