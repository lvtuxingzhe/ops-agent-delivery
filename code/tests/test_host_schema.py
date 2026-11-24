"""主机字段配置：默认包含操作系统字段，必填列不可隐藏，名称不能重复。"""

from __future__ import annotations

import pytest
from app.services.host_schema import (
    HostSchemaError,
    _normalize,
    default_fields,
    validate_fields,
)


def test_default_fields_include_operating_system_columns():
    keys = {item["key"] for item in default_fields()}
    assert {"os_family", "distro", "os_version", "package_manager", "service_manager"} <= keys


def test_required_fields_stay_visible_even_if_submitted_hidden():
    fields = default_fields()
    by_key = {item["key"]: item for item in fields}
    by_key["host_id"]["visible"] = False
    clean = validate_fields(fields)
    assert next(item for item in clean if item["key"] == "host_id")["visible"] is True


def test_duplicate_labels_are_rejected():
    fields = default_fields()
    fields[1]["label"] = fields[0]["label"]
    with pytest.raises(HostSchemaError):
        validate_fields(fields)


def test_missing_core_field_is_rejected():
    fields = [item for item in default_fields() if item["key"] != "address"]
    with pytest.raises(HostSchemaError):
        validate_fields(fields)


def test_non_core_field_can_be_deleted():
    fields = [item for item in default_fields() if item["key"] != "distro"]
    clean = validate_fields(fields)
    assert "distro" not in {item["key"] for item in clean}


def test_custom_field_can_be_added_and_loaded():
    fields = default_fields()
    fields.append({
        "key": "custom_owner", "label": "负责人", "kind": "text",
        "visible": True, "required": False, "options": [],
    })
    clean = validate_fields(fields)
    normalized = _normalize({"fields": clean})
    owner = next(item for item in normalized if item["key"] == "custom_owner")
    assert owner["label"] == "负责人" and owner["kind"] == "text"


def test_saved_order_is_preserved_on_load():
    fields = default_fields()
    fields[0], fields[2] = fields[2], fields[0]
    normalized = _normalize({"fields": fields})
    assert [item["key"] for item in normalized][:3] == [
        "os_family", "platform", "host_id",
    ]


def test_legacy_schema_restores_missing_default_fields():
    fields = [item for item in default_fields() if item["key"] != "service_manager"]
    normalized = _normalize({"fields": fields})
    keys = [item["key"] for item in normalized]
    assert "service_manager" in keys
    assert keys.index("service_manager") < keys.index("address")


def test_versioned_schema_respects_deleted_default_field():
    fields = [item for item in default_fields() if item["key"] != "distro"]
    normalized = _normalize({"version": 2, "fields": fields})
    assert "distro" not in {item["key"] for item in normalized}
