"""本机活体验证：启动进程并发起真实 HTTP 请求。"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DESKTOP = ROOT / "desktop"
WORKER = ROOT / "worker"

# Windows 的虚拟环境可执行文件在 Scripts/ 下，Linux/macOS 在 bin/ 下。
# 用虚拟环境而不是系统 Python，是为了不污染全局包，也保证用的是同一套依赖。
VENV_PY = BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
VITE_JS = FRONTEND / "node_modules/vite/bin/vite.js"
ELECTRON_BIN = DESKTOP / "node_modules/electron/dist/electron.exe"

RESULTS: list[tuple[str, str, str]] = []
PROCESSES: list[subprocess.Popen] = []


def record(name: str, ok: bool, detail: str = "", skipped: bool = False) -> None:
    """记一条结果并立刻打印。SKIP 明确表示「没测」，不算通过。"""
    tag = "SKIP" if skipped else ("PASS" if ok else "FAIL")
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def wait_port(port: int, timeout: float = 40.0) -> bool:
    """轮询本机端口，等进程真的开始监听。比 sleep 固定秒数可靠。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def port_busy(port: int) -> bool:
    """开跑前先确认端口没被别的东西占着，否则测到的可能是别人的服务。"""
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def http_get(url: str, timeout: float = 8.0) -> tuple[int, str]:
    """发一个真实的 GET，返回状态码和正文。只用于本机地址。"""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


def spawn(cmd: list[str], cwd: Path, extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    """起一个子进程并登记，最后统一收掉。"""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    PROCESSES.append(process)
    return process


def stop_all() -> None:
    """干净收尾：先温柔终止，超时才强杀，尽量不留孤儿进程。"""
    for process in reversed(PROCESSES):
        if process.poll() is not None:
            continue
        try:
            process.terminate()
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            try:
                process.kill()
            except OSError:
                pass


def read_tail(process: subprocess.Popen, limit: int = 300) -> str:
    """进程提前挂了的话，把它最后几行输出带出来，方便定位。"""
    if process.poll() is None:
        return ""
    try:
        output, _ = process.communicate(timeout=2)
    except (subprocess.TimeoutExpired, ValueError):
        return ""
    return (output or "").strip()[-limit:]


# ------------------------------------------------------------------ 1 后台
def check_backend() -> None:
    """真起后台进程，验证健康检查与接口鉴权。"""
    if not VENV_PY.exists():
        record("后台 FastAPI 能起", False, f"缺虚拟环境 {VENV_PY}，先 pip install -r backend/requirements.txt", skipped=True)
        return
    if port_busy(8000):
        record("后台 FastAPI 能起", False, "8000 端口已被别的程序占用，没法确认测的是本项目", skipped=True)
        return

    # -m uvicorn 而不是直接调 exe，保证用的是虚拟环境里那一套
    process = spawn(
        [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=BACKEND,
        extra_env={"APP_ENV": "test"},
    )
    if not wait_port(8000):
        record("后台 FastAPI 能起", False, "40 秒内没监听 8000；输出：" + read_tail(process))
        return
    record("后台 FastAPI 进程起来了", True, "uvicorn 监听 127.0.0.1:8000")

    try:
        status, body = http_get("http://127.0.0.1:8000/api/health")
        payload = json.loads(body)
        ok = status == 200 and payload.get("status") == "ok" and payload.get("environment") == "test"
        record("GET /api/health 返回正确", ok, f"HTTP {status} · {body.strip()}")
    except Exception as exc:
        record("GET /api/health 返回正确", False, f"请求失败：{exc}")

    # 登录方式开关接口；登录页打开时会先拉它
    try:
        status, body = http_get("http://127.0.0.1:8000/api/auth/methods")
        payload = json.loads(body)
        ok = status == 200 and all(key in payload for key in ("password", "totp", "sms"))
        record("GET /api/auth/methods 返回登录开关", ok, f"HTTP {status} · {body.strip()}")
    except Exception as exc:
        record("GET /api/auth/methods 返回登录开关", False, f"请求失败：{exc}")

    # 没带令牌访问受保护接口必须 401 —— 「服务端每次重新校验」的活体证据。
    # urllib 会把 401 当异常抛出来（HTTP Error 401），要按状态码判，不能当请求失败。
    try:
        status, _body = http_get("http://127.0.0.1:8000/api/auth/me")
        record("无令牌访问 /api/auth/me 被拒", status == 401, f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        record("无令牌访问 /api/auth/me 被拒", exc.code == 401, f"HTTP {exc.code}")
    except Exception as exc:
        record("无令牌访问 /api/auth/me 被拒", False, f"请求失败：{exc}")

    # 第 ④ 步：配置接口真的挂在真进程上，而且同样不吃没令牌的请求。
    try:
        status, _body = http_get("http://127.0.0.1:8000/api/config/datasources")
        record("无令牌访问 /api/config/datasources 被拒", status == 401, f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        record("无令牌访问 /api/config/datasources 被拒", exc.code == 401, f"HTTP {exc.code}")
    except Exception as exc:
        record("无令牌访问 /api/config/datasources 被拒", False, f"请求失败：{exc}")

    # 第 ④ 步扩展：大模型配置路由也真的挂在真进程上（同样要求登录）
    try:
        status, _body = http_get("http://127.0.0.1:8000/api/config/llm/templates")
        record("无令牌访问 /api/config/llm/templates 被拒", status == 401, f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        record("无令牌访问 /api/config/llm/templates 被拒", exc.code == 401, f"HTTP {exc.code}")
    except Exception as exc:
        record("无令牌访问 /api/config/llm/templates 被拒", False, f"请求失败：{exc}")


# ------------------------------------------------------------------ 2 前端
def check_frontend() -> None:
    """真起前端服务，验证登录页能打开。"""
    node = shutil.which("node")
    if not VITE_JS.exists() or node is None:
        record("前端 Vite 能起", False, "缺 frontend/node_modules 或 node，先 npm install", skipped=True)
        return
    if port_busy(5173):
        record("前端 Vite 能起", False, "5173 端口已被占用", skipped=True)
        return

    # 直接调 vite.js 而不是 npm run dev：跨 Windows / Linux 更稳，
    # 也避免 npm.cmd 在 subprocess 里的路径问题。
    process = spawn(
        [node, str(VITE_JS), "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
        cwd=FRONTEND,
    )
    if not wait_port(5173):
        record("前端 Vite 能起", False, "40 秒内没监听 5173；输出：" + read_tail(process))
        return
    record("前端 Vite 进程起来了", True, "dev server 监听 127.0.0.1:5173")

    # 首页：网页版打开的就是这个 HTML
    try:
        status, body = http_get("http://127.0.0.1:5173/")
        record("网页能打开首页 HTML", status == 200 and 'id="app"' in body, f"HTTP {status}，含 Vue 挂载点")
    except Exception as exc:
        record("网页能打开首页 HTML", False, f"请求失败：{exc}")

    # 登录页组件：Vite 会现场把 .vue 编译成 JS，能拿到就说明 SFC 没写错、
    # 页面内容也在（"运维 Agent 平台" 是 Login.vue 里的标题文字）。
    try:
        status, body = http_get("http://127.0.0.1:5173/src/views/Login.vue")
        ok = status == 200 and "运维 Agent 平台" in body and "登录" in body
        record("登录页组件能被真实编译加载", ok, f"HTTP {status}，正文含登录页文案")
    except Exception as exc:
        record("登录页组件能被真实编译加载", False, f"请求失败：{exc}")

    # 构建产物是桌面端打包要复用的那份，两边必须是同一个东西
    dist_index = FRONTEND / "dist/index.html"
    record("前端构建产物存在（桌面复用它）", dist_index.exists(), str(dist_index.relative_to(ROOT)) if dist_index.exists() else "缺 dist，先 npm run build")


# ------------------------------------------------------------------ 3 worker
def check_worker() -> None:
    """验证 worker 进程能起。"""
    try:
        process = subprocess.Popen(
            [sys.executable, str(WORKER / "main.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        )
        time.sleep(0.8)
        process.terminate()
        output, _ = process.communicate(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        record("worker 进程能起", False, f"启动/收尾异常：{exc}")
        return
    ok = "已启动" in output and "就绪" in output
    record("worker 进程能起", ok, "日志含「已启动 / 就绪」" if ok else output.strip()[:200])
    # 第 ⑤ 步：真起的 worker 进程要能把四类假数据读出来（本地文件、零外部连接）
    samples_ok = "样例数据 4 类就绪" in output
    record("worker 能读到本地样例数据", samples_ok,
           "启动日志含「样例数据 4 类就绪」" if samples_ok else output.strip()[-200:])


# ------------------------------------------------------------------ 4 桌面
def check_desktop() -> None:
    """
    桌面壳验证：用自检模式让 Electron 真加载登录页，读回页面标题后自己退出。

    需要 desktop/main.js 里的 OPS_SELFTEST=1 分支配合（不弹窗、验完退出）。
    没有桌面的环境里 Electron 仍可能起不来，这里会如实报 FAIL 而不是跳过。
    """
    result_file = DESKTOP / ".selftest-result.json"
    # 先删掉上次的结果：不能让一次旧的「成功」冒充这次的实测。
    # 这是脚本自己生成的一次性产物，不是用户数据。
    if result_file.exists():
        result_file.unlink()

    if not ELECTRON_BIN.exists():
        record("Electron 桌面壳能打开登录页", False, "缺 electron 二进制，先 npm install / npm rebuild electron", skipped=True)
        return

    env = os.environ.copy()
    # 关键：WorkBuddy 自身是 Electron 应用，环境里会带 ELECTRON_RUN_AS_NODE=1。
    # 子进程一旦继承它，electron.exe 就退化成纯 Node 进程，app / BrowserWindow
    # 全都不存在，表现为一堆 cjs loader 报错。必须显式移除才能按 GUI 程序启动。
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env.update({"OPS_SELFTEST": "1", "ELECTRON_ENABLE_LOGGING": "1"})
    try:
        done = subprocess.run(
            [str(ELECTRON_BIN), str(DESKTOP)],
            cwd=str(DESKTOP),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        output = (done.stdout or "") + (done.stderr or "")
    except subprocess.TimeoutExpired:
        record("Electron 桌面壳能打开登录页", False, "60 秒内没退出，可能卡在窗口创建")
        return
    except OSError as exc:
        record("Electron 桌面壳能打开登录页", False, f"启动失败：{exc}")
        return

    # 优先信结果文件：Windows 上 GUI 程序的 stdout 不一定能被父进程捕获，
    # 只看日志容易误判成失败。日志留着当排障线索。
    result: dict = {}
    if result_file.exists():
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    heading = str(result.get("heading", ""))
    ok = result.get("ok") is True and "运维 Agent 平台" in heading
    if result:
        detail = (
            f"窗口标题={result.get('title', '')} · 页面标题文字={heading} "
            f"· 加载来源={result.get('entry', '')}"
        )
    else:
        # 失败时看报错开头比看结尾有用（根因通常在第一行）
        detail = output.strip()[:400] or "没有拿到任何自检输出"
    record("Electron 桌面壳能打开登录页", ok, detail)


# ------------------------------------------------------------------ 汇总
def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    with_desktop = "--desktop" in sys.argv
    print("=" * 62)
    print("运维 Agent 平台 · 第 ① 步活体验证")
    print("只访问本机 127.0.0.1，不连任何真实环境 / 公网 / 生产")
    print("=" * 62)

    try:
        check_backend()
        check_frontend()
        check_worker()
        if with_desktop:
            check_desktop()
        else:
            record("Electron 桌面壳能打开登录页", False, "本次未加 --desktop，GUI 验证单独跑", skipped=True)
    finally:
        stop_all()

    passed = sum(1 for tag, _, _ in RESULTS if tag == "PASS")
    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    skipped = sum(1 for tag, _, _ in RESULTS if tag == "SKIP")
    print("-" * 62)
    print(f"汇总：PASS {passed} · FAIL {failed} · SKIP {skipped}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
