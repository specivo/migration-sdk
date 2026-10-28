"""Building placeholders, and finding them the way Specivo does."""

from __future__ import annotations

import pytest

from specivo_migration_sdk import placeholders
from specivo_migration_sdk.format import EntityType
from specivo_migration_sdk.placeholders import Placeholder, PlaceholderKind, find_placeholders


class TestBuilders:
    def test_each_kind(self) -> None:
        assert placeholders.issue("10042") == "{{issue:10042}}"
        assert placeholders.issue("10042", "the login bug") == "{{issue:10042|the login bug}}"
        assert placeholders.user("u1") == "{{user:u1}}"
        assert placeholders.attachment("a1") == "{{attachment:a1}}"
        assert placeholders.wiki("w1") == "{{wiki:w1}}"
        assert placeholders.wiki("w1", "the guide") == "{{wiki:w1|the guide}}"

    def test_an_empty_text_is_no_text(self) -> None:
        assert placeholders.issue("i1", "") == "{{issue:i1}}"

    def test_attachment_markdown(self) -> None:
        assert placeholders.attachment_image("a1") == "![]({{attachment:a1}})"
        assert placeholders.attachment_image("a1", "my [pic]") == "![my \\[pic\\]]({{attachment:a1}})"
        assert placeholders.attachment_link("a2", "the\nlog") == "[the log]({{attachment:a2}})"

    @pytest.mark.parametrize("ref", ["", "has space", "a|b", "{x}", "x" * 256])
    def test_a_bad_ref(self, ref: str) -> None:
        with pytest.raises(ValueError, match="source_ref"):
            placeholders.issue(ref)

    @pytest.mark.parametrize("text", ["a]]b", "line\nbreak", "{x}"])
    def test_a_bad_text(self, text: str) -> None:
        with pytest.raises(ValueError):
            placeholders.wiki("w1", text)

    def test_what_is_built_is_what_is_found(self) -> None:
        text = " ".join(
            [
                placeholders.issue("i1", "see this"),
                placeholders.user("u1"),
                placeholders.attachment_image("a1", "shot"),
                placeholders.wiki("w1"),
            ]
        )
        assert list(find_placeholders(text)) == [
            Placeholder(PlaceholderKind.ISSUE, "i1", "see this"),
            Placeholder(PlaceholderKind.USER, "u1"),
            Placeholder(PlaceholderKind.ATTACHMENT, "a1"),
            Placeholder(PlaceholderKind.WIKI, "w1"),
        ]


class TestFinding:
    def test_nothing_in_plain_text(self) -> None:
        assert list(find_placeholders("no references")) == []
        assert list(find_placeholders(None)) == []

    def test_fenced_code_is_skipped(self) -> None:
        text = "{{user:u1}}\n```\n{{user:u2}}\n```\n~~~\n{{user:u3}}\n~~~\n{{user:u4}}"
        assert [p.ref for p in find_placeholders(text)] == ["u1", "u4"]

    def test_other_braces_are_text(self) -> None:
        assert list(find_placeholders("{{project:p1}} {{ issue:i1 }} {{issue:}}")) == []

    def test_entity_types(self) -> None:
        assert [p.entity_type for p in find_placeholders("{{wiki:w}}{{attachment:a}}")] == [
            EntityType.WIKI_PAGE,
            EntityType.ATTACHMENT,
        ]
