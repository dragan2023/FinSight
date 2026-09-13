"""把 F15 的口径修正正式登记为一条规则变更（方案 G3）。

这是治理层建成后**第一条真正走完 record_change 的变更**，因此同时验证了三件事：
① 变更留痕；② 旧批次自动关闭并开新批次；③ 新批次继承停止条件（不可能顺带放松标准）。

`old_hash` / `new_hash` 用**真实代码内容的 sha256 前缀**，可从 git 历史复现核验。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.governance.service import GovernanceService  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402
from backend.app.storage import Storage  # noqa: E402

TARGET = "backend/app/strategy/service.py"
OLD_SNIPPET = "grade_value,reasons=grade(bars=len(bars),segments=seg,cost20_test=cost[-1],test=seg[-1])"
NEW_MARKER = "cost20_test=segments(data.strategy_id,bars,20,gate=gate)[-1]"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def read_old_line() -> str:
    blob = subprocess.run(["git", "show", f"HEAD:{TARGET}"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", check=True).stdout
    for line in blob.splitlines():
        if "cost20_test=cost[-1]" in line:
            return line
    raise SystemExit("在 HEAD 版本里找不到旧的 cost20_test 行")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    old_line = read_old_line()
    new_text = (ROOT / TARGET).read_text(encoding="utf-8")
    if NEW_MARKER not in new_text:
        raise SystemExit("工作区里没有找到 F15 的新实现，先改代码再登记变更")

    settings = load_settings()
    storage = Storage(settings.db_path)
    governance = GovernanceService(storage)

    existing = governance.list_changes()
    if any("F15" in (item.get("reason") or "") for item in existing):
        print("已登记过 F15 的变更，跳过。")
        return 0

    proposal = governance.find_by_reference("cost20_test_basis@1")
    before_batch = governance.current_batch_id()
    result = governance.record_change(
        field="strategy/grade:cost20_test_basis",
        reason="F15：门禁的 20bps 成本口径由「全周期」改为「测试段」。参数名 cost20_test 本就"
               "写明该意图；旧实现会把「测试段为正、全周期为负」的好实验误判为不合格。"
               "按 D14/D4 走 record_change 而非直接改代码。",
        old_hash=digest(old_line), new_hash=digest(NEW_MARKER),
        hypothesis_id=proposal["id"] if proposal else None)
    new_batch = result["new_batch"]

    # 提案已落地：冻结它（不再是可变草稿）。定义列本就未改，因此不触发冻结守卫。
    if proposal and proposal["status"] == "draft":
        governance.freeze(proposal["id"])

    # 规则与假设在新批次里继续有效，因此把登记项绑定到新批次（G4 按批次聚合）。
    for hypothesis in governance.list_hypotheses():
        governance.add_member(batch_id=new_batch["id"], artifact_kind="hypothesis",
                              artifact_id=hypothesis["id"])

    print(f"旧批次 {before_batch} -> {governance.get_batch(before_batch)['status']}"
          f"（superseded_by={governance.get_batch(before_batch)['superseded_by'][:8]}）")
    print(f"新批次 {new_batch['id'][:8]} open：{new_batch['name']}")
    print(f"停止条件是否继承：{'settled_30' in new_batch['stop_criteria_json']}")
    print(f"变更留痕：{len(governance.list_changes(batch_id=before_batch))} 条挂在旧批次上")
    print(f"old_hash={digest(old_line)} new_hash={digest(NEW_MARKER)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
