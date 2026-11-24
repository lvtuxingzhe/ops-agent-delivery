"""知识正文可读性：标签补换行、步骤分隔，且不破坏普通文本。"""

from app.services.knowledge_base_service import format_knowledge_body


def test_formats_inline_labels_and_steps():
    raw = (
        "现象：keepalived 服务 failed。 根因：配置绑定 eth0，本机只有 ens33。 "
        "处理步骤： 1. 核对网卡 2. 重写配置 3. 启动并验证。 结论：配置与环境不匹配。"
    )
    text = format_knowledge_body(raw)
    assert text.startswith("现象：")
    assert "\n根因：" in text
    assert "\n处理步骤：" in text
    assert "\n1. 核对网卡" in text
    assert "\n2. 重写配置" in text
    assert "\n结论：" in text


def test_does_not_split_version_decimal():
    text = format_knowledge_body("现象：服务失败。 根因：配置语法与 1.3.5 版本不匹配。")
    assert "1.3.5" in text
    assert "\n3.5" not in text


def test_plain_document_text_unchanged():
    raw = "第一段内容。\n\n第二段内容。"
    assert format_knowledge_body(raw) == raw
