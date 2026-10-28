"""``specivo-bundle``: check a bundle, or say what it holds.

::

    specivo-bundle validate export.zip      # exit 0 when valid, 1 when not
    specivo-bundle stats export.zip
    specivo-bundle validate --files-path files/ export/   # attachments kept outside the bundle

Both read only the bundle (and the files directory). ``--json`` prints the
result as JSON instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from specivo_migration_sdk import __version__
from specivo_migration_sdk.format import COUNT_KEYS
from specivo_migration_sdk.limits import BundleLimits, parse_size
from specivo_migration_sdk.report import Severity
from specivo_migration_sdk.validate import BundleStats, inspect_bundle

# How many problems the text report shows; --json always has them all (up to the report's cap).
_SHOWN_PROBLEMS = 200


def _size(value: str) -> int:
    try:
        return parse_size(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Expected a whole number, got {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"Expected a positive number, got {value!r}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="specivo-bundle", description="Check Specivo import bundles.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    defaults = BundleLimits()
    for name, help_text in (
        ("validate", "Check a bundle as Specivo does before loading it. Exits 1 if it is not valid."),
        ("stats", "Say what a bundle holds: its source, the records per file, stored files."),
    ):
        command = commands.add_parser(name, help=help_text, description=help_text)
        command.add_argument("path", help="The bundle: a directory or a .zip archive.")
        command.add_argument("--json", action="store_true", help="Print the result as JSON.")
        command.add_argument(
            "--files-path",
            metavar="DIR",
            help="Read the attachment bytes from DIR/<sha256> instead of the bundle's files/.",
        )
        limits = command.add_argument_group("size limits (the same as Specivo's)")
        limits.add_argument("--max-unpacked-size", type=_size, default=defaults.max_unpacked_bytes, metavar="SIZE")
        limits.add_argument("--max-entries", type=_positive, default=defaults.max_entries, metavar="N")
        limits.add_argument("--max-entry-size", type=_size, default=defaults.max_entry_bytes, metavar="SIZE")
        limits.add_argument(
            "--max-compression-ratio", type=_positive, default=defaults.max_compression_ratio, metavar="N"
        )
        limits.add_argument("--max-line-size", type=_size, default=defaults.max_line_bytes, metavar="SIZE")
        limits.add_argument("--max-json-size", type=_size, default=defaults.max_json_bytes, metavar="SIZE")
    return parser


def _limits(args: argparse.Namespace) -> BundleLimits:
    return BundleLimits(
        max_unpacked_bytes=args.max_unpacked_size,
        max_entries=args.max_entries,
        max_entry_bytes=args.max_entry_size,
        max_compression_ratio=args.max_compression_ratio,
        max_line_bytes=args.max_line_size,
        max_json_bytes=args.max_json_size,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = inspect_bundle(args.path, files_path=args.files_path, limits=_limits(args))
    if args.command == "validate":
        if args.json:
            print(json.dumps(stats.report.as_dict(), indent=2, ensure_ascii=False))
        else:
            print(stats.report.format_text(limit=_SHOWN_PROBLEMS))
        return 0 if stats.report.ok else 1

    if not stats.manifest:
        # No manifest worth describing (missing, unsupported or not matching
        # its schema): say why instead.
        if args.json:
            print(json.dumps(stats.report.as_dict(), indent=2, ensure_ascii=False))
        else:
            print(stats.report.format_text(limit=_SHOWN_PROBLEMS))
        return 1
    if args.json:
        print(json.dumps(_stats_dict(stats), indent=2, ensure_ascii=False))
    else:
        print(_stats_text(stats))
    return 0


def _stats_dict(stats: BundleStats) -> dict[str, Any]:
    manifest = stats.manifest
    return {
        "path": stats.report.path,
        "valid": stats.report.ok,
        "errors": stats.report.totals[Severity.ERROR],
        "warnings": stats.report.totals[Severity.WARNING],
        "format_version": manifest.get("format_version"),
        "source": manifest.get("source"),
        "generator": manifest.get("generator"),
        "exported_at": manifest.get("exported_at"),
        "records": {key: stats.report.records.get(key, 0) for key in COUNT_KEYS},
        "with_extra": dict(sorted(stats.extra.items())),
        "stored_files": stats.stored_files,
        "stored_bytes": stats.stored_bytes,
        "not_carried_over": manifest.get("not_carried_over") or [],
    }


def _stats_text(stats: BundleStats) -> str:
    data = _stats_dict(stats)
    report = stats.report

    source = data["source"] or {}
    generator = data["generator"] or {}
    verdict = "valid" if report.ok else f"INVALID ({data['errors']} error(s); run 'specivo-bundle validate')"
    lines = [
        f"Bundle {data['path']}: {verdict}",
        f"  format     {data['format_version']}",
        f"  source     {source.get('system')} {source.get('instance')}"
        + (f" (version {source['version']})" if source.get("version") else ""),
        f"  generator  {generator.get('name')} {generator.get('version')}",
        f"  exported   {data['exported_at']}",
        "",
        "Records:",
    ]
    records: dict[str, int] = data["records"]
    lines.extend(
        f"  {name:<20} {count}" + (f"  ({stats.extra[name]} with extra)" if stats.extra.get(name) else "")
        for name, count in records.items()
        if count
    )
    if not any(records.values()):
        lines.append("  (none)")
    lines += ["", f"Stored files: {data['stored_files']} ({data['stored_bytes']} bytes)"]
    if data["not_carried_over"]:
        lines += ["", "Not carried over:"]
        for item in data["not_carried_over"]:
            count = f" ({item['count']})" if item.get("count") is not None else ""
            lines.append(f"  - {item['what']}{count}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
