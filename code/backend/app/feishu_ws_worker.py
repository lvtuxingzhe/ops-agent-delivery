"""飞书应用长连接子进程：接收卡片按钮和群里 @机器人 的命令。

为什么单独进程：
* 飞书 SDK 的 `ws.Client.start()` 会一直阻塞，放进 FastAPI 主进程会拖住请求；
* 独立进程可以在配置变更时直接停掉重启，不需要重启整个后台；
* 子进程不读 App Secret 以外的配置，凭据只通过环境变量传入。

启动方式：
    python -m app.feishu_ws_worker

由 `feishu_app.start_worker()` 在保存配置或后台启动时拉起。
"""

from __future__ import annotations

import json
import logging
import os
import sys

from .core.config import load_dotenv, load_settings
from .services import feishu_app

logger = logging.getLogger("feishu_ws")


def _toast(ok: bool, content: str) -> dict:
    """给飞书客户端的短提示；不把内部错误堆栈带出去。"""
    return {
        "toast": {
            "type": "success" if ok else "error",
            "content": content[:120] or ("已处理" if ok else "处理失败"),
        },
    }


def main() -> int:
    """读取环境变量并启动长连接；没有凭据直接退出，不空转。"""
    load_dotenv()
    settings = load_settings()
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        logger.error("缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET")
        return 2

    import lark_oapi as lark
    from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import (
        P2ImMessageReceiveV1,
    )
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTrigger,
        P2CardActionTriggerResponse,
    )

    try:
        bot_open_id = feishu_app.get_bot_open_id(settings)
    except Exception:
        logger.exception("取机器人 open_id 失败，先只接受斜杠命令")
        bot_open_id = ""

    def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        """飞书回调入口：只做参数抽取，审批逻辑在服务层。"""
        try:
            event = data.event
            if event is None:
                return P2CardActionTriggerResponse(_toast(False, "事件为空"))
            operator = event.operator
            action = event.action
            context = event.context
            event_id = data.header.event_id if data.header else ""
            open_id = str(getattr(operator, "open_id", "") or "")
            value = getattr(action, "value", None) or {}
            message_id = str(getattr(context, "open_message_id", "") or "")
            if not open_id:
                return P2CardActionTriggerResponse(_toast(False, "没有取到操作人"))
            result = feishu_app.handle_card_action(
                settings,
                event_id=event_id,
                open_id=open_id,
                action_value=value,
                message_id=message_id,
            )
            return P2CardActionTriggerResponse(_toast(
                bool(result.get("ok")), str(result.get("toast") or ""),
            ))
        except Exception:
            logger.exception("飞书卡片回调处理失败")
            return P2CardActionTriggerResponse(_toast(False, "处理失败，请回平台查看"))

    def on_message_receive(data: P2ImMessageReceiveV1) -> None:
        """群里 @机器人 的文本命令：解析参数后交给服务层。"""
        try:
            event = data.event
            if event is None or event.message is None or event.sender is None:
                return
            if str(event.sender.sender_type or "") == "app":
                return
            msg = event.message
            sender = event.sender.sender_id
            open_id = str(getattr(sender, "open_id", "") or "")
            if not open_id:
                return
            try:
                content = json.loads(msg.content or "{}")
            except json.JSONDecodeError:
                return
            text = str(content.get("text") or "")
            mentions = [
                str(getattr(item.id, "open_id", "") or "")
                for item in (msg.mentions or [])
                if item and item.id
            ]
            result = feishu_app.handle_group_message(
                settings,
                event_id=str(data.header.event_id if data.header else ""),
                open_id=open_id,
                chat_id=str(msg.chat_id or ""),
                chat_type=str(msg.chat_type or ""),
                text=text,
                mentioned_open_ids=mentions,
                bot_open_id=bot_open_id,
            )
            reply = str(result.get("reply") or "")
            if result.get("handled") and reply:
                if str(msg.chat_type or "") == "group":
                    reply = f'<at user_id="{open_id}"></at> {reply}'
                feishu_app.send_text(settings, chat_id=str(msg.chat_id or ""), text=reply)
        except Exception:
            logger.exception("飞书群消息处理失败")

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_card_action_trigger(on_card_action)
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )
    client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    logger.info("长连接启动 app_id=%s", app_id)
    client.start()
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    sys.exit(main())
