"""后台进程入口（FastAPI）。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import (
    admin,
    alerts,
    audit,
    auth,
    config,
    deployments,
    health,
    hosts,
    knowledge,
    llm_configs,
    notifications,
    overview,
    public,
    tasks,
)
from .core import ratelimit, web
from .core.config import load_dotenv, load_settings, validate_runtime
from .services.login_assets_service import upload_dir

APPROVAL_SLA_INTERVAL_S = 60


async def _approval_sla_loop(settings) -> None:
    """后台每分钟扫一次超时审批；异常只记日志，不拖垮 FastAPI。"""
    logger = logging.getLogger(__name__)
    from .services import approval_sla

    while True:
        try:
            await asyncio.to_thread(approval_sla.sweep_approval_timeouts, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("审批超时提醒扫描失败")
        await asyncio.sleep(APPROVAL_SLA_INTERVAL_S)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """应用生命周期：启动时拉起审批 SLA 扫描，停机时取消。"""
    task = asyncio.create_task(
        _approval_sla_loop(app.state.settings),
        name="approval-sla-loop",
    )
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    """把应用装配起来。以后加功能 = 在这里多挂一个 router。"""
    # 便利：code/.env 存在就读它（已有环境变量优先，绝不覆盖）。
    # 生产环境由部署平台注入环境变量、机器上没有 .env 文件，
    # load_dotenv 会直接跳过 —— 所以这行对生产零影响。
    load_dotenv()
    settings = load_settings()
    # 启动期硬校验：危险组合（real 模式 + 测试短信通道）直接拒绝启动。
    validate_runtime(settings)

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger = logging.getLogger(__name__)

    app = FastAPI(
        title=settings.app_name,
        description="运维 Agent 平台后台 · 第 ④ 步（健康检查 + 登录认证 + 配置页 + 大模型自配置）",
        version="0.4.1",
        lifespan=_lifespan,
    )

    # 配置统一挂在 app.state 上，接口要用就从 request.app.state 取。
    # 不让各模块自己再读一遍环境变量 —— 读两遍就可能读出两个不一样的值。
    app.state.settings = settings

    # 请求 ID + 全局异常处理：每个响应都带 X-Request-ID，
    # 未处理异常统一回 500（只给通用文案，堆栈只进服务端日志）。
    web.install(app, logger)

    # 限流：登录与任务提交各一档。先挂限流，再挂 CORS ——
    # 后加的中间件在外层，CORS 在最外层才能给 429 补上跨域头。
    ratelimit.install(app, settings, logger)

    # 只放行配置里的前端地址，不使用 "*" 通配。
    # 桌面端加载的是同一份前端，同样走这个白名单。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    # 登录页公开配置：不需要令牌，只返回背景图/应用名/运行模式。
    app.include_router(public.router, prefix="/api")
    # 登录、TOTP 绑定、短信与自审批清单。router 自带 /auth 前缀。
    app.include_router(auth.router, prefix="/api")
    # 用户管理：仅 admin，使用独立的 ops_user_admin 连接。
    app.include_router(admin.router, prefix="/api")
    # 配置页：数据源 CRUD、启停、健康检查与平台开关。自带 /config 前缀。
    app.include_router(config.router, prefix="/api")
    # 通用大模型用户自配置。自带 /config/llm 前缀。
    # 放在 config 之后：两条路径不重叠，/config/datasources 与 /config/llm/* 各走各的。
    app.include_router(llm_configs.router, prefix="/api")
    # 排查任务接口（提交 / 查状态 / 查报告）。自带 /tasks 前缀。
    app.include_router(tasks.router, prefix="/api")
    # 部署任务接口：模板 / 方案指纹 / 结构化确认 / 审批执行。自带 /deployments 前缀。
    app.include_router(deployments.router, prefix="/api")
    # 独立知识库：部署经验 / 排查报告 / 手工知识 + RAG 检索入口。自带 /knowledge 前缀。
    app.include_router(knowledge.router, prefix="/api")
    # Alertmanager Webhook：Bearer 鉴权，只建只读排查。
    app.include_router(alerts.router, prefix="/api")
    app.include_router(notifications.router, prefix="/api")
    app.include_router(overview.router, prefix="/api")
    # 审计检索：只读审计记录，管理员 / 审计员可用。自带 /audit 前缀。
    app.include_router(audit.router, prefix="/api")
    # 执行主机清单：只存连接元数据，不接收密码。自带 /hosts 前缀。
    app.include_router(hosts.router, prefix="/api")

    # 登录背景图：公开静态资源。目录不存在时先建出来，
    # 只挂 /api/assets，和接口前缀同一层，前端不用另配反代。
    app.mount(
        "/api/assets",
        StaticFiles(directory=str(upload_dir())),
        name="login-assets",
    )

    # 日志里只打环境和监听地址，不打连接串、不打账号。
    logger.info(
        "后台装配完成：environment=%s，监听 %s:%s",
        settings.environment,
        settings.host,
        settings.port,
    )
    return app


# uvicorn 启动命令：uvicorn app.main:app （在 backend/ 目录下执行）
app = create_app()
