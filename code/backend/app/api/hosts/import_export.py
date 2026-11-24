"""主机 Excel 模板与导入接口。模板列由 host_schema 驱动。"""

from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.api.auth import client_kind, get_current_user, require_roles
from app.services import exec_hosts, host_schema
from app.services.exec_hosts import HostError

router = APIRouter()

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 500

# 表头别名：允许中文模板，也允许直接用接口字段名，减少手工填表摩擦。
_COLUMN_ALIASES = {
    "主机id": "host_id", "host_id": "host_id",
    "平台": "platform", "platform": "platform",
    "操作系统族": "os_family", "os_family": "os_family",
    "发行版": "distro", "distro": "distro",
    "版本": "os_version", "os_version": "os_version",
    "包管理器": "package_manager", "package_manager": "package_manager",
    "服务管理器": "service_manager", "service_manager": "service_manager",
    "地址": "address", "address": "address",
    "端口": "port", "port": "port",
    "账号": "user", "用户": "user", "user": "user",
    "密码": "password", "登录密码": "password", "password": "password",
    "启用": "enabled", "enabled": "enabled",
}
# 新模板默认列由 host_schema 决定；这里只保留默认快照，测试和回退用。
_TEMPLATE_HEADERS = [
    "主机ID", "平台", "操作系统族", "发行版", "版本", "包管理器",
    "服务管理器", "地址", "端口", "账号", "密码", "启用",
]

_FIELD_WIDTHS = {
    "host_id": 20, "platform": 12, "os_family": 16, "distro": 20,
    "os_version": 12, "package_manager": 16, "service_manager": 20,
    "address": 24, "port": 10, "user": 16, "password": 28, "enabled": 10,
}


def _build_import_template(fields: list[dict[str, Any]] | None = None) -> Workbook:
    """
    生成导入模板：列、字段名、显示与下拉都由 host_schema 驱动。

    下拉用 DataValidation(type="list")，直接写进 xlsx；用户即使不点下拉，
    手填非法值也会被 Excel 拦下。后端仍会再校验一遍，模板不是唯一防线。
    """
    fields = fields or host_schema.default_fields()
    visible = [item for item in fields if item.get("visible", True)]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "主机导入"
    sheet.append([item["label"] for item in visible])
    for index, item in enumerate(visible, start=1):
        width = _FIELD_WIDTHS.get(item["key"], 18)
        sheet.column_dimensions[get_column_letter(index)].width = width

    header_fill = PatternFill("solid", fgColor="E8EEF7")
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill

    # 所有非下拉列按文本处理，避免纯数字密码被 Excel 转成科学计数。
    for index, item in enumerate(visible, start=1):
        if item.get("options"):
            continue
        letter = get_column_letter(index)
        for row in range(2, MAX_IMPORT_ROWS + 2):
            sheet[f"{letter}{row}"].number_format = "@"

    for index, item in enumerate(visible, start=1):
        options = item.get("options") or []
        if not options:
            continue
        letter = get_column_letter(index)
        rule = DataValidation(
            type="list", formula1='"' + ",".join(options) + '"',
            allow_blank=not bool(item.get("required")),
            showErrorMessage=True, errorTitle=f"{item['label']}取值不合法",
            error=f"{item['label']}只能选择：" + " / ".join(options),
            promptTitle=item["label"], prompt=item.get("hint") or "",
        )
        sheet.add_data_validation(rule)
        rule.add(f"{letter}2:{letter}{MAX_IMPORT_ROWS + 1}")
    sheet.freeze_panes = "A2"

    # 模板不预置数据行：避免用户忘删示例行，把示例主机当成真实主机导入。
    note = workbook.create_sheet("填写说明")
    note.append(["字段", "说明"])
    required = [item["label"] for item in visible if item.get("required")]
    note.append(["必填", "、".join(required)])
    for item in visible:
        detail = item.get("hint") or ""
        if item["key"] == "host_id":
            detail += "；已存在的主机默认跳过"
        elif item["key"] == "password":
            detail += "；页面只显示是否已配置，不显示明文"
        elif item["key"] == "enabled":
            detail += "；启用前必须已配置登录密码"
        note.append([item["label"], detail])
    note.append(["重复主机", "按主机ID判断，已存在则整行跳过，不覆盖原记录"])
    note.append(["导入上限", "单个文件不超过 5MB，单次最多导入 500 行；超过部分不会写入"])
    note.append(["填写方式", "从“主机导入”工作表第 2 行开始逐行填写；空白行会自动跳过"])
    note.column_dimensions["A"].width = 18
    note.column_dimensions["B"].width = 86
    return workbook


@router.get("/import-template")
def download_import_template(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """下载 xlsx 模板；只对管理员开放，模板本身不含任何凭据。"""
    require_roles(user, {"admin"})
    fields = host_schema.load_fields(request.app.state.settings)
    workbook = _build_import_template(fields)
    output = io.BytesIO()
    workbook.save(output)
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="host-import-template.xlsx"'},
    )


def _text(value: Any) -> str:
    """Excel 单元格转字符串；None → 空串，数字不带 .0。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _truthy(value: Any) -> bool:
    """导入模板里的启用列：兼容 true/false、1/0、是/否。"""
    if isinstance(value, bool):
        return value
    text = _text(value).lower()
    return text in {"true", "1", "yes", "y", "是", "启用", "开"}


def _header_map(
    row: tuple[Any, ...], fields: list[dict[str, Any]] | None = None,
) -> dict[int, str]:
    """表头 → 字段 key：先认配置后的自定义名称，再回落到内置别名。"""
    custom = {
        _text(item["label"]).lower().replace(" ", ""): item["key"]
        for item in (fields or [])
        if item.get("visible", True)
    }
    mapping: dict[int, str] = {}
    for index, value in enumerate(row):
        normalized = _text(value).lower().replace(" ", "")
        key = custom.get(normalized) or _COLUMN_ALIASES.get(normalized)
        if key:
            mapping[index] = key
    return mapping


@router.post("/import")
async def import_hosts(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    source: str = Depends(client_kind),
):
    """导入 xlsx：逐行校验，已存在主机跳过，失败行单独报告。"""
    require_roles(user, {"admin"})
    filename = (file.filename or "").lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="只支持 .xlsx 文件，请先下载模板填写")

    data = await file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 5MB，请拆分后再导入")
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl 对坏文件抛多种异常，统一给用户可读提示
        raise HTTPException(status_code=400, detail="无法解析这个 xlsx，请用模板重新填写") from exc

    sheet = workbook["主机导入"] if "主机导入" in workbook.sheetnames else workbook.active
    rows = sheet.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration as exc:
        raise HTTPException(status_code=400, detail="表格是空的，请用模板填写") from exc
    fields = host_schema.load_fields(request.app.state.settings)
    columns = _header_map(header_row, fields)
    required_keys = {
        item["key"] for item in fields
        if item.get("required") and item.get("visible", True)
    }
    missing = required_keys - set(columns.values())
    if missing:
        labels = {item["key"]: item["label"] for item in fields}
        raise HTTPException(
            status_code=400,
            detail="缺少必填列：" + "、".join(
                labels.get(item, item) for item in sorted(missing)
            ),
        )

    existing = {item["id"] for item in exec_hosts.list_hosts(request.app.state.settings)}
    created = 0
    skipped = 0
    failed = 0
    errors: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for row_number, row in enumerate(rows, start=2):
        if row_number - 1 > MAX_IMPORT_ROWS:
            errors.append({"row": row_number, "host_id": "", "error": "超过单次 500 行上限，后续行未导入"})
            failed += 1
            break
        values = {
            key: row[index] if index < len(row) else None
            for index, key in columns.items()
        }
        host_id = _text(values.get("host_id")).lower()
        # 整行空白直接跳过，不当作错误，方便模板尾部留空。
        if not host_id and not any(_text(value) for value in values.values()):
            continue
        if not host_id:
            failed += 1
            errors.append({"row": row_number, "host_id": "", "error": "主机ID 不能为空"})
            continue
        if host_id in existing or host_id in seen_ids:
            skipped += 1
            continue
        seen_ids.add(host_id)

        payload = {
            "platform": _text(values.get("platform")),
            "os_family": _text(values.get("os_family")),
            "distro": _text(values.get("distro")),
            "os_version": _text(values.get("os_version")),
            "package_manager": _text(values.get("package_manager")),
            "service_manager": _text(values.get("service_manager")),
            "address": _text(values.get("address")),
            "port": _text(values.get("port")) or 22,
            "user": _text(values.get("user")),
            "password": _text(values.get("password")),
            "enabled": _truthy(values.get("enabled")),
            "custom_fields": {
                item["key"]: _text(values.get(item["key"]))
                for item in fields if str(item.get("key", "")).startswith("custom_")
            },
        }
        missing_custom = next(
            (
                item for item in fields
                if str(item.get("key", "")).startswith("custom_")
                and item.get("required")
                and not payload["custom_fields"].get(item["key"])
            ),
            None,
        )
        if missing_custom:
            failed += 1
            errors.append({
                "row": row_number, "host_id": host_id,
                "error": f"{missing_custom['label']} 不能为空",
            })
            continue
        if not payload["password"]:
            failed += 1
            errors.append({
                "row": row_number, "host_id": host_id,
                "error": "登录密码不能为空",
            })
            continue
        try:
            exec_hosts.upsert_host(
                request.app.state.settings, host_id, payload,
                actor_id=int(user["id"]), client_source=source,
            )
            created += 1
            existing.add(host_id)
        except HostError as exc:
            failed += 1
            errors.append({"row": row_number, "host_id": host_id, "error": str(exc)})
        except Exception:
            failed += 1
            errors.append({"row": row_number, "host_id": host_id, "error": "写入失败，请检查后台日志"})

    return {
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "errors": errors[:50],
    }


__all__ = [
    "MAX_IMPORT_BYTES",
    "MAX_IMPORT_ROWS",
    "_TEMPLATE_HEADERS",
    "_build_import_template",
    "_header_map",
    "router",
]
