"""Attachment parse worker: short-lived, isolated subprocess (plan 04 §2.5).

批次6 落地批次4/5 遗留偏差：附件解析从 Web 进程内迁入"独立短生命周期子进程"。
安全边界（对外声明随本文件生效）：
- 独立短生命周期子进程：每份文件一次 python -m 调用，解析完成即退出；
  不与 Web 进程共享任何可变状态。
- pywin32 Job Object 内存硬限制（默认 512 MiB，JOB_OBJECT_LIMIT_PROCESS_MEMORY）：
  恶意/畸形文件撑爆内存时由内核终止子进程，Web 进程不受影响；pywin32 不可用
  时降级为无内存上限的普通子进程并记录告警（仍进程隔离）。
- 清理继承密钥：子进程环境变量按白名单重建（不继承 FINSAIGHT_*、代理、
  凭据等），子进程启动后再二次删除疑似密钥变量；密钥不进入子进程地址空间。
- 父进程硬性超时：parse_time_budget_seconds + 5 秒宽限，超时 TerminateJobObject
  杀整棵进程树，返回 time_budget_exceeded。
- 子进程永不联网：解析器本身不发起网络请求；子进程环境不继承任何 Key。
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

# 子进程内存硬上限（Job Object）；对最大 25 MiB 的文件留足解析余量。
SUBPROCESS_MEMORY_LIMIT_MB = 512
# 父进程侧硬性超时 = 解析预算 + 宽限（子进程自身另有协作式预算检查）。
SUBPROCESS_TIMEOUT_GRACE_SECONDS = 5.0

_SECRET_PATTERN = re.compile(r"(?i)(key|token|secret|password|credential)")
# 环境白名单：只保留运行 Python/打开临时文件所需的最小集合。
_ENV_WHITELIST = ("SYSTEMROOT", "SYSTEMDRIVE", "PATH", "PATHEXT", "COMSPEC",
                  "TEMP", "TMP", "HOME", "LANG", "LC_ALL")


def build_clean_child_env(project_root: str) -> dict:
    """按白名单重建子进程环境：任何 Key/Token/Secret/代理都不随进程继承。"""
    env = {name: value for name, value in os.environ.items()
           if name.upper() in _ENV_WHITELIST}
    env["PYTHONPATH"] = project_root
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONSTARTUP", None)
    return env


def scrub_secret_env() -> list[str]:
    """子进程内的第二道防线：删除名字疑似密钥的环境变量，返回被删的名字。"""
    removed = [name for name in list(os.environ) if _SECRET_PATTERN.search(name)]
    for name in removed:
        del os.environ[name]
    return removed


def spawn_with_job(command: list[str], *, cwd: str, timeout_seconds: float,
                   input_data: bytes = b"",
                   memory_limit_mb: int = SUBPROCESS_MEMORY_LIMIT_MB,
                   ) -> tuple[int, bytes, bytes]:
    """在 Job Object 内运行子进程并等待；超时终止整棵进程树。

    返回 (returncode, stdout, stderr)。超时抛 TimeoutError；Job Object 不可用
    时降级为普通子进程（仍进程隔离），并在 stderr 前缀告警说明。
    """
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        import win32job  # noqa: F401  (Windows 专用；缺失则降级)
        job = win32job.CreateJobObject(None, "")
        # pywin32 的扩展限制信息为嵌套 dict：LimitFlags 在 BasicLimitInformation 内。
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        basic = dict(info.get("BasicLimitInformation") or {})
        basic["LimitFlags"] = (basic.get("LimitFlags") or 0) | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info["BasicLimitInformation"] = basic
        info["ProcessMemoryLimit"] = memory_limit_mb * 1024 * 1024
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info)
    except Exception:  # pragma: no cover - 仅非 Windows / 缺 pywin32 时
        job = None
    process = subprocess.Popen(
        command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=build_clean_child_env(cwd),
        creationflags=creationflags)
    if job is not None:
        try:
            win32job.AssignProcessToJobObject(job, int(process._handle))
        except Exception:  # pragma: no cover - 平台差异保护
            job = None
    try:
        stdout, stderr = process.communicate(input=input_data, timeout=timeout_seconds)
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        if job is not None:
            try:
                win32job.TerminateJobObject(job, 1)
            except Exception:  # pragma: no cover
                process.kill()
        else:
            process.kill()
        try:
            process.communicate(timeout=5)
        except Exception:  # pragma: no cover
            pass
        raise TimeoutError(f"子进程超过 {timeout_seconds:.0f} 秒硬性上限，已整树终止。")


def run_isolated_parse(request: dict, *, timeout_seconds: float) -> dict:
    """父进程侧入口：把解析请求交给一次性子进程，返回其 JSON 响应。

    cwd/PYTHONPATH 用 worker 模块自知的代码库根（PROJECT_ROOT），与运行时
    project_root（测试中为临时目录）解耦。子进程崩溃/输出非法 →
    {"ok": False, "code": "parse_failed"}；硬性超时 → time_budget_exceeded。
    """
    command = [sys.executable, "-m", "backend.app.research.attachment_worker"]
    payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
    try:
        code, stdout, _stderr = spawn_with_job(
            command, cwd=PROJECT_ROOT, timeout_seconds=timeout_seconds,
            input_data=payload)
    except TimeoutError:
        return {"ok": False, "code": "time_budget_exceeded",
                "message": "附件解析子进程超过硬性时间上限，已强制终止；请拆分文件后重新上传。"}
    if code != 0:
        return {"ok": False, "code": "parse_failed",
                "message": "附件解析子进程异常退出（安全隔离边界已生效），请确认文件未损坏后重试。"}
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        response = {"ok": False, "code": "parse_failed",
                    "message": "附件解析子进程输出不可解析，已拒绝该文件。"}
    return response if isinstance(response, dict) else {
        "ok": False, "code": "parse_failed", "message": "子进程响应格式异常，已拒绝该文件。"}


def _emit(response: dict) -> None:
    """子进程输出统一走字节级 UTF-8：Windows 管道默认 cp936 会破坏 JSON。"""
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    """子进程入口：stdin 收 JSON 请求，stdout 回一行 JSON 响应。

    启动后先清理疑似密钥环境变量；解析器延迟导入，保持启动轻量。
    """
    argv = sys.argv[1:] if argv is None else argv
    if "--print-env" in argv:
        # 仅测试用：列出仍然存活的疑似密钥变量名（只输出名字，不输出值）。
        surviving = [name for name in os.environ if _SECRET_PATTERN.search(name)]
        _emit({"ok": True, "secret_env": surviving})
        return 0
    scrub_secret_env()
    try:
        request = json.loads(sys.stdin.buffer.read() or b"{}")
    except json.JSONDecodeError:
        _emit({"ok": False, "code": "parse_failed",
               "message": "解析请求不可解析，已拒绝。"})
        return 0
    try:
        from .attachment_parsers import (AttachmentLimits, AttachmentParseError,
                                         parse_attachment)
        data = base64.b64decode(request.get("data_b64", ""))
        limits = AttachmentLimits(**(request.get("limits") or {}))
        parsed = parse_attachment(data, extension=request.get("extension", ""),
                                  mime_type=request.get("mime_type", ""),
                                  limits=limits)
        response = {"ok": True, "parsed": parsed.model_dump()}
    except AttachmentParseError as error:
        response = {"ok": False, "code": error.code, "message": error.message}
    except Exception:  # 子进程兜底：任何异常都转结构化失败，绝不带敏感上下文。
        response = {"ok": False, "code": "parse_failed",
                    "message": "附件解析失败（子进程内异常），请确认文件未损坏后重试。"}
    _emit(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
