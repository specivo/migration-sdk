"""Write an import bundle, one record at a time.

:class:`BundleWriter` streams records to their JSON-lines files as they are
added, so an export of any size runs in constant memory (apart from a set of
the ``source_ref``s seen, for catching duplicates early). Attachment bytes are
stored once per distinct content under ``files/<sha256>``, or, with
``files_path``, as ``<files_path>/<sha256>`` outside the bundle. The manifest, with
the count of every file, is written last, when the writer is closed: a bundle
whose export failed half way has no manifest, so it can never be loaded by
mistake.

Each record is checked against its JSON Schema as it is added, and a record
that does not match raises :class:`RecordError` naming the property. What a
single record cannot show (a reference to a record that is never written, a
placeholder naming nothing) is caught by :func:`~specivo_migration_sdk.validate.validate_bundle`,
which is worth running on every bundle before handing it over.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Any, BinaryIO

from specivo_migration_sdk import models
from specivo_migration_sdk.container import open_regular
from specivo_migration_sdk.format import (
    COUNT_KEYS,
    FILES_DIR,
    FORMAT_NAME,
    FORMAT_VERSION,
    LOOKUP_LISTS,
    LOOKUPS,
    MANIFEST,
    PUBLIC_ACCESS,
    RECORD_FILES,
    EntityType,
    LookupList,
    RecordFile,
)
from specivo_migration_sdk.helpers import is_source_ref, timestamp
from specivo_migration_sdk.limits import RATIO_CHECKED_FROM, BundleLimits, format_size
from specivo_migration_sdk.validate import schema_message, validator

_CHUNK = 1024 * 1024
_FILE_FOR: dict[type, RecordFile] = {record_file.model: record_file for record_file in RECORD_FILES}
_LOOKUP_FOR: dict[type, LookupList] = {lookup.model: lookup for lookup in LOOKUP_LISTS}
# Where add_file() puts bytes while it hashes them; never left in a finished bundle.
_STAGING_PREFIX = ".partial-"

# What add_file() reads: bytes, a path, an open binary file, or chunks of bytes.
type FileSource = bytes | bytearray | memoryview | str | os.PathLike[str] | BinaryIO | Iterable[bytes]


class RecordError(ValueError):
    """A record that cannot go into the bundle as given."""


class BundleTooLargeError(ValueError):
    """The bundle would exceed a limit Specivo applies when reading it, so it is not written."""


@dataclass(frozen=True, slots=True)
class StoredFile:
    """Content stored under ``files/`` (or the files directory): put ``sha256`` (and ``size``) on the attachment."""

    sha256: str
    size: int


class BundleWriter:
    """Writes one bundle: a directory by default, or an archive when asked for.

    The output is a zip only for a ``*.zip`` target or with ``zip=True``
    (``zip=False`` writes a directory whatever the name). A zip suits small
    exports and trial runs; for a large migration write a directory, or keep
    the attachments out of the bundle altogether with ``files_path``.

    Use it as a context manager, or call :meth:`close`. The target must not
    exist yet (an empty directory is accepted). A zip is assembled in a
    temporary directory next to the target and moved into place when closed.
    If the ``with`` block raises, nothing is finished: a zip is not written at
    all, and a directory is left without a manifest.

    ``files_path`` names a directory for the attachment bytes, which are then
    stored there as ``<files_path>/<sha256>`` and the bundle has no ``files/``.
    The directory may already exist and hold files from earlier exports: a
    file whose bytes hash to its name is kept as it is, so exporting again
    into the same directory only adds what is new. Load such a bundle with
    Specivo's ``--files-path`` and check it with
    ``validate_bundle(path, files_path=...)``. Files are complete once
    :meth:`add_file` returns, and stay in the directory if the export fails.

    ``source_system`` and ``source_instance`` scope the identifier map in
    Specivo: use the same pair for every export of one installation, and a
    different instance for every other installation. ``generator_name`` and
    ``generator_version`` name your importer.
    """

    def __init__(
        self,
        target: str | os.PathLike[str],
        *,
        source_system: str,
        source_instance: str,
        generator_name: str,
        generator_version: str,
        source_version: str | None = None,
        exported_at: datetime | str | None = None,
        zip: bool | None = None,
        limits: BundleLimits | None = None,
        files_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.target = Path(target)
        self.files_path = Path(files_path) if files_path is not None else None
        self.zip = self.target.suffix.lower() == ".zip" if zip is None else zip
        self.limits = limits or BundleLimits()

        source: dict[str, Any] = {"system": source_system, "instance": source_instance}
        if source_version:
            source["version"] = source_version
        try:
            exported = timestamp(exported_at or datetime.now(UTC))
        except (ValueError, TypeError) as exc:
            raise RecordError(f"{MANIFEST}: exported_at: {exc}") from None
        self._manifest: dict[str, Any] = {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "source": source,
            "generator": {"name": generator_name, "version": generator_version},
            "exported_at": exported,
        }
        # Check the manifest now, not after an hour of exporting, and before
        # anything is created on disk.
        _check(MANIFEST, "manifest.schema.json", self._manifest)

        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        if self.target.exists() and (self.zip or not self.target.is_dir() or any(self.target.iterdir())):
            raise FileExistsError(f"{self.target} already exists")
        if self.files_path is not None:
            _check_files_path(self.files_path, self.target)
        if self.zip:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            self._temporary = tempfile.TemporaryDirectory(prefix=".specivo-bundle-", dir=self.target.parent)
            self.root = Path(self._temporary.name)
        else:
            self.target.mkdir(parents=True, exist_ok=True)
            self.root = self.target
        self.files_dir = self.files_path if self.files_path is not None else self.root / FILES_DIR

        self._handles: dict[str, IO[str]] = {}
        self._counts: Counter[str] = Counter()
        self._refs: dict[EntityType, set[str]] = {}
        self._lookups: dict[str, list[dict[str, Any]]] = {}
        self._public_access: dict[str, Any] | None = None
        self._not_carried_over: list[dict[str, Any]] = []
        self._files: dict[str, int] = {}
        self._closed = False

    def __enter__(self) -> BundleWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abandon()

    @property
    def counts(self) -> dict[str, int]:
        """Records written so far, by manifest count key."""
        return {key: self._counts[key] for key in COUNT_KEYS}

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    def add(self, record: models.LineRecord | models.LookupRecord) -> None:
        """Write one record: to its JSON-lines file, or to ``lookups.json`` for a lookup.

        An attachment's ``sha256`` must be one :meth:`add_file` returned.
        Raises :class:`RecordError` for a record that does not match its
        schema or reuses a ``source_ref``.
        """
        self._check_open()
        lookup = _LOOKUP_FOR.get(type(record))
        if lookup is not None:
            self._add_lookup(lookup, record)
            return
        record_file = _FILE_FOR.get(type(record))
        if record_file is None:
            raise TypeError(f"{type(record).__name__} is not a bundle record")

        data = _encode(record)
        _check(record_file.name, record_file.schema, data)
        if isinstance(record, models.Attachment) and record.sha256 not in self._files:
            raise RecordError(
                f"attachments: {record.source_ref!r}: add the file's bytes with add_file() first and use its sha256"
            )
        line = _dumps(record_file.name, data, indent=None) + "\n"
        size = len(line.encode("utf-8"))
        if size > self.limits.max_line_bytes:
            raise RecordError(
                f"{record_file.name}: record {data.get('source_ref')!r} is {size} bytes, over the "
                f"{format_size(self.limits.max_line_bytes)} Specivo reads in one line"
            )
        claims: list[tuple[EntityType, str]] = []
        if record_file.entity_type is not None:
            claims.append((record_file.entity_type, data["source_ref"]))
        if isinstance(record, models.WikiPage):
            claims += [(EntityType.WIKI_VERSION, version["source_ref"]) for version in data.get("versions", [])]
        self._claim(claims)

        handle = self._handles.get(record_file.name)
        if handle is None:
            handle = (self.root / record_file.name).open("w", encoding="utf-8", newline="\n")
            self._handles[record_file.name] = handle
        handle.write(line)
        self._counts[record_file.stem] += 1
        if isinstance(record, models.WikiPage):
            self._counts["wiki_versions"] += len(record.versions)

    def add_all(self, records: Iterable[models.LineRecord | models.LookupRecord]) -> None:
        """Write each of *records*, in order."""
        for record in records:
            self.add(record)

    def _add_lookup(self, lookup: LookupList, record: models.Record) -> None:
        data = _encode(record)
        _check(LOOKUPS, "lookups.schema.json", {lookup.name: [data]})
        # Written at close; checked now, so a bad value is reported with its record.
        _dumps(LOOKUPS, data)
        self._claim([(lookup.entity_type, data["source_ref"])])
        self._lookups.setdefault(lookup.name, []).append(data)
        self._counts[lookup.name] += 1

    def _claim(self, claims: list[tuple[EntityType, str]]) -> None:
        """Claim every ref of one record, or none of them if any is taken."""
        wanted: set[tuple[EntityType, str]] = set()
        for entity_type, ref in claims:
            if not is_source_ref(ref):
                # The schema's pattern lets a trailing newline through; Specivo does not.
                raise RecordError(f"{ref!r} is not a valid source_ref for {entity_type}")
            if ref in self._refs.get(entity_type, ()) or (entity_type, ref) in wanted:
                raise RecordError(f"Duplicate source_ref {ref!r} for {entity_type}")
            wanted.add((entity_type, ref))
        for entity_type, ref in wanted:
            self._refs.setdefault(entity_type, set()).add(ref)

    def set_public_access(self, access: models.PublicAccess) -> None:
        """Write ``public_access.json``: what non-members could read in the source."""
        self._check_open()
        data = _encode(access)
        _check(PUBLIC_ACCESS, "public_access.schema.json", data)
        _dumps(PUBLIC_ACCESS, data)
        self._public_access = data

    def not_carried_over(
        self,
        what: str,
        *,
        count: int | None = None,
        entity_type: str | None = None,
        source_ref: str | None = None,
    ) -> None:
        """Record in the manifest something the source had that the bundle does not."""
        self._check_open()
        item = models.NotCarriedOver(what=what, count=count, entity_type=entity_type, source_ref=source_ref)
        data = _encode(item)
        _check(MANIFEST, "manifest.schema.json", {**self._manifest, "not_carried_over": [data]})
        _dumps(MANIFEST, data)
        self._not_carried_over.append(data)

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def add_file(self, source: FileSource) -> StoredFile:
        """Store content under ``files/`` (or in ``files_path``) and return its hash and size.

        *source* is the bytes themselves, a path, a binary file open for
        reading, or an iterable of byte chunks (a streamed download). The bytes
        are hashed as they are written, so the name always matches the
        content. Content stored before is not stored again, and a file already
        in ``files_path`` whose bytes hash to its name is left untouched.
        """
        self._check_open()
        files_dir = self.files_dir
        files_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        staged = tempfile.NamedTemporaryFile(dir=files_dir, prefix=_STAGING_PREFIX, delete=False)  # noqa: SIM115
        try:
            with staged:
                for chunk in _chunks(source):
                    digest.update(chunk)
                    staged.write(chunk)
                    size += len(chunk)
            sha256 = digest.hexdigest()
            if sha256 in self._files or _holds(files_dir / sha256, sha256):
                Path(staged.name).unlink()
                self._files[sha256] = size
            else:
                Path(staged.name).replace(files_dir / sha256)
                self._files[sha256] = size
        except BaseException:
            Path(staged.name).unlink(missing_ok=True)
            raise
        return StoredFile(sha256, size)

    # ------------------------------------------------------------------
    # Finishing
    # ------------------------------------------------------------------

    def close(self) -> Path:
        """Write the manifest and the small JSON files, and return the bundle's path.

        Raises :class:`BundleTooLargeError`, writing nothing, if the bundle
        would exceed one of :attr:`limits`, which are the ones Specivo reads
        a bundle with. On any failure the bundle is abandoned.
        """
        if self._closed:
            return self.target
        try:
            for handle in self._handles.values():
                handle.close()
            manifest = {**self._manifest, "counts": self.counts}
            if self._not_carried_over:
                manifest["not_carried_over"] = self._not_carried_over
            small: dict[str, bytes] = {MANIFEST: _json_bytes(manifest)}
            if self._lookups:
                small[LOOKUPS] = _json_bytes(self._lookups)
            if self._public_access is not None:
                small[PUBLIC_ACCESS] = _json_bytes(self._public_access)
            self._check_limits(small)

            # The manifest goes last: a bundle without one is never loaded.
            for name in sorted(small, key=lambda name: name == MANIFEST):
                (self.root / name).write_bytes(small[name])
            if self._temporary is not None:
                self._write_zip()
                self._temporary.cleanup()
                self._temporary = None
        except BaseException:
            if self._temporary is None:
                (self.root / MANIFEST).unlink(missing_ok=True)
            self.abandon()
            raise
        self._closed = True
        return self.target

    def abandon(self) -> None:
        """Stop without finishing: no manifest is written, and a zip is not created."""
        for handle in self._handles.values():
            handle.close()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self._closed = True

    def _check_limits(self, small: dict[str, bytes]) -> None:
        """Refuse a bundle Specivo would refuse for its size, before any of it is finished."""
        limits = self.limits
        for name, data in small.items():
            if len(data) > limits.max_json_bytes:
                raise BundleTooLargeError(
                    f"{name} would be {len(data)} bytes, over the {format_size(limits.max_json_bytes)} "
                    "Specivo reads (max_json_bytes)"
                )
        if not self.zip:
            return
        sizes = {path.relative_to(self.root).as_posix(): path.stat().st_size for path in self._files_on_disk()}
        sizes.update({name: len(data) for name, data in small.items()})
        if len(sizes) > limits.max_entries:
            raise BundleTooLargeError(
                f"The archive would hold {len(sizes)} entries, over the {limits.max_entries} Specivo reads "
                "(max_entries)"
            )
        for name, size in sorted(sizes.items()):
            if size > limits.max_entry_bytes:
                raise BundleTooLargeError(
                    f"{name} is {size} bytes, over the {format_size(limits.max_entry_bytes)} Specivo reads "
                    "in one archive entry (max_entry_bytes)"
                )
        total = sum(sizes.values())
        if total > limits.max_unpacked_bytes:
            raise BundleTooLargeError(
                f"The archive would unpack to {total} bytes, over the {format_size(limits.max_unpacked_bytes)} "
                "Specivo accepts (max_unpacked_bytes)"
            )

    def _files_on_disk(self) -> list[Path]:
        return sorted(path for path in self.root.rglob("*") if path.is_file())

    def _write_zip(self) -> None:
        """Write the archive, storing any entry that compresses more than Specivo's reader accepts.

        Attachment bytes are stored uncompressed: they are mostly compressed
        already, and a file of zeros would otherwise look like a zip bomb. A
        JSON-lines file that deflates too well is written again, stored.
        """
        stored = {path for path in self._files_on_disk() if path.parent.name == FILES_DIR}
        partial = self.target.with_name(self.target.name + ".partial")
        try:
            while True:
                too_compressed = self._zip_once(partial, stored)
                if not too_compressed:
                    break
                stored |= too_compressed
            partial.replace(self.target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def _zip_once(self, partial: Path, stored: set[Path]) -> set[Path]:
        ratio = self.limits.max_compression_ratio
        too_compressed: set[Path] = set()
        with zipfile.ZipFile(partial, "w") as archive:
            for path in self._files_on_disk():
                compression = zipfile.ZIP_STORED if path in stored else zipfile.ZIP_DEFLATED
                archive.write(path, path.relative_to(self.root).as_posix(), compress_type=compression)
                info = archive.getinfo(path.relative_to(self.root).as_posix())
                if info.file_size > RATIO_CHECKED_FROM and info.file_size > max(info.compress_size, 1) * ratio:
                    too_compressed.add(path)
        return too_compressed

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("The bundle writer is closed")


def _check_files_path(files_path: Path, target: Path) -> None:
    """Refuse a files directory that is not a directory, or that nests with the bundle."""
    if files_path.exists() and not files_path.is_dir():
        raise NotADirectoryError(f"files_path {files_path} is not a directory")
    files_dir, bundle = files_path.resolve(), target.resolve()
    if files_dir.is_relative_to(bundle) or bundle.is_relative_to(files_dir):
        raise ValueError(f"files_path {files_path} and the bundle {target} must not be inside one another")


def _holds(path: Path, sha256: str) -> bool:
    """Whether *path* is a regular file (not a link) whose bytes hash to *sha256*."""
    if path.is_symlink() or not path.is_file():
        return False
    digest = hashlib.sha256()
    with open_regular(path) as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest() == sha256


def _encode(record: models.Record) -> dict[str, Any]:
    try:
        return record.to_dict()
    except (ValueError, TypeError) as exc:
        raise RecordError(str(exc)) from None


def _check(file: str, schema_name: str, data: Any) -> None:
    """Raise :class:`RecordError` if *data* does not match the schema."""
    errors = sorted(validator(schema_name).iter_errors(data), key=lambda e: e.json_path)
    if errors:
        ref = data.get("source_ref") if isinstance(data, dict) else None
        what = f"{file}: {ref!r}" if ref else file
        problems = "; ".join(
            f"/{'/'.join(str(p) for p in error.absolute_path)}: {schema_message(error)}" for error in errors
        )
        raise RecordError(f"{what}: {problems}")


def _chunks(source: FileSource) -> Iterable[bytes]:
    if isinstance(source, bytes | bytearray | memoryview):
        yield bytes(source)
    elif isinstance(source, str | os.PathLike):
        with Path(source).open("rb") as handle:
            yield from iter(lambda: handle.read(_CHUNK), b"")
    elif hasattr(source, "read"):
        yield from iter(lambda: source.read(_CHUNK), b"")
    else:
        for chunk in source:
            if not isinstance(chunk, bytes | bytearray | memoryview):
                raise TypeError(f"Expected chunks of bytes, got {type(chunk).__name__}")
            yield bytes(chunk)


def _dumps(file: str, data: dict[str, Any], *, indent: int | None = 2) -> str:
    """Return *data* as JSON, or raise :class:`RecordError` naming the file and record."""
    try:
        text = json.dumps(
            data, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"), allow_nan=False
        )
        text.encode("utf-8")
    except (TypeError, ValueError) as exc:
        ref = data.get("source_ref")
        what = f"{file}: {ref!r}" if ref else file
        reason = (
            "holds text that is not valid Unicode"
            if isinstance(exc, UnicodeError)
            else f"cannot be written as JSON: {exc}"
        )
        raise RecordError(f"{what}: {reason}") from None
    return text


def _json_bytes(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
