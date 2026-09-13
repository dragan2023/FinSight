"""把涨停池的三个字段登记进预注册台账（幂等）——**登记定义与设计，不是宣称已发现优势**。

背景（`docs/荐股治理方案/14` §8 第 2 步）：`14` 号文档写的是"把 `continue_day_cnt` /
`seal_money` / `limit_up_reason` 作为**新因子**入预注册台账"。真做的时候发现一件事，
必须先说清楚，否则登记出来的东西是假的：

**这三个字段只在当日涨停池内**有定义。池外标的上它们**不是缺失，而是未定义**——
用 0 填充或当缺失填进全市场横截面 IC，等于把"选择集"当成数值，算出来的 IC 无法解释。
所以本脚本登记的是**事件研究**设计，不是横截面因子设计，并且把它们**排除在
`screener.FACTOR_IDS` 之外**（横截面引擎取不到它们，不存在"误用"的可能）。

D19 的要求是"预注册时必须同时声明安慰剂通过率，并在评估报告里与真实结果并列"。
注意 `13` 号文档已经修正过这句话的一半：**安慰剂不能当显著性门槛**（逐标的置乱会把
横截面相关一起打碎，实测零分布只有真实 IC 离散度的 1/4–1/7，把它当门槛会系统性放大
通过率）。所以这里声明的形式是：**预先声明安慰剂设计 + 声明"真零分布下通过率应≈5%"**，
显著性仍然只由 t 值给出。

用法：

    .\\.venv\\Scripts\\python.exe scripts\\governance_register_limit_up_fields.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\governance_register_limit_up_fields.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.governance.service import GovernanceService  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402
from backend.app.storage import Storage  # noqa: E402

#: 三个字段共同的样本域与"为什么不能做横截面"的说明——写一次，逐条引用。
DOMAIN = {
    "eligible_domain": "仅当日同花顺涨停池内的标的（实测 2022-01-05 至 2026-09-04 "
                       "每个交易日 24–143 只；来源见 `sync_limit_pools.py` 与 `limit_pools` 表）",
    "outside_domain": "池外标的**没有取值**（未定义，不是缺失）。因此本字段**不进入** "
                      "screener.FACTOR_IDS，横截面排序引擎取不到它。",
    "data_source": "local_duckdb:limit_pools（人工显式同步；台账 limit_pool_sync_days 记录"
                   "每日 status/total/fetched）",
    "as_of": "只用评估日当日及之前的池子数据；前向收益只用于**事后结算**（见 "
             "repository.settlement_returns 的结果侧纪律）",
}

#: 预注册的事件研究设计。**先声明，再评价**——这便是 D19 要的那个"先"字。
DESIGN = {
    "design": "事件研究（不是横截面 IC）",
    "unit": "(评估日, 入池标的) 事件",
    "outcome": "事件后 20 个交易日的**公司行动总收益**（除权除息已折算，"
               "ex_date_ms <= 结算日 截断），口径与 `13` 号文档一致",
    "control": "同日**同一 20 日成交额十分位**的非涨停标的等权收益",
    "statistic": "配对超额（事件收益 − 对照收益）按评估日聚合后的**时间序列 t 值**",
    "significance": "|t| >= 1.96；**不**使用逐标的置乱安慰剂作门槛（`13` 号文档的修正）",
    "placebo": [
        "对照置换：把入池标的随机替换为同日同成交额十分位的非涨停标的（保持样本量与"
        "日期分布），重抽 200 次，真实配对超额应超出该零分布 95 分位",
        "日期错位：把池子整体平移到随机其它交易日，配对超额应≈0",
    ],
    "placebo_pass_rate_declared": "真零分布下上述门槛的通过率应 ≈5%。若实测通过率明显高于 5%，"
                                  "先怀疑零分布构造有误（`13` 号文档正是这么踩过一次坑），"
                                  "**不得**据此宣布发现因子。",
    "status": "已预注册，**尚未评价**——本条登记的是定义、样本域与安慰剂设计，"
              "不是『已发现优势』的声明",
}

FIELDS = (
    {
        "reference": "limit_up_event:continue_day_cnt@v1",
        "name": "涨停连板高度（当日连续涨停天数）",
        "direction": "high",
        "prior": "连板高度代表资金一致性与市场关注度；先验认为连板数越高，事件后 20 个交易日"
                 "的延续性越强。",
        "note": "先验**很弱**且反例常见（连板股亦可能情绪反转），因此本条是一次检验，"
                "不是一条可依赖的规律。字段由涨停池接口返回，实测同一标的在不同日期取值可不同。",
    },
    {
        "reference": "limit_up_event:seal_money@v1",
        "name": "涨停封单额（收盘时封单金额）",
        "direction": "high",
        "prior": "封单额相对流通市值越大，说明买盘承接越强、抛压越轻；先验认为封单强度与"
                 "事件后短期表现正相关。",
        "note": "只登记**绝对封单额**；若要改用封单/流通市值等相对口径，属于新定义，"
                "必须作为新 reference 重新预注册（方案 A.1）。",
    },
    {
        "reference": "limit_up_event:limit_up_reason@v1",
        "name": "涨停原因（分类文本字段）",
        "direction": "不适用（分类字段，方向由编码方案决定）",
        "prior": "涨停原因反映驱动事件的性质（题材、业绩、公告等）；不同原因类别的后续"
                 "表现可能系统性地不同。",
        "note": "**本次不评价**：文本字段必须先确定编码方案（类别合并规则本身是一个搜索空间，"
                "按方案 A.2 必须计入 trial_count）。任何编码方案在使用前都要作为"
                "**新假设单独预注册**，不得在本条目下反复试编码。",
    },
)

SCAN_REFERENCE = "limit_up_event_study_design@v1"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="登记涨停池字段的事件研究预注册")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    governance = GovernanceService(Storage(load_settings().db_path))
    plan: list[str] = []
    existing = {item["reference"] for item in governance.list_hypotheses()}

    for field in FIELDS:
        reference = field["reference"]
        if reference in existing:
            plan.append(f"skip  {reference}（已登记）")
            continue
        plan.append(f"add   {reference}（kind=factor, exploratory, 事件研究）")
        if args.dry_run:
            continue
        governance.register_hypothesis(
            kind="factor", name=field["name"], reference=reference,
            direction=field["direction"],
            definition={"id": reference, "prior": field["prior"], **DOMAIN, **DESIGN},
            params={"eligible_domain": DOMAIN["eligible_domain"], "design": DESIGN["design"],
                    "horizon_days": 20},
            exploratory=True, trial_count=len(FIELDS),
            notes=field["note"])

    if SCAN_REFERENCE in existing:
        plan.append(f"skip  {SCAN_REFERENCE}（已登记）")
    else:
        plan.append(f"add   {SCAN_REFERENCE}（kind=scan, exploratory）")
    if SCAN_REFERENCE not in existing and not args.dry_run:
        governance.register_hypothesis(
            kind="scan", name="涨停池事件研究设计（预注册，探索性）",
            reference=SCAN_REFERENCE,
            definition={"fields": [field["reference"] for field in FIELDS], **DOMAIN, **DESIGN},
            direction="无方向（设计登记）", exploratory=True, trial_count=len(FIELDS),
            notes="把「先声明设计、再评价」这件事本身落成台账条目：即使三个字段将来一个都没过，"
                  "也能回答『当时承诺用的是什么设计、什么门槛』。")

    batch_id = governance.current_batch_id()
    if batch_id and not args.dry_run:
        members = {(item["artifact_kind"], item["artifact_id"])
                   for item in governance.members(batch_id)}
        for hypothesis in governance.list_hypotheses():
            if hypothesis["reference"].startswith("limit_up_event") \
                    and ("hypothesis", hypothesis["id"]) not in members:
                plan.append(f"bind  {hypothesis['reference']} -> batch")
                governance.add_member(batch_id=batch_id, artifact_kind="hypothesis",
                                      artifact_id=hypothesis["id"])

    print("\n".join(plan) if plan else "无需变更。")
    print(f"\n{'（dry-run，未写入）' if args.dry_run else f'完成。当前批次 {batch_id}'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
