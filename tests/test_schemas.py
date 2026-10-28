"""The models and the JSON Schemas agree field by field, and the schemas are the published ones.

For every record type: the model's fields are exactly the schema's
properties, the fields without a default are exactly the required ones, a
field that may be ``None`` is one the schema lets be ``null``, enumerations
list the same values, and timestamps, dates and decimals are typed as such on
both sides.
"""

from __future__ import annotations

import dataclasses
import json
import os
import types
from pathlib import Path
from typing import Any, Literal, TypeAliasType, Union, get_args, get_origin, get_type_hints

import pytest
from jsonschema import Draft202012Validator

from specivo_migration_sdk import models
from specivo_migration_sdk.format import LOOKUP_LISTS, RECORD_FILES, RecordFile
from specivo_migration_sdk.helpers import DateValue, DecimalValue, Timestamp
from specivo_migration_sdk.validate import schema_files, schema_registry, schemas

# A referencing resolver; the library exports no public name for its type.
type _Resolver = Any

_ID_BASE = "https://specivo.io/schemas/import-bundle/v1/"
_VALUE_REFS = {
    "dateTime": Timestamp,
    "optionalDateTime": Timestamp,
    "date": DateValue,
    "optionalDate": DateValue,
    "decimal": DecimalValue,
    "optionalDecimal": DecimalValue,
}


def _published_schemas() -> Path | None:
    """The schemas Specivo publishes, to compare the shipped copies with.

    ``SPECIVO_CORE_SCHEMAS`` names them (CI points it at a checkout of a pinned
    Specivo release). Without it there is nothing to compare with, which fails
    when ``SPECIVO_REQUIRE_CORE_SCHEMAS=1`` (as in CI) and skips otherwise.
    """
    explicit = os.environ.get("SPECIVO_CORE_SCHEMAS")
    if explicit:
        if not Path(explicit).is_dir():
            pytest.fail(f"SPECIVO_CORE_SCHEMAS={explicit} is not a directory")
        return Path(explicit)
    if os.environ.get("SPECIVO_REQUIRE_CORE_SCHEMAS") == "1":
        pytest.fail("SPECIVO_REQUIRE_CORE_SCHEMAS=1 but SPECIVO_CORE_SCHEMAS is not set")
    return None


# --------------------------------------------------------------------------
# Walking a schema
# --------------------------------------------------------------------------


@dataclasses.dataclass
class _Prop:
    """What a schema says about one property, with every ``$ref`` and ``anyOf`` followed."""

    nullable: bool = False
    types: set[str] = dataclasses.field(default_factory=set)
    enum: list[Any] | None = None
    value_def: str | None = None
    node: dict[str, Any] = dataclasses.field(default_factory=dict)
    resolver: _Resolver | None = None


def _resolver(schema_name: str) -> _Resolver:
    return schema_registry().resolver(base_uri=_ID_BASE + schema_name)


def _describe(node: dict[str, Any], resolver: _Resolver, prop: _Prop | None = None) -> _Prop:
    prop = prop or _Prop()
    if "$ref" in node:
        ref = node["$ref"]
        name = ref.rsplit("/", 1)[-1]
        if name in _VALUE_REFS and prop.value_def is None:
            prop.value_def = name.removeprefix("optional")
            prop.value_def = prop.value_def[0].lower() + prop.value_def[1:]
        resolved = resolver.lookup(ref)
        return _describe(resolved.contents, resolved.resolver, prop)
    for alternative in node.get("anyOf", []):
        _describe(alternative, resolver, prop)
    kinds = node.get("type")
    for kind in [kinds] if isinstance(kinds, str) else kinds or []:
        if kind == "null":
            prop.nullable = True
        else:
            prop.types.add(kind)
    if "enum" in node:
        if None in node["enum"]:
            prop.nullable = True
        prop.enum = [value for value in node["enum"] if value is not None]
    if "properties" in node or "items" in node or "additionalProperties" in node:
        prop.node, prop.resolver = node, resolver
    return prop


# --------------------------------------------------------------------------
# Walking a model
# --------------------------------------------------------------------------


def _unalias(hint: Any) -> Any:
    while isinstance(hint, TypeAliasType) and hint not in (Timestamp, DateValue, DecimalValue):
        hint = hint.__value__
    return hint


def _optional(hint: Any) -> tuple[Any, bool]:
    hint = _unalias(hint)
    if get_origin(hint) in (Union, types.UnionType):
        args = get_args(hint)
        if type(None) in args:
            rest = [arg for arg in args if arg is not type(None)]
            assert len(rest) == 1, hint
            return _unalias(rest[0]), True
    return hint, False


def _check_record(where: str, cls: type, node: dict[str, Any], resolver: _Resolver) -> None:
    properties: dict[str, Any] = node["properties"]
    fields = {field.name: field for field in dataclasses.fields(cls)}
    assert set(fields) == set(properties), f"{where}: fields and schema properties differ"
    assert node.get("additionalProperties") is False, f"{where}: the schema must be closed"

    required = {
        name
        for name, field in fields.items()
        if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
    }
    assert required == set(node.get("required", [])), f"{where}: required fields differ"

    hints = get_type_hints(cls)
    for name, field in fields.items():
        _check_value(f"{where}.{name}", hints[name], _describe(properties[name], resolver), field)


def _check_value(where: str, hint: Any, prop: _Prop, field: dataclasses.Field[Any] | None = None) -> None:
    inner, optional = _optional(hint)
    if optional:
        assert prop.nullable or (field is not None and field.default is None), f"{where}: None but not nullable"
    if field is not None and field.default is None:
        assert optional, f"{where}: defaults to None but is not optional"
    if prop.nullable:
        assert optional, f"{where}: the schema allows null but the field cannot be None"

    if inner in (Timestamp, DateValue, DecimalValue):
        assert _VALUE_REFS[prop.value_def or ""] is inner, f"{where}: {inner} vs schema {prop.value_def}"
        return
    assert prop.value_def is None, f"{where}: the schema says {prop.value_def}"

    origin = get_origin(inner)
    if origin is Literal:
        assert prop.enum is not None and sorted(map(str, get_args(inner))) == sorted(map(str, prop.enum)), where
    elif inner is Any:
        assert not prop.types or prop.types == {"object"}, where
    elif origin is list:
        assert prop.types == {"array"}, where
        (item_hint,) = get_args(inner)
        resolver = prop.resolver
        assert resolver is not None
        _check_value(f"{where}[]", item_hint, _describe(prop.node["items"], resolver))
    elif origin is dict:
        assert prop.types == {"object"}, where
        _, value_hint = get_args(inner)
        additional = prop.node.get("additionalProperties")
        if isinstance(additional, dict) and prop.resolver is not None:
            _check_value(f"{where}{{}}", value_hint, _describe(additional, prop.resolver))
    elif isinstance(inner, type) and dataclasses.is_dataclass(inner):
        assert prop.resolver is not None, where
        _check_record(where, inner, prop.node, prop.resolver)
    else:
        expected = {str: {"string"}, bool: {"boolean"}, int: {"integer"}}[inner]
        assert prop.types == expected, f"{where}: {inner.__name__} vs schema {prop.types}"


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestModelsMatchTheSchemas:
    @pytest.mark.parametrize("record_file", RECORD_FILES, ids=lambda f: f.name)
    def test_each_record_file(self, record_file: RecordFile) -> None:
        _check_record(record_file.name, record_file.model, schemas()[record_file.schema], _resolver(record_file.schema))

    @pytest.mark.parametrize("lookup", LOOKUP_LISTS, ids=lambda lookup: lookup.name)
    def test_each_lookup(self, lookup: Any) -> None:
        resolver = _resolver("lookups.schema.json")
        prop = _describe(schemas()["lookups.schema.json"]["properties"][lookup.name], resolver)
        assert prop.resolver is not None
        item = _describe(prop.node["items"], prop.resolver)
        assert item.resolver is not None
        _check_record(lookup.name, lookup.model, item.node, item.resolver)

    def test_lookups_holds_exactly_the_five_lists(self) -> None:
        assert set(schemas()["lookups.schema.json"]["properties"]) == {lookup.name for lookup in LOOKUP_LISTS}

    def test_public_access(self) -> None:
        schema = schemas()["public_access.schema.json"]
        _check_record("public_access", models.PublicAccess, schema, _resolver("public_access.schema.json"))

    def test_not_carried_over(self) -> None:
        resolver = _resolver("manifest.schema.json")
        prop = _describe(schemas()["manifest.schema.json"]["properties"]["not_carried_over"], resolver)
        assert prop.resolver is not None
        _check_record("not_carried_over", models.NotCarriedOver, prop.node["items"], prop.resolver)

    def test_every_record_file_has_a_schema(self) -> None:
        names = {record_file.schema for record_file in RECORD_FILES}
        assert names | {"manifest.schema.json", "lookups.schema.json", "public_access.schema.json"} | {
            "common.schema.json"
        } == set(schemas())


class TestTheShippedSchemas:
    def test_every_schema_is_valid_draft_2020_12(self) -> None:
        for name, schema in schemas().items():
            Draft202012Validator.check_schema(schema)
            assert schema["$id"] == _ID_BASE + name

    def test_they_are_the_published_ones(self) -> None:
        published = _published_schemas()
        if published is None:
            pytest.skip("No published schemas to compare with: set SPECIVO_CORE_SCHEMAS")
        shipped = schema_files()
        assert sorted(shipped) == sorted(path.name for path in published.glob("*.schema.json"))
        for name, data in shipped.items():
            assert data == (published / name).read_bytes(), f"{name} differs from docs/import-bundle/v1/{name}"

    def test_they_are_valid_json(self) -> None:
        for name, data in schema_files().items():
            assert isinstance(json.loads(data), dict), name

    def test_an_explicit_location_that_does_not_exist_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SPECIVO_CORE_SCHEMAS", str(tmp_path / "nowhere"))
        with pytest.raises(pytest.fail.Exception):
            _published_schemas()

    def test_an_explicit_location_is_used(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SPECIVO_CORE_SCHEMAS", str(tmp_path))
        assert _published_schemas() == tmp_path

    def test_a_required_comparison_without_a_location_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SPECIVO_CORE_SCHEMAS", raising=False)
        monkeypatch.setenv("SPECIVO_REQUIRE_CORE_SCHEMAS", "1")
        with pytest.raises(pytest.fail.Exception):
            _published_schemas()

    def test_an_optional_comparison_without_a_location_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SPECIVO_CORE_SCHEMAS", raising=False)
        monkeypatch.delenv("SPECIVO_REQUIRE_CORE_SCHEMAS", raising=False)
        assert _published_schemas() is None
