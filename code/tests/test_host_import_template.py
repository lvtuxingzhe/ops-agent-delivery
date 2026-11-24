"""主机导入模板：操作系统字段、下拉校验、自定义字段名、密码加密可逆。"""

from __future__ import annotations

from types import SimpleNamespace

from app.api.hosts import _TEMPLATE_HEADERS, _build_import_template, _header_map
from app.services.exec_hosts import seal_password
from app.services.host_schema import default_fields
from cryptography.fernet import Fernet


def test_template_contains_operating_system_fields():
    workbook = _build_import_template(default_fields())
    sheet = workbook["主机导入"]
    headers = [cell.value for cell in sheet[1]]
    assert headers == _TEMPLATE_HEADERS
    assert "操作系统族" in headers
    assert "发行版" in headers
    assert "包管理器" in headers
    assert "服务管理器" in headers


def test_choice_fields_have_dropdown_validation():
    workbook = _build_import_template(default_fields())
    sheet = workbook["主机导入"]
    rules = {item.formula1: item for item in sheet.data_validations.dataValidation}
    assert '"linux,windows"' in rules
    assert '"unknown,debian,rhel,windows"' in rules
    assert '"unknown,apt,dnf,yum,none"' in rules
    assert '"true,false"' in rules
    platform_rule = rules['"linux,windows"']
    assert "B2:B501" in str(platform_rule.sqref)


def test_custom_labels_and_hidden_fields_drive_template():
    fields = default_fields()
    by_key = {item["key"]: item for item in fields}
    by_key["os_family"]["label"] = "操作系统类型"
    by_key["distro"]["visible"] = False
    fields.append({
        "key": "custom_owner", "label": "负责人", "kind": "text",
        "visible": True, "required": False, "options": [], "hint": "自定义字段",
    })
    workbook = _build_import_template(fields)
    headers = [cell.value for cell in workbook["主机导入"][1]]
    assert "操作系统类型" in headers
    assert "发行版" not in headers
    assert "负责人" in headers
    assert len(headers) == 12


def test_import_parser_accepts_custom_field_label():
    fields = default_fields()
    by_key = {item["key"]: item for item in fields}
    by_key["os_family"]["label"] = "操作系统类型"
    mapping = _header_map(("主机ID", "操作系统类型"), fields)
    assert mapping == {0: "host_id", 1: "os_family"}


def test_custom_field_enters_template_and_parser():
    fields = default_fields()
    fields.append({
        "key": "custom_owner", "label": "负责人", "kind": "text",
        "visible": True, "required": False, "options": [], "hint": "自定义字段",
    })
    workbook = _build_import_template(fields)
    headers = [cell.value for cell in workbook["主机导入"][1]]
    assert "负责人" in headers
    mapping = _header_map(("主机ID", "负责人"), fields)
    assert mapping == {0: "host_id", 1: "custom_owner"}


def test_password_is_encrypted_and_can_be_decrypted():
    key = Fernet.generate_key().decode("ascii")
    ciphertext = seal_password(SimpleNamespace(totp_encryption_key=key), "s3cret-pass")
    assert ciphertext and ciphertext != "s3cret-pass"
    assert Fernet(key.encode("ascii")).decrypt(
        ciphertext.encode("ascii"),
    ).decode("utf-8") == "s3cret-pass"
