"""Placeholders: how bundle text refers to other records.

An importer cannot know what a record will be called in Specivo: project keys
are assigned by the import, a login can be suffixed on a clash, a file is
renamed on upload. So text names the record by its ``source_ref``, and Specivo
replaces each placeholder after loading:

==========================  ===============================================
``{{issue:REF}}``           the issue's display key, ``ACME-15``
``{{issue:REF|text}}``      a link to the issue reading *text*
``{{user:REF}}``            a mention, ``@login``
``{{attachment:REF}}``      the file name, as a Markdown link target
``{{wiki:REF}}``            a link to the page, ``[[Title]]``
``{{wiki:REF|text}}``       a link to the page reading *text*
==========================  ===============================================

Build them with :func:`issue`, :func:`user`, :func:`attachment`,
:func:`wiki`, or :func:`attachment_image` and :func:`attachment_link` for the
usual Markdown around an attachment. Only those four kinds are placeholders;
any other ``{{...}}`` is ordinary text. Text inside fenced code blocks is never
expanded, so a code sample can show a placeholder literally. Fences pair the
way CommonMark pairs them (see :func:`_fenced_blocks`); inline code spans are
not code here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from specivo_migration_sdk.format import EntityType
from specivo_migration_sdk.helpers import is_source_ref


class PlaceholderKind(StrEnum):
    """What a placeholder refers to."""

    ISSUE = "issue"
    USER = "user"
    ATTACHMENT = "attachment"
    WIKI = "wiki"


# The entity type each kind's ref names.
PLACEHOLDER_ENTITY_TYPES: dict[PlaceholderKind, EntityType] = {
    PlaceholderKind.ISSUE: EntityType.ISSUE,
    PlaceholderKind.USER: EntityType.USER,
    PlaceholderKind.ATTACHMENT: EntityType.ATTACHMENT,
    PlaceholderKind.WIKI: EntityType.WIKI_PAGE,
}

# Kinds that may carry a ``|text``. A mention and a link target have no text
# of their own to replace.
LABELLED_KINDS = frozenset({PlaceholderKind.ISSUE, PlaceholderKind.WIKI})

# A ref is a source_ref, which the format keeps free of whitespace, braces and
# the bar, so the grammar needs no escaping.
PLACEHOLDER_RE = re.compile(r"\{\{(issue|user|attachment|wiki):([^\s{}|]+)(?:\|([^{}\n]*))?\}\}")

# A line that may open or close a fenced code block: up to three spaces, a run
# of three or more backticks or tildes, then the rest of the line.
_FENCE_LINE_RE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")

# Markdown that would end an image's alt text or a link's text early.
_MARKDOWN_TEXT_RE = re.compile(r"([\\\[\]])")


@dataclass(frozen=True, slots=True)
class Placeholder:
    """One placeholder found in a text."""

    kind: PlaceholderKind
    ref: str
    label: str | None = None

    @property
    def entity_type(self) -> EntityType:
        return PLACEHOLDER_ENTITY_TYPES[self.kind]

    def __str__(self) -> str:
        label = f"|{self.label}" if self.label else ""
        return f"{{{{{self.kind}:{self.ref}{label}}}}}"


def _fenced_blocks(text: str) -> Iterator[tuple[int, int]]:
    """Yield the ``(start, end)`` offsets of each fenced code block in *text*.

    Fences pair as in CommonMark: a fence is a whole line of three or more
    backticks or tildes, indented by at most three spaces (a backtick fence's
    info string holds no backtick). The block ends at a line of the same
    character, at least as long, with nothing after it but spaces and tabs,
    or else at the end of the text. A block starts at the beginning of its
    opening line and ends after its closing line.
    """
    opening = ""
    block_start = offset = 0
    for line in text.split("\n"):
        line_end = offset + len(line)
        match = _FENCE_LINE_RE.fullmatch(line.rstrip("\r"))
        if match is not None:
            run, rest = match.groups()
            if not opening:
                if not (run[0] == "`" and "`" in rest):
                    opening, block_start = run, offset
            elif run[0] == opening[0] and len(run) >= len(opening) and not rest.strip(" \t"):
                yield block_start, line_end
                opening = ""
        offset = line_end + 1
    if opening:
        yield block_start, len(text)


def _outside_code(text: str) -> Iterator[tuple[int, int]]:
    """Yield the ``(start, end)`` offsets of each stretch of *text* outside fenced code blocks."""
    position = 0
    for start, end in _fenced_blocks(text):
        yield position, start
        position = end
    yield position, len(text)


def find_placeholders(text: str | None) -> Iterator[Placeholder]:
    """Yield every placeholder in *text*, outside fenced code blocks."""
    if not text or "{{" not in text:
        return
    for start, end in _outside_code(text):
        for match in PLACEHOLDER_RE.finditer(text, start, end):
            label = match.group(3)
            yield Placeholder(PlaceholderKind(match.group(1)), match.group(2), label if label else None)


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def _build(kind: PlaceholderKind, ref: str, text: str | None = None) -> str:
    if not is_source_ref(ref):
        raise ValueError(f"{ref!r} is not a valid source_ref (1-255 characters, no whitespace, braces or '|')")
    if text:
        if any(char in text for char in "{}\n"):
            raise ValueError(f"A placeholder's text cannot contain braces or a newline: {text!r}")
        if "]]" in text:
            raise ValueError(f"A placeholder's text cannot contain ']]': {text!r}")
    return str(Placeholder(kind, ref, text or None))


def issue(ref: str, text: str | None = None) -> str:
    """Refer to an issue: its display key (``ACME-15``), or a link reading *text*.

    *ref* may name an issue of an earlier bundle of the same source: Specivo
    looks it up among that source's earlier imports when it loads the bundle.
    """
    return _build(PlaceholderKind.ISSUE, ref, text)


def user(ref: str) -> str:
    """Mention a user: ``@login``."""
    return _build(PlaceholderKind.USER, ref)


def attachment(ref: str) -> str:
    """An attachment's file name as a link target; use it inside ``(...)`` of a link or an image."""
    return _build(PlaceholderKind.ATTACHMENT, ref)


def wiki(ref: str, text: str | None = None) -> str:
    """Link to a wiki page: ``[[Title]]``, or a link reading *text*."""
    return _build(PlaceholderKind.WIKI, ref, text)


def attachment_image(ref: str, alt: str = "") -> str:
    """Markdown for an attached image: ``![alt]({{attachment:REF}})``."""
    return f"![{_markdown_text(alt)}]({attachment(ref)})"


def attachment_link(ref: str, text: str) -> str:
    """Markdown for a link to an attached file: ``[text]({{attachment:REF}})``."""
    return f"[{_markdown_text(text)}]({attachment(ref)})"


def _markdown_text(text: str) -> str:
    """Escape what would end a link's text early, and drop newlines, which would end the link."""
    return _MARKDOWN_TEXT_RE.sub(r"\\\1", " ".join(text.split()))
