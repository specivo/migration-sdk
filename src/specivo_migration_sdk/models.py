"""Typed records of import bundle format version 1.

One dataclass per record type, field for field the properties of its JSON
Schema (``schemas/v1/``); a test keeps the two in step. Every field is a
keyword argument. A field left at its default is not written, which the format
treats the same as writing the default.

Values are plain Python: ``datetime`` for timestamps, ``date`` for dates,
``Decimal`` for decimals, strings for enumerations. A timestamp or a date may
also be given as a string, which is normalised when the record is written (see
:func:`specivo_migration_sdk.helpers.timestamp`); a timestamp without a UTC offset
is refused rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from specivo_migration_sdk.encode import to_record
from specivo_migration_sdk.helpers import DateValue, DecimalValue, Timestamp

type StatusCategory = Literal["backlog", "active", "done", "closed"]
type UserStatus = Literal["active", "locked", "pending_verification", "deactivated"]
type ProjectStatus = Literal[1, 5, 9]
type ProjectModule = Literal["issue_tracking", "wiki", "time_tracking"]
type PrincipalKind = Literal["user", "group"]
type VersionStatus = Literal["open", "locked", "closed"]
type VersionSharing = Literal["none", "descendants", "hierarchy", "tree", "system"]
type ValueKind = Literal["scalar", "user_ref", "version_ref"]
type DetailProperty = Literal["attr", "cf", "attachment", "relation"]
type RelationType = Literal["relates", "duplicates", "blocks", "precedes", "copied_to"]
type ContainerKind = Literal["issue", "wiki_page"]
type RoleBuiltin = Literal[0, 1, 2]
type UsersVisibility = Literal["all", "members_of_visible_projects"]
type TimeEntriesVisibility = Literal["all", "own"]
type AnonymousPermission = Literal["view_issues", "view_wiki"]


@dataclass(kw_only=True, slots=True)
class Record:
    """Base of every record: turns itself into the JSON object written to the bundle."""

    def to_dict(self) -> dict[str, Any]:
        """Return the record as written to the bundle.

        Raises :class:`ValueError` for a value the format cannot hold, such as
        a timestamp without a UTC offset.
        """
        return to_record(self)


# --------------------------------------------------------------------------
# lookups.json
# --------------------------------------------------------------------------


@dataclass(kw_only=True, slots=True)
class Tracker(Record):
    """An issue type."""

    source_ref: str
    name: str
    description: str | None = None
    is_in_roadmap: bool = True
    default_status_ref: str | None = None
    disabled_core_fields: list[str] = field(default_factory=list)
    position: int = 0
    private_by_default: bool = False
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Status(Record):
    source_ref: str
    name: str
    category: StatusCategory
    position: int = 0
    default_done_ratio: int | None = None
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Priority(Record):
    source_ref: str
    name: str
    position: int = 0
    is_default: bool = False
    active: bool = True
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Activity(Record):
    """A time tracking activity."""

    source_ref: str
    name: str
    is_default: bool = False
    active: bool = True
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Role(Record):
    source_ref: str
    name: str
    builtin: RoleBuiltin = 0
    permissions: list[str] = field(default_factory=list)
    users_visibility: UsersVisibility | None = None
    time_entries_visibility: TimeEntriesVisibility | None = None
    extra: dict[str, Any] | None = None


# --------------------------------------------------------------------------
# Record files
# --------------------------------------------------------------------------


@dataclass(kw_only=True, slots=True)
class Credential(Record):
    """A user's stored password, as the source kept it. Secret."""

    algorithm: str
    fields: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        # Never show the fields: a repr ends up in logs and tracebacks.
        return f"Credential(algorithm={self.algorithm!r}, fields=<{len(self.fields)} hidden>)"


@dataclass(kw_only=True, slots=True)
class User(Record):
    source_ref: str
    login: str
    display_name: str
    email: str | None = None
    status: UserStatus = "active"
    is_admin: bool = False
    language: str | None = None
    last_login_at: Timestamp | None = None
    created_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    credential: Credential | None = None
    must_change_password: bool = False
    password_changed_at: Timestamp | None = None
    external_auth: bool = False
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Group(Record):
    source_ref: str
    name: str
    member_refs: list[str] = field(default_factory=list)
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Project(Record):
    source_ref: str
    identifier: str
    name: str
    description: str | None = None
    is_public: bool = False
    parent_ref: str | None = None
    status: ProjectStatus = 1
    # None leaves Specivo's default set of modules.
    modules: list[ProjectModule] | None = None
    created_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Membership(Record):
    """One role grant on a project, to a user or a group."""

    source_ref: str
    project_ref: str
    principal_kind: PrincipalKind
    principal_ref: str
    role_refs: list[str] = field(default_factory=list)
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Version(Record):
    """A release or milestone of a project."""

    source_ref: str
    project_ref: str
    name: str
    description: str | None = None
    status: VersionStatus = "open"
    effective_date: DateValue | None = None
    sharing: VersionSharing = "none"
    wiki_page_title: str | None = None
    created_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Category(Record):
    """An issue category (component)."""

    source_ref: str
    project_ref: str
    name: str
    assigned_to_ref: str | None = None
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class CustomField(Record):
    source_ref: str
    name: str
    key: str
    field_format: str
    json_schema: dict[str, Any] = field(default_factory=dict)
    is_required: bool = False
    multiple: bool = False
    is_for_all: bool = False
    tracker_refs: list[str] = field(default_factory=list)
    project_refs: list[str] = field(default_factory=list)
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class CustomValue(Record):
    """One custom field value of an issue.

    For ``user_ref`` and ``version_ref`` the value is the ``source_ref`` of the
    user or version, or a list of them.
    """

    field_ref: str
    key: str
    value: Any
    value_kind: ValueKind = "scalar"


@dataclass(kw_only=True, slots=True)
class Issue(Record):
    source_ref: str
    project_ref: str
    tracker_ref: str
    status_ref: str
    priority_ref: str
    subject: str
    author_ref: str | None = None
    assigned_to_ref: str | None = None
    description: str | None = None
    parent_ref: str | None = None
    category_ref: str | None = None
    fixed_version_ref: str | None = None
    start_date: DateValue | None = None
    due_date: DateValue | None = None
    estimated_hours: DecimalValue | None = None
    remaining_estimate: DecimalValue | None = None
    done_ratio: int = 0
    is_private: bool = False
    custom_values: list[CustomValue] = field(default_factory=list)
    created_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    closed_at: Timestamp | None = None
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class JournalDetail(Record):
    """One field change in a journal entry.

    A change of an attribute that names a record (``status_id``,
    ``fixed_version_id``, …, see :data:`~specivo_migration_sdk.format.REFERENCE_ATTRIBUTES`)
    gives the records' ``source_ref`` as ``old_ref`` / ``new_ref``, never the
    source's ids as values; ``old_value`` / ``new_value`` are then the source's
    text for them (a version's name, an issue's key), which Specivo shows when a
    ref names nothing that was imported.
    """

    property: DetailProperty
    prop_key: str
    old_value: str | None = None
    new_value: str | None = None
    old_ref: str | None = None
    new_ref: str | None = None


@dataclass(kw_only=True, slots=True)
class Journal(Record):
    """An issue history entry: a comment, field changes, or both."""

    source_ref: str
    issue_ref: str
    user_ref: str | None = None
    notes: str | None = None
    is_private: bool = False
    created_at: Timestamp | None = None
    details: list[JournalDetail] = field(default_factory=list)
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Relation(Record):
    """A relation between two issues, in its canonical direction."""

    source_ref: str
    from_ref: str
    to_ref: str
    relation_type: RelationType
    delay: int | None = None
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class WikiVersion(Record):
    """One revision of a wiki page."""

    source_ref: str
    version: int
    text: str
    comments: str | None = None
    author_ref: str | None = None
    created_at: Timestamp | None = None


@dataclass(kw_only=True, slots=True)
class WikiPage(Record):
    """A wiki page with its whole history, oldest revision first."""

    source_ref: str
    project_ref: str
    title: str
    parent_ref: str | None = None
    protected: bool = False
    versions: list[WikiVersion] = field(default_factory=list)
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class WikiRedirect(Record):
    """A title a wiki page had before it was renamed."""

    project_ref: str
    title: str
    redirects_to: str
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Watcher(Record):
    container_kind: ContainerKind
    container_ref: str
    user_ref: str
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Attachment(Record):
    """A file attached to an issue or a wiki page. Its bytes are ``files/<sha256>``."""

    source_ref: str
    container_kind: ContainerKind
    container_ref: str
    filename: str
    sha256: str
    content_type: str | None = None
    filesize: int | None = None
    description: str | None = None
    author_ref: str | None = None
    created_at: Timestamp | None = None
    extra: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class TimeEntry(Record):
    source_ref: str
    project_ref: str
    hours: DecimalValue
    spent_on: DateValue
    issue_ref: str | None = None
    user_ref: str | None = None
    activity_ref: str | None = None
    comments: str | None = None
    created_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    extra: dict[str, Any] | None = None


# --------------------------------------------------------------------------
# public_access.json and the manifest
# --------------------------------------------------------------------------


@dataclass(kw_only=True, slots=True)
class PublicAccess(Record):
    """What people without a membership could read in the source, in Specivo's terms."""

    anonymous_allowed: bool = False
    anonymous_source_setting: str = ""
    anonymous_permissions: dict[str, list[AnonymousPermission]] = field(default_factory=dict)
    non_member_source_name: str = "Non member"
    non_member_permissions: list[str] = field(default_factory=list)
    not_carried_over: list[str] = field(default_factory=list)


@dataclass(kw_only=True, slots=True)
class NotCarriedOver(Record):
    """Something the source had that the bundle does not, for the manifest."""

    what: str
    count: int | None = None
    entity_type: str | None = None
    source_ref: str | None = None


# Every record that goes in a JSON-lines file.
type LineRecord = (
    User
    | Group
    | Project
    | Membership
    | Version
    | Category
    | CustomField
    | Issue
    | Journal
    | Relation
    | WikiPage
    | WikiRedirect
    | Watcher
    | Attachment
    | TimeEntry
)
# Every record that goes in lookups.json.
type LookupRecord = Tracker | Status | Priority | Activity | Role
