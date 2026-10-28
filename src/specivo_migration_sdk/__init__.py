"""Write and validate Specivo import bundles.

An import bundle is how data from another tracker gets into Specivo: a
directory or ``.zip`` holding a manifest, one JSON-lines file per kind of record
and the attachment bytes. This package writes one (:class:`BundleWriter` and
the record dataclasses) and checks one the way Specivo does before loading it
(:func:`validate_bundle`). It does not depend on Specivo.

The format is specified in ``docs/import-bundle/v1.md`` of the Specivo
repository; the JSON Schemas it ships under ``schemas/v1/`` are that
document's machine-readable half.
"""

from importlib.metadata import PackageNotFoundError, version

from specivo_migration_sdk import placeholders
from specivo_migration_sdk.format import FORMAT_NAME, FORMAT_VERSION, EntityType
from specivo_migration_sdk.helpers import date_value, decimal_value, is_source_ref, source_ref, timestamp
from specivo_migration_sdk.limits import BundleLimits
from specivo_migration_sdk.models import (
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
from specivo_migration_sdk.report import Problem, ProblemCode, Severity, ValidationReport
from specivo_migration_sdk.validate import BundleStats, inspect_bundle, validate_bundle
from specivo_migration_sdk.writer import BundleTooLargeError, BundleWriter, RecordError, StoredFile

try:
    __version__ = version("specivo-migration-sdk")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled checkout
    __version__ = "0"

__all__ = [
    "FORMAT_NAME",
    "FORMAT_VERSION",
    "Activity",
    "Attachment",
    "BundleLimits",
    "BundleStats",
    "BundleTooLargeError",
    "BundleWriter",
    "Category",
    "Credential",
    "CustomField",
    "CustomValue",
    "EntityType",
    "Group",
    "Issue",
    "Journal",
    "JournalDetail",
    "Membership",
    "NotCarriedOver",
    "Priority",
    "Problem",
    "ProblemCode",
    "Project",
    "PublicAccess",
    "Record",
    "RecordError",
    "Relation",
    "Role",
    "Severity",
    "Status",
    "StoredFile",
    "TimeEntry",
    "Tracker",
    "User",
    "ValidationReport",
    "Version",
    "Watcher",
    "WikiPage",
    "WikiRedirect",
    "WikiVersion",
    "__version__",
    "date_value",
    "decimal_value",
    "inspect_bundle",
    "is_source_ref",
    "placeholders",
    "source_ref",
    "timestamp",
    "validate_bundle",
]
