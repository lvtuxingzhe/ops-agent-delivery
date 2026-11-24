"""
[已废弃] 历史 SSH 真机自检脚本。

当前主机接入走密码认证（`password_enc`）与平台集中 `known_hosts` 校验；
首次确认用 `backend/scripts/pin_ssh_host_key.py` 或「主机」页的「确认密钥」，
已登记后公钥变化会硬失败。本脚本仍按旧清单字段（`password_env`/`key_path`/
`known_hosts`）读取，仅用于排查旧环境，不要作为现行接入验收依据。

原用途：SSH 远程执行真机自检（S4-12）：许可绑主机 → 执行器 → VMware 主机。

怎么用（在 code/ 目录下）：
    python backend/scripts/selftest_exec_ssh.py

前置：
  * `configure_exec_host.py` 已登记 vm-150；
  * `.env` 里有 `EXEC_SSH_PASSWORD`（密码值不打印、不写入数据库）。

测什么：
  * 主机清单能读到 vm-150；
  * 许可按 `host=vm-150` 签发后，执行器能真的 SSH 到目标并执行 `uname -a`；
  * 同一许可换到 `host=local` 验签必须失败（防「批准 A 跑 B」）；
  * 输出里含目标内核信息，证明不是本机假装执行。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
BACKEND = CODE / "backend"
WORKER = CODE / "worker" / "exec_run.py"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(CODE / "packages"))
sys.path.insert(0, str(CODE / "worker"))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.services import exec_hosts  # noqa: E402
from ops_contract.permits import (  # noqa: E402
    fingerprint,
    issue_permit,
    verify_permit,
)

HOST_ID = "vm-150"
RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _key_fingerprint(pub_line: str) -> str:
    """从 `keytype base64 [comment]` 算 SHA256 指纹。"""
    parts = pub_line.strip().split()
    if len(parts) < 2:
        return ""
    digest = hashlib.sha256(base64.b64decode(parts[1])).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _pinned_fingerprint(path: Path, address: str) -> str:
    """读 known_hosts 里该主机的第一条 ed25519 记录并算指纹。"""
    for line in path.read_text(encoding="utf-8").splitlines():
        if address in line and "ssh-ed25519" in line:
            # known_hosts 是 `host keytype base64`，去掉 host 再算
            return _key_fingerprint(" ".join(line.split()[1:]))
    return ""


def main() -> int:
    load_dotenv()
    settings = load_settings()
    try:
        host = exec_hosts.resolve_host(settings, HOST_ID, "linux")
    except exec_hosts.HostError as exc:
        record("主机清单可读", False, str(exc))
        return 1
    record("主机清单可读", True,
           f"{host['address']}:{host['port']} {host['user']}")

    if host["password_env"]:
        if not os.environ.get(host["password_env"]):
            record("SSH 凭据已注入", False, f"缺少环境变量 {host['password_env']}")
            return 1
        auth_mode = f"password:{host['password_env']}"
    elif host["key_path"]:
        key_file = (CODE / host["key_path"]).resolve()
        if not key_file.exists():
            record("SSH 凭据已注入", False, f"密钥不存在：{host['key_path']}")
            return 1
        auth_mode = f"key:{host['key_path']}"
    else:
        record("SSH 凭据已注入", False, "既没有 password_env，也没有 key_path")
        return 1
    record("SSH 凭据已注入", True, auth_mode)

    known = (CODE / host["known_hosts"]).resolve()
    if not known.exists():
        record("主机指纹已固定", False, f"known_hosts 不存在：{host['known_hosts']}")
        return 1
    record("主机指纹已固定", True, host["known_hosts"])

    # 没有登记指纹时必须直接拒，不能回退到「首次信任」
    target = exec_hosts.target_of(host)
    bad_target = {**target, "known_hosts": "var/ssh/known_hosts_missing"}
    from tools.ssh_exec import run_ssh  # noqa: PLC0415

    untrusted = run_ssh(bad_target, ["true"], timeout_s=3)
    record("未登记指纹的主机被拒", untrusted.get("reason") == "ssh_host_key_untrusted",
           str(untrusted.get("reason")))

    # 目标机自己报告的主机公钥，必须和 known_hosts 里固定的指纹一致
    remote = run_ssh(target, ["cat", "/etc/ssh/ssh_host_ed25519_key.pub"], timeout_s=5)
    pinned = _pinned_fingerprint(known, host["address"])
    reported = _key_fingerprint(str(remote.get("stdout") or ""))
    record("指纹与本机公钥一致", bool(pinned) and pinned == reported,
           f"pinned={pinned} reported={reported} "
           f"exit={remote.get('exit_code')} err={str(remote.get('stderr'))[:60]}")

    secret = secrets.token_hex(32)
    os.environ["EXEC_PERMIT_SECRET"] = secret
    task_id = "selftest-ssh-" + secrets.token_hex(4)
    argv = ["uname", "-a"]
    token = issue_permit(secret, task_id=task_id, platform="linux",
                         argv=argv, host=HOST_ID)

    ok, reason, _scope = verify_permit(token, task_id=task_id,
                                       platform="linux", argv=argv,
                                       host=HOST_ID)
    record("许可绑定 vm-150 可验签", ok, reason or "ok")

    ok_other, reason_other, _ = verify_permit(
        token, task_id=task_id, platform="linux", argv=argv, host="local",
    )
    record("许可换到 local 必须失败", not ok_other,
           reason_other or "错误地通过了")

    payload = {
        "task_id": task_id, "platform": "linux", "host": HOST_ID,
        "target": exec_hosts.target_of(host),
        "permits": {fingerprint("linux", argv, HOST_ID): token},
        "ops": [{"argv": argv, "scope": "read"}],
    }
    completed = subprocess.run(
        [sys.executable, str(WORKER)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8",
        timeout=120, env=os.environ.copy(),
    )
    if completed.returncode != 0:
        record("执行器返回成功", False,
               (completed.stderr or completed.stdout or "无输出")[-400:])
        return 1
    line = completed.stdout.strip().splitlines()[-1]
    output = json.loads(line)
    result = (output.get("results") or [{}])[0]
    record("执行器返回成功", bool(output.get("ok")),
           f"exit={result.get('exit_code')} host={result.get('host')}")
    stdout = str(result.get("stdout") or "")
    record("远程内核信息已返回",
           "Linux" in stdout and "x86_64" in stdout,
           stdout.strip()[:160] or "空输出")

    failed = [row for row in RESULTS if row[0] == "FAIL"]
    print(f"\nPASS {len(RESULTS) - len(failed)}/{len(RESULTS)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
