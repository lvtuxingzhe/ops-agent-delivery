"""排查域拆分后的兼容门面回归测试。"""

from __future__ import annotations

import ast
from pathlib import Path

from app.services import investigation_service as facade
from app.services.investigation import agent_runner, execution, public, repository

NETWORK_LIBS = {"socket", "requests", "httpx", "aiohttp", "urllib.request", "paramiko"}


def test_facade_reexports_moved_symbols() -> None:
    assert facade.public_trace is public.public_trace
    assert facade.public_evidence is public.public_evidence
    assert facade.public_knowledge is public.public_knowledge
    assert facade.get_task is repository.get_task
    assert facade.get_report is repository.get_report
    assert facade._task_row_to_response is repository._task_row_to_response
    assert facade._TASK_COLUMNS == repository._TASK_COLUMNS
    assert facade.run_agent is agent_runner.run_agent
    assert facade._agent_env is agent_runner._agent_env
    assert facade.run_exec_phase is execution.run_exec_phase
    assert facade._run_exec_batch is execution._run_exec_batch
    assert facade._expect_ok is execution._expect_ok
    assert facade._failure_summary is execution._failure_summary


def test_facade_keeps_orchestration_and_patch_points() -> None:
    assert facade.run_task.__module__ == facade.__name__
    assert facade.submit_task.__module__ == facade.__name__
    assert facade._ssh_hosts_for_task.__module__ == facade.__name__
    assert callable(facade.AGENT_RUNNER)
    assert facade.ROOT.name == "code"
    assert facade.AGENT_SCRIPT.name == "agent_live.py"
    assert facade.EXEC_SCRIPT.name == "exec_run.py"


def test_investigation_package_has_no_network_client() -> None:
    package_dir = Path(public.__file__).resolve().parent
    imported: set[str] = set()
    for path in package_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not (imported & NETWORK_LIBS)
