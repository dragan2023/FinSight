"""Explicit CLI entry point for the safe first-save initializer."""
import sys

from backend.app.model_config.local_secrets import initialize

#: 输出里有中文错误信息；管道/重定向下默认码页（本机 GBK）会让它抛 UnicodeEncodeError，
#: 把"写 .env 失败"这条真实原因换成一条无关的编码 traceback。见
#: `tests/unit/test_script_stdout_encoding.py`（那条守卫会检查所有 scripts/*.py）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

if __name__ == "__main__":
    try:
        print(initialize())
    except (RuntimeError, OSError) as error:
        print(str(error) if isinstance(error, RuntimeError) else "写入本机 .env 失败，请检查文件权限。")
        raise SystemExit(1) from None
