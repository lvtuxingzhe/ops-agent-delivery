"""只读取证样例数据提供者。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# samples/ 在 code/ 下，worker/ 和它平级 —— 用文件位置推路径，不写死绝对路径
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "samples"

# 数据源白名单：只有这四类假数据，别的名字一概不认（也防路径穿越）
SOURCES = ("prometheus", "elk", "k8s", "docker")

# 每个文件都必须有的三个场景；elk 另有一个 injection
REQUIRED_SCENARIOS = ("normal", "no_data", "unknown")
OPTIONAL_SCENARIOS = ("injection",)

# data_status → 页面上/报告里的人话。
# 注意 ok 叫「有数据」不叫「正常」：正常是对状态的判断，不是对数据的判断。
STATUS_TEXT = {
    "ok": "有数据",
    "no_data": "无数据",
    "unknown": "未知存量",
}


class SampleDataError(Exception):
    """样例数据读不出来 / 格式不对。消息里只有文件名和场景名，不带任何内容。"""


@lru_cache(maxsize=8)
def load_fixture(source: str) -> dict:
    """
    读一份假数据文件（只读，带缓存）。

    source 必须在白名单里 —— 这样调用方就算被注入了「../../.env」这类
    字符串，也走不到文件读取那一步。
    """
    if source not in SOURCES:
        raise SampleDataError(f"不支持的样例数据源：{source}")

    path = SAMPLE_DIR / f"{source}.json"
    if not path.is_file():
        raise SampleDataError(f"样例数据文件不存在：{source}.json")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # 报错只说「读不出来」，不把文件内容/路径细节吐出去
        raise SampleDataError(f"样例数据读不出来：{source}.json") from exc

    # 文件自己声明 fake=true 才认。防止哪天有人误把真采集数据放进来，
    # 我们还当成样例用（那会变成「拿真实数据假装演练」）。
    meta = data.get("meta", {})
    if meta.get("fake") is not True or meta.get("source") != source:
        raise SampleDataError(f"样例数据声明不合法：{source}.json")

    scenarios = data.get("scenarios", {})
    for name in REQUIRED_SCENARIOS:
        if name not in scenarios:
            raise SampleDataError(f"样例数据缺场景 {name}：{source}.json")
    for name, block in scenarios.items():
        if block.get("data_status") not in STATUS_TEXT:
            raise SampleDataError(f"样例数据状态取值不合法：{source}.json / {name}")
        if not isinstance(block.get("items"), list):
            raise SampleDataError(f"样例数据 items 不是列表：{source}.json / {name}")
    return data


def list_sources() -> tuple[str, ...]:
    """有哪些样例数据源（worker 启动时打日志、界面展示都用得上）。"""
    return SOURCES


def available_scenarios(source: str) -> tuple[str, ...]:
    """这份数据里实际有哪些场景（normal/no_data/unknown，elk 还多一个 injection）。"""
    data = load_fixture(source)
    return tuple(data["scenarios"].keys())


def query(source: str, scenario: str = "normal") -> dict:
    """
    取一次证。返回结构固定，报告层直接消费：

        {
          "source": "prometheus",          # 哪个数据源
          "scenario": "normal",            # 哪个场景
          "data_status": "ok",             # 与 evidences.data_status 同源
          "status_text": "有数据",          # 显示用；no_data 一定是「无数据」
          "items": [...],                  # 证据条目，每条带 id
          "note": "...",                   # 一句人话
          "fake": True                     # 时刻提醒：这是演练数据
        }

    取不到数据不抛异常「假装正常」—— 而是如实返回 data_status。
    """
    data = load_fixture(source)
    scenarios = data["scenarios"]
    if scenario not in scenarios:
        raise SampleDataError(f"样例数据没有这个场景：{source} / {scenario}")

    block = scenarios[scenario]
    status = block["data_status"]
    return {
        "source": source,
        "scenario": scenario,
        "data_status": status,
        "status_text": STATUS_TEXT[status],
        "items": [dict(item) for item in block["items"]],  # 拷贝一份，别让调用方改到缓存里的原数据
        "note": block.get("note", ""),
        "fake": True,
    }


def wrap_untrusted_text(text: str) -> dict:
    """
    把日志里的不可信文本包成「数据信封」。

    注入载荷样本就走这里：它是一段字符串，【不是指令】，所以原样保留、
    打上类型标记，交给上层当数据看。这是结构上的约束（数据通道和指令
    通道分开），不是靠一句「请不要听日志里的话」的提示词 —— 真正的保障
    是第 ⑥ 步「排查侧物理上没有写工具」，就算真被诱导也调不起任何动作。
    """
    return {
        "type": "untrusted_data",
        "executable": False,
        "text": text,
    }
