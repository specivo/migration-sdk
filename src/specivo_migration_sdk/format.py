"""Constants of import bundle format version 1.

The format is specified in ``docs/import-bundle/v1.md`` of the Specivo
repository; this module is the part of it the code needs: the version, the
file names, the entity types and which model each record file carries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from specivo_migration_sdk import models

FORMAT_NAME = "specivo-import-bundle"
FORMAT_MAJOR = 1
FORMAT_MINOR = 0
FORMAT_VERSION = f"{FORMAT_MAJOR}.{FORMAT_MINOR}"

MANIFEST = "manifest.json"
LOOKUPS = "lookups.json"
PUBLIC_ACCESS = "public_access.json"
FILES_DIR = "files"

# A stored file is named after the SHA-256 of its bytes, lower-case hex.
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Properties whose values are secret (a user's password hash). A schema error
# at or below one is reported without the value, which jsonschema would quote.
SECRET_PROPERTIES: frozenset[str] = frozenset({"credential"})


class EntityType(StrEnum):
    """The namespaces ``source_ref`` values are unique in."""

    TRACKER = "tracker"
    STATUS = "status"
    PRIORITY = "priority"
    ACTIVITY = "activity"
    ROLE = "role"
    USER = "user"
    GROUP = "group"
    PROJECT = "project"
    MEMBERSHIP = "membership"
    VERSION = "version"
    CATEGORY = "category"
    CUSTOM_FIELD = "custom_field"
    ISSUE = "issue"
    JOURNAL = "journal"
    RELATION = "relation"
    WIKI_PAGE = "wiki_page"
    WIKI_VERSION = "wiki_version"
    ATTACHMENT = "attachment"
    TIME_ENTRY = "time_entry"


# Issue attributes whose history values name a record, and the entity type of
# that record: a journal detail changing one of them gives the records as
# ``old_ref`` / ``new_ref``, with the source's text for them in
# ``old_value`` / ``new_value``.
REFERENCE_ATTRIBUTES: dict[str, EntityType] = {
    "project_id": EntityType.PROJECT,
    "tracker_id": EntityType.TRACKER,
    "status_id": EntityType.STATUS,
    "priority_id": EntityType.PRIORITY,
    "assigned_to_id": EntityType.USER,
    "category_id": EntityType.CATEGORY,
    "fixed_version_id": EntityType.VERSION,
    "parent_id": EntityType.ISSUE,
}


@dataclass(frozen=True, slots=True)
class RecordFile:
    """One JSON-lines file of the bundle.

    ``entity_type`` is the namespace its ``source_ref`` values are unique in,
    and ``None`` for the two record types that have no ``source_ref``
    (watchers and wiki redirects, which are identified by what they join).
    """

    name: str
    model: type[models.Record]
    entity_type: EntityType | None

    @property
    def stem(self) -> str:
        """The file name without ``.jsonl``: the key used in the manifest's counts."""
        return self.name.removesuffix(".jsonl")

    @property
    def schema(self) -> str:
        return f"{self.stem}.schema.json"


USERS = RecordFile("users.jsonl", models.User, EntityType.USER)
GROUPS = RecordFile("groups.jsonl", models.Group, EntityType.GROUP)
PROJECTS = RecordFile("projects.jsonl", models.Project, EntityType.PROJECT)
MEMBERSHIPS = RecordFile("memberships.jsonl", models.Membership, EntityType.MEMBERSHIP)
VERSIONS = RecordFile("versions.jsonl", models.Version, EntityType.VERSION)
CATEGORIES = RecordFile("categories.jsonl", models.Category, EntityType.CATEGORY)
CUSTOM_FIELDS = RecordFile("custom_fields.jsonl", models.CustomField, EntityType.CUSTOM_FIELD)
ISSUES = RecordFile("issues.jsonl", models.Issue, EntityType.ISSUE)
JOURNALS = RecordFile("journals.jsonl", models.Journal, EntityType.JOURNAL)
RELATIONS = RecordFile("relations.jsonl", models.Relation, EntityType.RELATION)
WIKI_PAGES = RecordFile("wiki_pages.jsonl", models.WikiPage, EntityType.WIKI_PAGE)
WIKI_REDIRECTS = RecordFile("wiki_redirects.jsonl", models.WikiRedirect, None)
WATCHERS = RecordFile("watchers.jsonl", models.Watcher, None)
ATTACHMENTS = RecordFile("attachments.jsonl", models.Attachment, EntityType.ATTACHMENT)
TIME_ENTRIES = RecordFile("time_entries.jsonl", models.TimeEntry, EntityType.TIME_ENTRY)

# Every record file, in the order the validator checks them: each file refers
# only to files before it, except where a record points at another of its own
# kind (a parent) or text names a record by placeholder.
RECORD_FILES: tuple[RecordFile, ...] = (
    USERS,
    GROUPS,
    PROJECTS,
    MEMBERSHIPS,
    VERSIONS,
    CATEGORIES,
    CUSTOM_FIELDS,
    ISSUES,
    JOURNALS,
    RELATIONS,
    WIKI_PAGES,
    WIKI_REDIRECTS,
    WATCHERS,
    ATTACHMENTS,
    TIME_ENTRIES,
)


@dataclass(frozen=True, slots=True)
class LookupList:
    """One list in ``lookups.json``."""

    name: str
    model: type[models.Record]
    entity_type: EntityType


LOOKUP_LISTS: tuple[LookupList, ...] = (
    LookupList("trackers", models.Tracker, EntityType.TRACKER),
    LookupList("statuses", models.Status, EntityType.STATUS),
    LookupList("priorities", models.Priority, EntityType.PRIORITY),
    LookupList("activities", models.Activity, EntityType.ACTIVITY),
    LookupList("roles", models.Role, EntityType.ROLE),
)

# Files a bundle may contain at its top level besides files/.
KNOWN_FILES: frozenset[str] = frozenset({MANIFEST, LOOKUPS, PUBLIC_ACCESS, *(f.name for f in RECORD_FILES)})

# Every key of the manifest's counts, in the order they are written.
COUNT_KEYS: tuple[str, ...] = (
    *(lookup.name for lookup in LOOKUP_LISTS),
    *(record_file.stem for record_file in RECORD_FILES),
    "wiki_versions",
)
