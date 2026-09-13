"""登记一条规则变更并开启新批次（方案 G3 的通用入口，幂等）。

用法：

    .\\.venv\\Scripts\\python.exe scripts\\governance_record_change.py \
        --field strategy/backtest:execution_price \
        --reason "F23：净值曲线在换手当日沿用 close/prev_close，与 trades[].return_pct 的 open→open 矛盾" \
        --file backend/app/strategy/backtest.py \
        --old "cash *= bar.close/bars[i-1].close; held += 1" \
        --new "cash *= bar.open/bars[i-1].close" \
        [--proposal backtest_execution_price@1] [--batch-name "…"]

`--old` / `--new` 是**代码片段**，脚本对工作区文件与 `git show HEAD:<file>` 做包含性检查，
并用两者的 sha256 前缀作为 `old_hash` / `new_hash`——这样留痕可从 git 历史复现核验，
而不是一段无法验证的自由文本。
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.governance.service import GovernanceService  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402
from backend.app.storage import Storage  # noqa: E402


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def head_text(relative: str) -> str:
    return subprocess.run(["git", "show", f"HEAD:{relative}"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", check=True).stdout


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="登记规则变更并开新批次")
    parser.add_argument("--field", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--file", required=True, help="相对仓库根的文件路径")
    parser.add_argument("--old", required=True, help="改动前的代码片段（须出现在 HEAD 版本里）")
    parser.add_argument("--new", required=True, help="改动后的代码片段（须出现在工作区文件里）")
    parser.add_argument("--proposal", default="", help="对应的 draft 规则提案 reference")
    parser.add_argument("--batch-name", default="")
    args = parser.parse_args()

    before = head_text(args.file)
    after = (ROOT / args.file).read_text(encoding="utf-8")
    if args.old not in before:
        raise SystemExit(f"--old 片段没有出现在 HEAD:{args.file} 里，核对后再试")
    if args.new not in after:
        raise SystemExit(f"--new 片段没有出现在工作区 {args.file} 里，核对后再试")

    governance = GovernanceService(Storage(load_settings().db_path))
    for item in governance.list_changes():
        if args.field in (item.get("field") or ""):
            print(f"字段 {args.field} 已登记过变更，跳过。")
            return 0

    proposal = governance.find_by_reference(args.proposal) if args.proposal else None
    before_batch = governance.current_batch_id()
    result = governance.record_change(
        field=args.field, reason=args.reason,
        old_hash=digest(args.old), new_hash=digest(args.new),
        hypothesis_id=proposal["id"] if proposal else None,
        new_batch_name=args.batch_name or None)
    new_batch = result["new_batch"]
    if proposal and proposal["status"] == "draft":
        governance.freeze(proposal["id"])
    for hypothesis in governance.list_hypotheses():
        governance.add_member(batch_id=new_batch["id"], artifact_kind="hypothesis",
                              artifact_id=hypothesis["id"])

    if before_batch:
        closed = governance.get_batch(before_batch)
        print(f"旧批次 {before_batch[:8]} -> {closed['status']}"
              f"（superseded_by={closed['superseded_by'][:8]}）")
    print(f"新批次 {new_batch['id'][:8]} open：{new_batch['name']}")
    print(f"停止条件继承：{'settled_30' in new_batch['stop_criteria_json']}")
    print(f"old_hash={digest(args.old)} new_hash={digest(args.new)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
