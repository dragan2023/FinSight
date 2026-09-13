"""把「涨停剔除口径」的变更正式登记为一条规则变更（方案 G3 / D33 / D34）。

变更本身：`screener/universe.py` 的涨停剔除从**只有价量代理**（涨幅 ≥ 9.8% 且收盘 = 最高）
改为**优先用同花顺涨停池**（交易所口径），未同步的评估日**回落到代理并把口径写进输出与
`params_hash`**。

为什么要走 record_change 而不是直接改代码：
- 这是**已经有结果之后**的规则变更（`13` 号文档的 IC 评价是在旧口径下跑出来的），
  必须留痕、必须开新批次，旧批次的结果才不会被误当成新口径下的结果；
- 口径进 `params_hash`，因此新旧结果天然不可混比——但治理台账要能回答"为什么变了"。

`old_hash` / `new_hash` 是**真实代码片段的 sha256 前缀**，可从 git 历史复现核验。
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

TARGET = "backend/app/screener/universe.py"
OLD_MARKER = "change_pct >= LIMIT_UP_PROXY_PCT"
NEW_MARKER = ('        if rules.exclude_suspected_limit_up:\n'
              '            stats["checked_rows"] += 1')

PROPOSAL = "limit_up_exclusion_basis@1"

PROPOSAL_DEFINITION = {
    "old_rule": "涨停剔除 = 涨幅 ≥ 9.8% 且收盘 = 当日最高（LIMIT_UP_PROXY_PCT 价量代理）",
    "new_rule": "涨停剔除优先用同花顺涨停池（交易所口径）；该评估日没有池子数据时回落到代理，"
                "并把 basis 与回落理由写进输出；basis 进 params_hash",
    "affected_field": "screener/universe:limit_up_exclusion",
    "expected_effect": "① 修正代理的两类系统性偏差——ST 的 5% 涨停被漏掉、"
                       "「收在最高但未封板」被误剔；② params_hash 变化，新旧结果不可混比",
    "accepted_risk": "真实池只覆盖已同步的交易日（实测涨停池可回溯到 2022-01-05，"
                     "更早的日子静默返回 0 条）。因此历史 IC 评价会落在**混合口径**上，"
                     "报告必须报出口径覆盖天数，**不得**表述为『已全面按交易所口径剔除』",
    "rollback": "把 UniverseRules.limit_up_source 设为 proxy_only 即回到旧口径，"
                "此时 params_hash 与旧结果一致（该取值只用于对照，不设为默认）",
}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def old_line() -> str:
    blob = subprocess.run(["git", "show", f"HEAD:{TARGET}"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", check=True).stdout
    for line in blob.splitlines():
        if OLD_MARKER in line:
            return line
    raise SystemExit(f"在 HEAD:{TARGET} 里找不到旧的代理判定行")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    old = old_line()
    after = (ROOT / TARGET).read_text(encoding="utf-8")
    if NEW_MARKER not in after:
        raise SystemExit(f"工作区 {TARGET} 里没有新实现，先改代码再登记变更")

    governance = GovernanceService(Storage(load_settings().db_path))
    if any("limit_up_exclusion" in (item.get("field") or "")
           for item in governance.list_changes()):
        print("已登记过涨停剔除口径的变更，跳过。")
        return 0

    proposal = governance.find_by_reference(PROPOSAL)
    if proposal is None:
        proposal = governance.register_hypothesis(
            kind="rule", name="涨停剔除口径：价量代理 → 真实涨停池", reference=PROPOSAL,
            definition=PROPOSAL_DEFINITION, direction="剔除更严（真实池同时会漏掉代理抓不到的 ST 涨停）",
            params={"field": PROPOSAL_DEFINITION["affected_field"]}, exploratory=False,
            trial_count=1, freeze=False,
            notes="先作为 draft 提案登记，再走 record_change 落地；落地后立即冻结。")
        print(f"已登记 draft 提案 {PROPOSAL}")

    before_batch = governance.current_batch_id()
    result = governance.record_change(
        field="screener/universe:limit_up_exclusion",
        reason="D33/D34：涨停剔除由纯价量代理改为优先用同花顺涨停池（交易所口径）。"
               "代理有两类系统性偏差：ST 的 5% 涨停会被漏掉（9.8% 门槛天然抓不到），"
               "而「收在最高价但并未封板」会被误剔。变更后的口径进 params_hash，"
               "并且每个评估日实际用的是哪一种口径都写进输出（universe.limit_up.basis）。",
        old_hash=digest(old), new_hash=digest(NEW_MARKER),
        hypothesis_id=proposal["id"], new_batch_name="涨停剔除口径变更（真实池）")
    new_batch = result["new_batch"]
    if governance.find_by_reference(PROPOSAL)["status"] == "draft":
        governance.freeze(proposal["id"])
    for hypothesis in governance.list_hypotheses():
        governance.add_member(batch_id=new_batch["id"], artifact_kind="hypothesis",
                              artifact_id=hypothesis["id"])

    for batch_id in (before_batch,):
        if batch_id:
            closed = governance.get_batch(batch_id)
            print(f"旧批次 {batch_id[:8]} -> {closed['status']}"
                  f"（superseded_by={(closed['superseded_by'] or '')[:8]}）")
    print(f"新批次 {new_batch['id'][:8]} open：{new_batch['name']}")
    print(f"停止条件继承：{'settled_30' in new_batch['stop_criteria_json']}")
    print(f"old_hash={digest(old)} new_hash={digest(NEW_MARKER)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
