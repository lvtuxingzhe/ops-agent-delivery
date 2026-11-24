"""
审计检索接口 —— 只给管理员 / 审计员看审计记录。

`GET /api/audit/events` 的参数全部是筛选条件，不做写操作。查询走
`audit_query.query_events()`，它内部用审计只读账号；接口层只负责
角色门槛、HTTP 参数和错误话术。

为什么不返回整张表
------------------
审计记录会一直增长；一次全量返回既慢又把敏感操作细节一次性铺到浏览器。
默认 50 条、上限 200 条，前端翻页或缩小筛选范围。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..services import audit_export, audit_query
from .auth import get_current_user, require_roles

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/actors")
def list_actors(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """返回审计里出现过的操作人，供筛选框做搜索与多选。"""
    require_roles(user, {"admin", "auditor"})
    try:
        return audit_query.list_actors(request.app.state.settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="审计查询暂不可用") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="审计查询暂不可用") from exc


@router.get("/events/export")
def export_events(
    request: Request,
    task_id: str = Query("", max_length=64),
    trace_id: str = Query("", max_length=128),
    event_type: str = Query("", max_length=128),
    actor: str = Query("", max_length=512),
    q: str = Query("", max_length=128),
    since: str = Query("", max_length=40),
    until: str = Query("", max_length=40),
    user: dict = Depends(get_current_user),
):
    """按当前筛选条件导出全量 CSV；管理员 / 审计员专用。"""
    require_roles(user, {"admin", "auditor"})
    try:
        result = audit_export.export_events(
            request.app.state.settings,
            task_id=task_id, trace_id=trace_id, event_type=event_type,
            actor=actor, q=q, since=since, until=until,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="审计查询暂不可用") from exc
    except Exception as exc:  # noqa: BLE001 - 接口层保底，异常类型只进服务端日志
        raise HTTPException(status_code=503, detail="审计查询暂不可用") from exc

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"audit-events-{stamp}.csv"
    if result["truncated"]:
        filename = f"audit-events-{stamp}-truncated.csv"
    return Response(
        content=result["content"],
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Rows": str(result["count"]),
            "X-Export-Truncated": "1" if result["truncated"] else "0",
        },
    )



@router.get("/events")
def list_events(
    request: Request,
    task_id: str = Query("", max_length=64),
    trace_id: str = Query("", max_length=128),
    event_type: str = Query("", max_length=128),
    actor: str = Query("", max_length=512),
    q: str = Query("", max_length=128),
    since: str = Query("", max_length=40),
    until: str = Query("", max_length=40),
    limit: int = Query(audit_query.DEFAULT_LIMIT, ge=1, le=audit_query.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """按条件检索审计流水；只读，不写任何审计事件。"""
    require_roles(user, {"admin", "auditor"})
    try:
        return audit_query.query_events(
            request.app.state.settings,
            task_id=task_id, trace_id=trace_id, event_type=event_type,
            actor=actor, q=q, since=since, until=until,
            limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # 只读连接没配好时明确告诉调用方“查询不可用”，不回数据库细节
        raise HTTPException(status_code=503, detail="审计查询暂不可用") from exc
    except Exception as exc:  # noqa: BLE001 - 接口层保底，异常类型只进服务端日志
        raise HTTPException(status_code=503, detail="审计查询暂不可用") from exc


__all__ = ["router"]
