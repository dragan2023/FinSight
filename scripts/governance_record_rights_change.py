"""把「配股比例漏进乘数」的口径修正登记为一条规则变更（方案 G3 / D49）。

**这是修 bug，不是改定义**：两处 docstring 与仓库里 P0-1 验证过的规范式子本来就要求
配股进乘数，是**实现**没跟上。所以不改任何已登记的因子/设计 reference，
只留变更痕迹 + 开新批次，并重跑受影响的评价（`19` 号文档）。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\governance_record_rights_change.py [--dry-run]
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

TARGET = "backend/app/screener/service.py"
OLD_MARKER = 'row["horizon_close"] * (1 + row.get("bonus", 0.0)) + row.get("dividend", 0.0)'
NEW_MARKER = 'row["horizon_close"] * (1 + row.get("bonus", 0.0) + row.get("rights", 0.0))'
PROPOSAL = "corporate_action_rights_basis@1"

PROPOSAL_DEFINITION = {
    "old_implementation": "total_return / 动量项写成 (1 + 送股)，配股只作为成本扣除",
    "correct_implementation": "(1 + 送股 + 配股)，配股成本照扣——持股数增加必须计入",
    "why_it_is_a_bug_not_a_definition_change":
        "两处 docstring 与仓库里 P0-1 验证过的规范式子 "
        "（`corporate_action_total_return_history`："
        "(close × (1 + 送股 + 配股) + 分红 − 配股成本) / prev_close）本来就要求配股进乘数；"
        "是**实现**漏了。因此不改任何已登记的因子/设计 reference，只留变更痕迹。",
    "measured_impact": "真实库 10,154,444 个有前向收益的 (日期, 标的) 单元里，窗口内含配股的 "
                       "有 1,411 个（0.0139%）；这批单元的数值会变。",
    "affected_evaluations": ["C2 横截面 IC 评价（24 个评估日）",
                             "涨停池事件研究（连板高度 / 封单额 / 封成比）"],
    "rollback": "把两处乘数改回 (1 + 送股) 即可；面板版本号同步降回 v3。",
}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def head_line() -> str:
    blob = subprocess.run(["git", "show", f"HEAD:{TARGET}"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", check=True).stdout
    for line in blob.splitlines():
        if "horizon_close" in line and "bonus" in line:
            return line
    raise SystemExit(f"在 HEAD:{TARGET} 里找不到旧的 total_return 行")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="登记配股口径修正")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    old = head_line()
    after = (ROOT / TARGET).read_text(encoding="utf-8")
    if NEW_MARKER not in after:
        raise SystemExit(f"工作区 {TARGET} 里没有修正后的实现，先改代码再登记")

    governance = GovernanceService(Storage(load_settings().db_path))
    if any("corporate_action_rights" in (item.get("field") or "")
           for item in governance.list_changes()):
        print("已登记过配股口径的变更，跳过。")
        return 0

    proposal = governance.find_by_reference(PROPOSAL)
    if proposal is None:
        if args.dry_run:
            print(f"（dry-run）将登记 draft 提案 {PROPOSAL}")
        else:
            proposal = governance.register_hypothesis(
                kind="rule", name="公司行动总收益：配股比例必须进乘数", reference=PROPOSAL,
                definition=PROPOSAL_DEFINITION,
                direction="修正后收益更高（对窗口内有配股的标的）",
                params={"field": "screener/returns:corporate_action_rights"},
                exploratory=False, trial_count=1, freeze=False,
                notes="先 draft 再 record_change 落地；落地后立即冻结。")
            print(f"已登记 draft 提案 {PROPOSAL}")

    if args.dry_run:
        print("（dry-run）将调用 record_change 并开新批次")
        return 0

    before_batch = governance.current_batch_id()
    result = governance.record_change(
        field="screener/returns:corporate_action_rights",
        reason="D49：`screener.service.total_return` 与 `_factor_value` 的动量项写成 (1 + 送股)，"
               "**漏掉配股比例**——只减了配股成本，没把配股拿到的新股算进去。这与两处 docstring "
               "以及仓库里 P0-1 验证过的规范式子（corporate_action_total_return_history）都不一致，"
               "属于实现没跟上既有定义。实测影响 10,154,444 个单元中的 1,411 个（0.0139%）。"
               "修正后重跑 C2 横截面 IC 与涨停池事件研究，并如实报告差异。",
        old_hash=digest(old), new_hash=digest(NEW_MARKER),
        hypothesis_id=proposal["id"], new_batch_name="配股口径修正（公司行动总收益）")
    new_batch = result["new_batch"]
    if governance.find_by_reference(PROPOSAL)["status"] == "draft":
        governance.freeze(proposal["id"])
    for hypothesis in governance.list_hypotheses():
        governance.add_member(batch_id=new_batch["id"], artifact_kind="hypothesis",
                             artifact_id=hypothesis["id"])

    if before_batch:
        closed = governance.get_batch(before_batch)
        print(f"旧批次 {before_batch[:8]} -> {closed['status']}"
              f"（superseded_by={(closed['superseded_by'] or '')[:8]}）")
    print(f"新批次 {new_batch['id'][:8]} open：{new_batch['name']}")
    print(f"停止条件继承：{'settled_30' in new_batch['stop_criteria_json']}")
    print(f"old_hash={digest(old)} new_hash={digest(NEW_MARKER)}")
    print(f"old 片段：{old.strip()[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
