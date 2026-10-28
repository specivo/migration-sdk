"""Timestamps, dates, decimals and source refs."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from specivo_migration_sdk import date_value, decimal_value, is_source_ref, source_ref, timestamp
from specivo_migration_sdk.helpers import is_timestamp

BANGKOK = ZoneInfo("Asia/Bangkok")


class TestTimestamp:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2024-03-01T09:30:00+07:00", "2024-03-01T09:30:00+07:00"),
            ("2024-03-01T09:30:00.000+0700", "2024-03-01T09:30:00+07:00"),
            ("2024-03-01T09:30:00.123+0700", "2024-03-01T09:30:00.123000+07:00"),
            ("2024-03-01T02:30:00Z", "2024-03-01T02:30:00Z"),
            ("2024-03-01T02:30:00+00:00", "2024-03-01T02:30:00Z"),
            ("2024-03-01 09:30:00+07", "2024-03-01T09:30:00+07:00"),
            ("  2024-03-01T09:30:00-05:00 ", "2024-03-01T09:30:00-05:00"),
            # Seven or nine fractional digits are cut to the microsecond.
            ("2024-03-01T09:30:00.123456789Z", "2024-03-01T09:30:00.123456Z"),
        ],
    )
    def test_strings(self, value: str, expected: str) -> None:
        assert timestamp(value) == expected
        assert is_timestamp(expected)

    def test_an_aware_datetime(self) -> None:
        assert timestamp(datetime(2024, 3, 1, 9, 30, tzinfo=BANGKOK)) == "2024-03-01T09:30:00+07:00"
        assert timestamp(datetime(2024, 3, 1, 2, 30, tzinfo=UTC)) == "2024-03-01T02:30:00Z"

    @pytest.mark.parametrize("value", [1709260200, 1709260200.0, 1709260200000])
    def test_epoch_seconds_or_milliseconds(self, value: float) -> None:
        assert timestamp(value) == "2024-03-01T02:30:00Z"

    def test_an_epoch_value_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="epoch"):
            timestamp(10**20)

    @pytest.mark.parametrize("value", ["2024-03-01T09:30:00", datetime(2024, 3, 1, 9, 30)])
    def test_no_offset_is_refused(self, value: str | datetime) -> None:
        with pytest.raises(ValueError, match="no UTC offset"):
            timestamp(value)

    def test_no_offset_with_a_zone_to_assume(self) -> None:
        assert timestamp("2024-03-01 09:30:00", assume_tz=BANGKOK) == "2024-03-01T09:30:00+07:00"
        assert timestamp(datetime(2024, 3, 1, 9, 30), assume_tz=UTC) == "2024-03-01T09:30:00Z"

    def test_an_offset_already_there_wins_over_the_assumed_zone(self) -> None:
        assert timestamp("2024-03-01T09:30:00Z", assume_tz=BANGKOK) == "2024-03-01T09:30:00Z"

    def test_an_offset_in_seconds_becomes_utc(self) -> None:
        odd = timezone(timedelta(hours=6, minutes=42, seconds=4))
        assert timestamp(datetime(1900, 1, 1, 6, 42, 4, tzinfo=odd)) == "1900-01-01T00:00:00Z"

    @pytest.mark.parametrize("value", ["yesterday", "2024-02-30T10:00:00Z", ""])
    def test_not_a_timestamp(self, value: str) -> None:
        with pytest.raises(ValueError):
            timestamp(value)

    @pytest.mark.parametrize("value", [True, None, date(2024, 1, 1)])
    def test_not_a_timestamp_type(self, value: object) -> None:
        with pytest.raises((TypeError, ValueError)):
            timestamp(value)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("value", "valid"),
        [
            ("2024-03-01T09:30:00Z", True),
            ("2024-03-01T09:30:00.1+07:00", True),
            ("2024-03-01T09:30:00", False),
            ("2024-03-01 09:30:00Z", False),
            ("2024-02-30T09:30:00Z", False),
            ("2024-03-01T09:30:00+0700", False),
        ],
    )
    def test_is_timestamp_follows_the_schema(self, value: str, valid: bool) -> None:
        assert is_timestamp(value) is valid


class TestDate:
    def test_values(self) -> None:
        assert date_value(date(2024, 3, 1)) == "2024-03-01"
        assert date_value(datetime(2024, 3, 1, 23, 0, tzinfo=BANGKOK)) == "2024-03-01"
        assert date_value(" 2024-03-01 ") == "2024-03-01"

    @pytest.mark.parametrize("value", ["2024-02-30", "01/03/2024", "2024-03-01T00:00:00Z"])
    def test_not_a_date(self, value: str) -> None:
        with pytest.raises(ValueError):
            date_value(value)

    def test_not_a_date_type(self) -> None:
        with pytest.raises(TypeError):
            date_value(20240301)  # type: ignore[arg-type]


class TestDecimal:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("1.333333"), "1.333333"),
            (Decimal("1E+2"), "100"),
            ("2.50", "2.50"),
            (3, 3),
            (1.5, 1.5),
        ],
    )
    def test_values(self, value: Decimal | str | int | float, expected: str | int | float) -> None:
        assert decimal_value(value) == expected

    @pytest.mark.parametrize("value", [Decimal("NaN"), float("inf"), "Infinity", "lots"])
    def test_not_a_finite_number(self, value: Decimal | float | str) -> None:
        with pytest.raises(ValueError):
            decimal_value(value)

    def test_a_boolean_is_not_a_number(self) -> None:
        with pytest.raises(TypeError):
            decimal_value(True)


class TestSourceRef:
    def test_plain_keys_stay_as_they_are(self) -> None:
        assert source_ref(10042) == "10042"
        assert source_ref("web_15") == "web_15"

    def test_parts_are_joined(self) -> None:
        assert source_ref("comment", 10042, 7) == "comment:10042:7"

    def test_unsafe_characters_are_encoded(self) -> None:
        ref = source_ref("Home Page {draft}|v2")
        assert ref == "Home%20Page%20%7Bdraft%7D%7Cv2"
        assert is_source_ref(ref)

    def test_a_separator_inside_a_part_cannot_collide(self) -> None:
        assert source_ref("a:b") != source_ref("a", "b")
        assert source_ref("a%3Ab") != source_ref("a:b")

    def test_non_ascii_is_kept(self) -> None:
        assert source_ref("หน้าแรก") == "หน้าแรก"

    def test_a_long_ref_is_shortened_stably(self) -> None:
        long_key = "x" * 400
        ref = source_ref("page", long_key)
        assert len(ref) == 255
        assert is_source_ref(ref)
        assert ref == source_ref("page", long_key)
        assert ref != source_ref("page", long_key + "y")

    @pytest.mark.parametrize("parts", [(), ("",), (True,), (1.5,)])
    def test_bad_parts(self, parts: tuple[object, ...]) -> None:
        with pytest.raises((TypeError, ValueError)):
            source_ref(*parts)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("value", "valid"),
        [("u1", True), ("a b", False), ("", False), ("a{b", False), ("a|b", False), ("x" * 256, False), (1, False)],
    )
    def test_is_source_ref(self, value: object, valid: bool) -> None:
        assert is_source_ref(value) is valid
