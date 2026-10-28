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
expanded, so a code sample can show a placeholder literally.
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

# Splits text on fenced code blocks, keeping the fences.
_FENCE_RE = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)

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


def find_placeholders(text: str | None) -> Iterator[Placeholder]:
    """Yield every placeholder in *text*, outside fenced code blocks."""
    if not text or "{{" not in text:
        return
    for index, part in enumerate(_FENCE_RE.split(text)):
        # Odd positions are the fenced blocks themselves.
        if index % 2:
            continue
        for match in PLACEHOLDER_RE.finditer(part):
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
