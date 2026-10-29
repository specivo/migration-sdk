# specivo-migration-sdk

A small Python library for writing and checking [Specivo](https://github.com/specivo/specivo)
import bundles. An import bundle is how data from another tracker gets into Specivo: an importer
reads the source system and writes a bundle, and Specivo validates the bundle and loads it. This
package lets a Python importer do its half without depending on Specivo itself:

- typed records (dataclasses) for everything a bundle holds, checked against the format's JSON
  Schemas, which ship with the package;
- `BundleWriter`, which streams records to disk, stores attachment bytes once per distinct
  content (in the bundle, or in a separate files directory), writes the manifest with its counts,
  and writes a directory, or a `.zip` when asked for one;
- `validate_bundle()`, which makes the same checks Specivo makes before it loads a bundle, and
  reports each problem with its file and line;
- helpers for timestamps, source refs and the placeholders text uses to refer to other records;
- the `specivo-bundle` command: `validate` and `stats`.

The format itself is specified in
[`docs/import-bundle/v1.md`](https://github.com/specivo/specivo/blob/main/docs/import-bundle/v1.md)
in the Specivo repository. Read it before writing an importer: it says what every field means and
how Specivo treats it.

Its only dependency is `jsonschema`. Python 3.12 or later.

**Compatibility:** bundle format v1, Specivo 0.11.0 or newer.

[Specivo](https://specivo.io) is a self-hosted platform for project tracking, a knowledge base and
AI-safe automation. Its source is at [github.com/specivo/specivo](https://github.com/specivo/specivo),
and its documentation at [specivo.io/docs/specivo](https://specivo.io/docs/specivo/).

## Install

The SDK is not on PyPI. Releases are tags of this repository, with the wheel and sdist attached to
each [GitHub Release](https://github.com/specivo/migration-sdk/releases). Pin a tag:

```bash
# as a dependency of your importer
uv add "specivo-migration-sdk @ git+https://github.com/specivo/migration-sdk@v0.1.0"
# or in pyproject.toml:
#   dependencies = ["specivo-migration-sdk @ git+https://github.com/specivo/migration-sdk@v0.1.0"]

# only the specivo-bundle command
uv tool install git+https://github.com/specivo/migration-sdk@v0.1.0
# or: pipx install git+https://github.com/specivo/migration-sdk@v0.1.0
```

## Writing a bundle

One project, two users, and an issue with a comment that also changes its status:

```python
from specivo_migration_sdk import (BundleWriter, Issue, Journal, JournalDetail, Priority, Project, Status,
                                Tracker, User, placeholders, validate_bundle)

with BundleWriter("export", source_system="sample", source_instance="tracker.example.com",
                  generator_name="sample-importer", generator_version="0.1.0") as bundle:
    bundle.add(Tracker(source_ref="bug", name="Bug", default_status_ref="open"))
    bundle.add(Status(source_ref="open", name="Open", category="backlog"))
    bundle.add(Status(source_ref="doing", name="In Progress", category="active"))
    bundle.add(Priority(source_ref="normal", name="Normal", is_default=True))
    bundle.add(User(source_ref="u1", login="alice", display_name="Alice Example", email="alice@example.com"))
    bundle.add(User(source_ref="u2", login="bob", display_name="Bob Example"))
    bundle.add(Project(source_ref="p1", identifier="web", name="Website"))
    bundle.add(Issue(source_ref="10001", project_ref="p1", tracker_ref="bug", status_ref="doing",
                     priority_ref="normal", subject="Login fails", author_ref="u1",
                     created_at="2024-02-01T10:00:00+07:00"))
    bundle.add(Journal(source_ref="c1", issue_ref="10001", user_ref="u2", created_at="2024-02-02T10:00:00+07:00",
                       notes=f"Reproduced, {placeholders.user('u1')}. Working on it.",
                       details=[JournalDetail(property="attr", prop_key="status_id",
                                              old_ref="open", new_ref="doing")]))
print(validate_bundle("export").format_text())
```

A few things worth knowing:

- **A directory, or a zip.** The writer writes a directory by default. It writes a `.zip` only
  for a target named `*.zip` or with `zip=True` (`zip=False` writes a directory whatever the
  name). A zip is handy for small exports and trial runs; Specivo unpacks it into a temporary
  directory before loading, so it needs room for both. For anything with many attachments,
  write a directory, or keep the attachments out of the bundle with `files_path` (see
  [Large migrations](#large-migrations)).
- **`source_ref`** is each record's key in the source, as a string. It must be stable: export the
  same source twice and every record must get the same ref, because that is how Specivo knows a
  record is already imported. `source_ref(...)` builds a valid ref from any key, or from several
  parts (`source_ref("comment", 10042)`).
- **`source_system` and `source_instance`** scope those refs in Specivo. Use one pair per
  installation of the source, the same pair every time you export it.
- **Records go to their file as they are added**, each one checked against its schema first; a
  bad record raises `RecordError` naming the field. Records may come in any order. What one record
  cannot show (a reference to a record that is never added) is found by `validate_bundle()`.
- **Memory.** To refuse a duplicate `source_ref` as soon as it is added, the writer keeps every
  ref it has seen: roughly 100 MB per million records. Everything else is streamed to disk.
- **Timestamps** need a UTC offset. Give a `datetime` with a zone, or a string such as
  `2024-02-01T10:00:00.000+0700`; `timestamp(value, assume_tz=...)` converts what a source gives,
  including epoch seconds or milliseconds, and refuses to guess a missing offset.
- **Estimates are hours**, zero or more, given as a `Decimal`, a number or a string of digits.
  `estimated_hours` is the estimate Specivo shows and sums (for a source with an original and a
  remaining estimate, the original); `remaining_estimate` is the time left as the source tracked
  it. Specivo keeps no history for the remaining estimate, so write no journal detail for it.
- **History names records by ref.** A journal detail changing an attribute that names a record
  (`project_id`, `tracker_id`, `status_id`, `priority_id`, `assigned_to_id`, `category_id`,
  `fixed_version_id`, `sprint_id`, `parent_id`; `REFERENCE_ATTRIBUTES` in `specivo_migration_sdk.format`) gives
  the records' refs as `old_ref` / `new_ref`, never the source's ids as values: Specivo would read
  those as ids of its own. Put the source's text for the value (a version's name, an issue's key)
  in `old_value` / `new_value` where there is one. Specivo stores the Specivo id of the record a
  ref names, and the text when the record was not imported (history often names a version or a
  status the source has since deleted), so `validate_bundle()` does not require such a ref to
  name a record in the bundle. A change of a custom field whose `field_format` is `user` or
  `version` (`REFERENCE_FIELD_FORMATS`) is written the same way, one value a side: a `multiple`
  field's change is a detail per value removed (`old_ref` only) and per value added (`new_ref`
  only). A ref on any other detail is an error.
- **Sprints and tags.** A `Sprint` belongs to one project and holds issues of that project only
  (an issue's `sprint_ref`; a sprint of another project is an error). Give it the status it had
  (`planned`, `active`, `completed`) and, for a completed one, `completed_at`. Specivo has one
  active sprint per project and imports a second one as planned. A source whose sprints span
  projects writes a sprint per project. An issue's `tags` are names, 1 to 64 characters, created
  in its project as needed; Specivo keeps no history of them.
- **Text is Markdown.** Refer to other records with placeholders, which Specivo turns into links
  and mentions once it knows what they are called: `placeholders.issue(ref)`,
  `placeholders.user(ref)`, `placeholders.wiki(ref, "text")`, and for attachments
  `placeholders.attachment_image(ref, "alt")` or `placeholders.attachment_link(ref, "text")`.
  An issue placeholder may name an issue exported in an earlier bundle of the same source (one
  project at a time, say): Specivo looks it up among that source's earlier imports, and
  `validate_bundle()` reports it as an `external_ref` warning, not an error. Every other
  placeholder must name a record in the bundle. A placeholder whose record is never imported
  becomes plain text, its label or else its ref, so give issue placeholders a label where the
  bare ref would mean nothing to a reader.
- **Links between parts.** The same goes for an issue's `parent_ref` and for one end of a
  `Relation`: either may name an issue of another bundle of the same source, an `external_ref`
  warning. Specivo makes the link once both issues are imported, whichever bundle is loaded
  first, and keeps it waiting until then. Write a relation between two parts into both bundles
  if that is simpler: it is made once. A relation with neither end in the bundle is an error.
- **Attachments**: store the bytes first, then add the record with the hash it returns. The bytes
  can be `bytes`, a path, an open file or an iterable of chunks (a streamed download):

  ```python
  stored = bundle.add_file(response.iter_bytes())
  bundle.add(Attachment(source_ref="a1", container_kind="issue", container_ref="10001",
                        filename="log.txt", sha256=stored.sha256, filesize=stored.size))
  ```

- **Nothing dropped silently**: put source data with no field of its own in a record's `extra`,
  and whole features you cannot carry over in `bundle.not_carried_over("Sprint boards", count=4)`.
- If the `with` block raises, the bundle is not finished: a `.zip` is not written, and a directory
  is left without its manifest, so it cannot be loaded by mistake. Attachment files are written
  under a temporary name and renamed once complete, so `files/` (or the files directory) never
  holds a half-written file.
- **Size limits.** The writer never finishes a bundle Specivo would refuse for its size. Closing
  checks the limits Specivo reads with (`BundleLimits`: the size of `manifest.json`,
  `lookups.json` and `public_access.json`, and for a `.zip` the number of entries, the size of
  each and the total) and raises `BundleTooLargeError` instead of writing. Attachment bytes are
  stored in the zip uncompressed, and any other entry that would compress more than Specivo's
  reader accepts is stored too. Pass `limits=` to match an import run with raised limits.

## Checking a bundle

```bash
specivo-bundle validate export         # exit status 0 when valid, 1 when not
specivo-bundle stats export            # source, records per file, stored files
specivo-bundle validate --files-path files export   # attachments in a separate directory
```

Both take `--json`, `--files-path DIR` (see [Large migrations](#large-migrations)), and the size limits Specivo applies (`--max-line-size`, `--max-json-size`,
and for a zip `--max-unpacked-size`, `--max-entries`, `--max-entry-size`,
`--max-compression-ratio`). From Python, `validate_bundle(path)` returns the report:
`report.ok`, and `report.problems`, each with its file, line or JSON pointer, and a stable `code`.
`validate_bundle(path, files_path=...)` checks against a separate files directory.

## Loading it into Specivo

On the Specivo server:

```bash
# check the bundle: no database writes
python -m specivo.cli.import_data --source bundle --path export --validate
# rehearse: the whole import inside a transaction that is rolled back, then the report
python -m specivo.cli.import_data --source bundle --path export --dry-run
# import
python -m specivo.cli.import_data --source bundle --path export
```

Loading the same bundle again creates nothing new, and a later export of the same source (same
`source_system`, `source_instance` and refs) adds only the records created since.

## Large migrations

A real migration can carry many gigabytes of attachments. Keep them out of any archive: most are
compressed already (images, PDFs, office documents), a zip needs disk space for itself and for the
unpacked copy, and copying one huge file cannot resume. Write the attachments to a plain directory
instead, and the records to a small bundle of their own:

```python
with BundleWriter("export", files_path="files", source_system=..., source_instance=...,
                  generator_name=..., generator_version=...) as bundle:
    stored = bundle.add_file(response.iter_bytes())   # lands in files/<sha256>
    ...
```

The bundle then has no `files/` directory; the bytes are in `files/<sha256>`, named by their
content. Nothing about the files directory is written into the bundle, so the same bundle can be
loaded wherever the files end up. The records may still go into a zip (`"export.zip"`) when that
is easier to move: only the records are in it.

Then:

1. **Export** to a folder, as above.
2. **Copy** both to the Specivo host with `rsync`, which resumes an interrupted copy and skips
   files already there:
   `rsync -a --partial export files specivo-host:/srv/import/`
3. **Validate** on the host: no database writes.
   `python -m specivo.cli.import_data --source bundle --path /srv/import/export --files-path /srv/import/files --validate`
4. **Dry run**: the whole import in a transaction that is rolled back.
   `... --path /srv/import/export --files-path /srv/import/files --dry-run`
5. **Import**: the same command without `--dry-run`.

`specivo-bundle validate --files-path files export` makes the same checks before you copy
anything. They are the checks made inside a bundle: every attachment's file is there, has its
size and hashes to its name, and the directory holds no symbolic links, pipes, devices or
sockets.

**Re-exports only add what is new.** Export again into a new bundle with the same `files_path`:
a file already there whose bytes hash to its name is kept untouched (a damaged one is replaced),
so only new attachments are written, and `rsync` copies only those. Loading the new bundle
creates only the records and attachments that are not imported yet. If an export fails, the
files it completed stay in the directory and are reused by the next run.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict
```

### Checking the schemas against Specivo

The JSON Schemas under `src/specivo_migration_sdk/schemas/v1/` are copies of the ones Specivo
publishes in `docs/import-bundle/v1/`, and must stay byte-identical to them. A test compares the
two, reading the published schemas from the directory named by `SPECIVO_CORE_SCHEMAS`:

```bash
git clone --depth 1 --branch v0.11.0 https://github.com/specivo/specivo.git ../specivo
SPECIVO_CORE_SCHEMAS=../specivo/docs/import-bundle/v1 uv run pytest
```

Without the variable the comparison is skipped, unless `SPECIVO_REQUIRE_CORE_SCHEMAS=1` is set,
which makes it fail instead; a directory that does not exist always fails.

CI checks out the Specivo repository at a pinned ref (`SPECIVO_CORE_REF` in
`.github/workflows/ci.yml`), never a moving branch, and runs the tests with both variables set.
Moving the pin to a newer Specivo release is a deliberate change, made together with the schema
update it brings. CI also runs the tests against the lowest versions of the dependencies
`pyproject.toml` allows.

### Releasing

Set the version in `pyproject.toml`, commit, then tag and push the tag (`git tag v0.1.0 && git push
origin v0.1.0`). The release workflow builds the wheel and sdist with `uv build` and attaches them
to a GitHub Release for the tag.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
