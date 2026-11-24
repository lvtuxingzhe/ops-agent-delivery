"""把一台目标机的主机公钥写入集中 known_hosts。

安全边界：
* 只做 SSH 握手取候选公钥，不发送登录密码；
* 不带 `--expected-fingerprint` 时只打印 `CANDIDATE=...` 并返回 2，不写入；
* 只有候选指纹与 `--expected-fingerprint` 一致时才写入；
* 已登记主机公钥发生变化时拒绝，不提供“覆盖”开关。

用法（在 code 目录）：
    python backend/scripts/pin_ssh_host_key.py --host-id vm-150
    python backend/scripts/pin_ssh_host_key.py --host-id vm-150 \
        --expected-fingerprint SHA256:xxxxx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
BACKEND = CODE / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.services import exec_hosts  # noqa: E402


def main() -> int:
    load_dotenv()
    settings = load_settings()
    parser = argparse.ArgumentParser(description="固定 SSH 主机公钥")
    parser.add_argument("--host-id", required=True)
    parser.add_argument("--expected-fingerprint", default="")
    parser.add_argument("--actor-id", type=int, default=0)
    args = parser.parse_args()

    try:
        probe = exec_hosts.probe_host_key(settings, args.host_id)
    except exec_hosts.HostError as exc:
        print(f"PROBE_FAILED={exc}")
        return 2

    actual = probe["fingerprint"]
    if not args.expected_fingerprint.strip():
        print(f"CANDIDATE={actual}")
        print(f"KEY_TYPE={probe['key_type']}")
        print("请到目标机控制台核对后，带 --expected-fingerprint 重跑；本次未写入")
        return 2
    if probe["status"] == "mismatch":
        print(f"FINGERPRINT_MISMATCH stored={probe['stored']} actual={actual}")
        print("已登记主机公钥发生变化，先排查，不覆盖")
        return 2
    if probe["status"] == "registered":
        print(f"ALREADY_REGISTERED={args.host_id} FINGERPRINT={actual}")
        return 0
    if actual != args.expected_fingerprint.strip():
        print(f"FINGERPRINT_MISMATCH expected={args.expected_fingerprint} actual={actual}")
        return 2

    try:
        exec_hosts.confirm_host_key(
            settings, args.host_id, actual,
            actor_id=args.actor_id, source="verified", client_source="cli",
        )
    except exec_hosts.HostError as exc:
        print(f"WRITE_FAILED={exc}")
        return 2
    print(f"PINNED={args.host_id} FINGERPRINT={actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
