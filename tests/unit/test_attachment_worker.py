"""attachment_worker unit tests (plan 04 §2.5, batch 6).

验证子进程隔离三要素：环境白名单（密钥不随进程继承）、请求-响应协议
（字节级 UTF-8）、Job Object 内存硬限制与硬性超时杀树。全部离线。
"""

from __future__ import annotations

import base64
import io
import json
import sys
import types

import pytest

from backend.app.research.attachment_worker import (
    PROJECT_ROOT,
    build_clean_child_env,
    main as worker_main,
    run_isolated_parse,
    spawn_with_job,
)
from backend.app.settings import Settings


def _request(text: str, *, extension: str = "txt", mime_type: str = "text/plain",
             limits: dict | None = None) -> dict:
    return {"data_b64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "extension": extension, "mime_type": mime_type, "limits": limits or {}}


def _run_main_with(payload: bytes) -> dict:
    """进程内直接调 worker.main()：替换 sys.stdin/sys.stdout 捕获协议输出。"""
    fake_stdin = types.SimpleNamespace(buffer=io.BytesIO(payload))
    fake_stdout = types.SimpleNamespace(buffer=io.BytesIO())
    original = (sys.stdin, sys.stdout)
    sys.stdin, sys.stdout = fake_stdin, fake_stdout
    try:
        assert worker_main([]) == 0
    finally:
        sys.stdin, sys.stdout = original
    return json.loads(fake_stdout.buffer.getvalue().decode("utf-8"))


def test_settings_default_requires_subprocess(tmp_path):
    """生产默认必须走子进程隔离；False 只允许测试显式降级。"""
    assert Settings(project_root=tmp_path).attachment_parse_subprocess is True


def test_clean_env_never_inherits_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("FINSAIGHT_ENCRYPTION_KEY", "super-secret")
    monkeypatch.setenv("HEXIN_TOKEN", "t")
    monkeypatch.setenv("DATABASE_PASSWORD", "p")
    env = build_clean_child_env(str(tmp_path))
    assert "FINSAIGHT_ENCRYPTION_KEY" not in env
    assert "HEXIN_TOKEN" not in env
    assert "DATABASE_PASSWORD" not in env
    assert env["PYTHONPATH"] == str(tmp_path)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "SYSTEMROOT" in env  # Windows Python 运行所需最小集合


def test_main_roundtrip_keeps_chinese_text():
    response = _run_main_with(
        json.dumps(_request("好的内容第一行\n第二行 123"), ensure_ascii=False).encode("utf-8"))
    assert response["ok"] is True
    assert response["parsed"]["text"] == "好的内容第一行\n第二行 123"
    assert response["parsed"]["parse_method"] == "plain_text"


def test_main_reports_structured_error_for_unsupported_type():
    response = _run_main_with(json.dumps(_request("MZ", extension="exe")).encode("utf-8"))
    assert response["ok"] is False
    assert response["code"] == "unsupported_type"
    assert "docx 请另存" in response["message"] or "TXT/Markdown" in response["message"]


def test_run_isolated_end_to_end_real_child():
    """真实拉起一次性子进程：文本经 stdin 进、JSON 出，中文不乱码。"""
    response = run_isolated_parse(
        _request("会议要点：资本开支维持克制。"), timeout_seconds=60.0)
    assert response["ok"] is True, response
    assert "资本开支" in response["parsed"]["text"]
    assert response["parsed"]["parse_method"] == "plain_text"


def test_child_env_has_no_surviving_secrets(monkeypatch):
    """父进程白名单 + 子进程 --print-env 双重验证：密钥变量一个都不剩。"""
    monkeypatch.setenv("FINSAIGHT_ENCRYPTION_KEY", "test-value")
    monkeypatch.setenv("MY_SECRET_TOKEN", "test-value")
    code, stdout, _stderr = spawn_with_job(
        [sys.executable, "-m", "backend.app.research.attachment_worker", "--print-env"],
        cwd=PROJECT_ROOT, timeout_seconds=60.0)
    assert code == 0
    response = json.loads(stdout.decode("utf-8"))
    assert response["secret_env"] == []


@pytest.mark.skipif(sys.platform != "win32", reason="pywin32 Job Object 仅 Windows")
def test_job_object_memory_limit_kills_hog():
    """内存硬限制：子进程贪吃内存被 Job Object 终止，父进程不受影响。"""
    code, _stdout, stderr = spawn_with_job(
        [sys.executable, "-c", "buf = []\nwhile True:\n    buf.append(b'x' * 1048576)"],
        cwd=PROJECT_ROOT, timeout_seconds=60.0, memory_limit_mb=128)
    assert code != 0  # MemoryError 退出或被 Job Object 终止


@pytest.mark.skipif(sys.platform != "win32", reason="超时杀树走 Job Object 路径")
def test_timeout_terminates_whole_tree():
    with pytest.raises(TimeoutError):
        spawn_with_job(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=PROJECT_ROOT, timeout_seconds=1.0)


def test_parse_isolated_maps_timeout_to_budget_error(tmp_path, monkeypatch):
    """父进程把子进程硬性超时映射为 time_budget_exceeded 结构化错误。"""
    from backend.app.research import attachments as attachments_module
    from backend.app.research.attachment_parsers import AttachmentParseError
    from backend.app.research.attachments import AttachmentStore

    def _fail_timeout(request, *, timeout_seconds):
        assert timeout_seconds > 0
        return {"ok": False, "code": "time_budget_exceeded",
                "message": "附件解析子进程超过硬性时间上限，已强制终止；请拆分文件后重新上传。"}

    # attachments 模块以 from-import 绑定函数名，必须打补丁到它的命名空间。
    monkeypatch.setattr(attachments_module, "run_isolated_parse", _fail_timeout)
    settings = Settings(project_root=tmp_path)
    store = AttachmentStore(repository=None, settings=settings)
    with pytest.raises(AttachmentParseError) as info:
        store._parse_isolated(b"x", extension="txt", mime_type="text/plain")
    assert info.value.code == "time_budget_exceeded"
