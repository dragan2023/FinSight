"""把财务因子登记进预注册台账（幂等）——**登记的是定义与设计，不是宣称已发现优势**。

背景：`22` 号文档把财务三表做成带 **point-in-time 闸门** 的本地数据集，并确认 F-Score
的 7/9 项可以本地算出。`22` §8 第 1 项要求：**先把因子与设计登记进台账，再去看数字**（D19 / A.1 / A.2）。

本脚本登记六条因子（同一族 `financial_fundamentals`）与一份评价设计：

| 因子 | 定义 | 方向 | 依据 |
|---|---|---|---|
| `f_score` | Piotroski 本地口径得分（0–7，9 项里可算 7 项） | high | Piotroski(2000) 的 F-Score；本地口径见 `financials.py` |
| `f_score_ratio` | 得分 ÷ 可算项数（0–1） | high | 可算项数随行业变化（银行没有营业成本 → 6 项），比值才可比 |
| `roa` | 净利润 ÷ **期末**资产总计 | high | 盈利能力；与厂商 `total_assets_net_ratio` 实测相对差 <1% |
| `assets_debt_ratio` | 负债合计 ÷ 资产总计 | **low** | 杠杆越低越好（方向必须显式声明，写错 IC 符号全反） |
| `sale_gross_margin` | (营业收入 − 营业成本) ÷ 营业收入 | high | 毛利率水平；**银行等无营业成本者不给值**（缺值不填补） |
| `total_assets_turnover_ratio` | 营业收入 ÷ 资产总计 | high | 资产使用效率 |

**为什么只登记"水平"而不登记"变化量"**：变化量（ΔROA/Δ杠杆…）已经在 F-Score 的九项里
被用掉了；再单独登记它们，等于把同一份信息数第二遍，把族级计数推高而信息量不增（A.2 要防的
正是"用搜索空间换显著性"）。

用法：

    .\\.venv\\Scripts\\python.exe scripts\\governance_register_financial_factors.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\governance_register_financial_factors.py
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

FAMILY = "financial_fundamentals"

#: 六条因子共同的样本域与纪律——写一次，逐条引用。
DOMAIN = {
    "eligible_domain": "本地财务数据集（`financial_facts`，2026-09-15 人工同步：候选池前 200 + "
                       "已确认标的，共 202 只）× 当日通过既有股票池规则（`screener.universe`）"
                       "的标的；每个评估日需 ≥20 只有效样本才参与统计。",
    "data_source": "local_duckdb:financial_facts（人工显式同步，见 scripts/sync_financials.py；"
                   "带取数台账 financial_sync_symbols 与数据集自证 financial_sync_meta）",
    "as_of": "**严格 point-in-time**：每个评估日 T 只用 `available_from_ms <= T` 的报告期；"
             "闸门 = min(接口披露日, 法定披露截止日) + 1 天（见 `22` §1 与 D59）。"
             "查询入口 `repository.financial_snapshot_asof`（返回前对闸门做**代码断言**）。"
             "对照期一律取**上年同季**（季报为累计口径，同季同比才是同一口径的比较）。",
    "known_bias": "**样本选择偏置（本轮必须量化披露）**：这 202 只按 **2026-09-04 的成交额"
                  "前 200** 选出，却被用于评价 2016 年以来的历史 → 标的层面存在"
                  "『以未来流动性选样』的偏置。评价报告必须给出这些标的在各评估日的流动性分位"
                  "与长期居留情况；结论**只能**表述为『在这个已选定的高流动性样本内』，"
                  "**不得**外推为全市场结论。",
}

#: 评价设计：先写死"看哪些比较、门槛多少"，再去看数字。
DESIGN = {
    "design": "横截面 Rank IC（不是事件研究）",
    "why_cross_section": "财务因子对所有有财务数据的标的都有定义（不像涨停池字段只在池内有定义），"
                         "所以横截面 IC 是它的合法评价方式（`13` 号文档的框架）。",
    "unit": "(评估日, 标的) 观测",
    "evaluation_dates": "**每月最后一个交易日**（共约 120 个），自财务数据第一个闸门起；"
                        "若某评估日的报告期不统一（众数占比 < 90%），该日剔除并在报告里列出清单"
                        "（累计口径的比率（如总资产周转率）在不同 YTD 窗口之间不可比）。",
    "factor_preference": "因子先折算成 0–1 的**偏好分**（方向已在声明里折进去），"
                         "因此 **IC > 0 恒表示该因子的偏好方向确实赚钱**，符号可直接解释。",
    "outcome_primary": "**`next_open`（T+1 开盘 → T+20 收盘）**——按 D53，可成交口径才是主检验；"
                       "`close_T` 口径同时跑，作为**声明的对照**（它在 `21` 已被证明不可成交）。",
    "statistic": "Rank IC（Spearman）按评估日聚合后的**时间序列 t 值**：`t = mean(IC) / (sd(IC)/√T)`",
    "why_not_placebo": "`13` 号文档已纠正过：逐标的置乱会把横截面相关一起打碎，零分布离散度"
                       "只有真实 IC 的 1/4–1/7，拿它当门槛会系统性放大通过率。因此"
                       "**显著性一律来自 IC 时间序列 t 值**，安慰剂只作机制自检。",
    "placebo": "逐标的置乱（重抽 200 次）：真实 IC 应显著超出该零分布，但**不得**作为门槛。",
    "replication": "分时段复现：2016–2020 与 2021 起（与 `13` 的采样区间口径一致）。",
    "multiplicity": "计数 = 6 因子 × 2 前向口径 × (全样本 + 2 分时段) = **36 条**；"
                    "族级先验 = 0（新族）。族级 Bonferroni 门槛由 `_z_for_two_sided(0.05/36)` 给出。"
                    "**注意**：本轮的 36 条必须计入**下一轮同族**的族级先验。",
    "forbidden": "① 看到结果后增删因子、改方向、改评估日频率或样本数下限；"
                 "② 改用未在本设计里声明的口径（如换 T+5/T+10 终点、换 T+1 均价）；"
                 "③ 用 RSS/博查的新闻或搜索摘要去解释结果并当作证据（D62：它们只是线索）；"
                 "④ 把结论外推到全市场（样本选择偏置已声明）。",
    "status": "已预注册，**结果尚未产生**",
}

FACTORS = (
    {"reference": "financial:f_score@v1", "name": "Piotroski F-Score（本地口径，0–7）",
     "direction": "high",
     "prior": "财务质量综合得分越高，未来表现越好（Piotroski 2000 的价值股样本结论）。"
              "本机实现取 9 项中可本地计算的 7 项。",
     "note": "前 8 项逐项判定、第 7 项（未增发）与流动比率项因数据源缺失恒为 N/A。"
             "得分本身是**计数**，可算项数随行业变化 → 同时登记 `f_score_ratio` 作对照。"},
    {"reference": "financial:f_score_ratio@v1", "name": "F-Score 归一化（得分 ÷ 可算项数）",
     "direction": "high",
     "prior": "同上，但剔除『可算项数不同』带来的不可比：银行等无营业成本的标的只能算 6 项。",
     "note": "**同源变体**（与 `f_score` 共用同一批判定），仅用于处理可比性，不是独立证据。"},
    {"reference": "financial:roa@v1", "name": "总资产收益率（本地口径 = 净利润 ÷ 期末资产总计）",
     "direction": "high",
     "prior": "盈利能力越强越好。",
     "note": "与厂商 `total_assets_net_ratio` 实测相对差 <1%（厂商疑似用平均总资产）——"
             "**两个口径不混用**，本条只用本地口径（可自证、带披露日）。"},
    {"reference": "financial:assets_debt_ratio@v1", "name": "资产负债率（本地口径）",
     "direction": "low", "prior": "杠杆越低，财务风险越小。",
     "note": "方向为 **low**（偏好分里已取反）；方向写错会让 IC 符号全反，故在登记里显式写死。"},
    {"reference": "financial:sale_gross_margin@v1", "name": "销售毛利率（本地口径）",
     "direction": "high", "prior": "毛利率反映定价能力与竞争格局。",
     "note": "营业收入或营业成本缺失时**不给值**（不填补）：银行等本就没有营业成本。"},
    {"reference": "financial:total_assets_turnover_ratio@v1", "name": "总资产周转率（本地口径）",
     "direction": "high", "prior": "资产使用效率越高越好。",
     "note": "分子是**累计口径**的营业收入，因此跨期不可比 → 由设计里的"
             "『报告期统一性 ≥90%』规则处理，而不是自己拼一个 TTM。"},
)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="登记财务因子的预注册")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    governance = GovernanceService(Storage(load_settings().db_path))
    existing = {item["reference"] for item in governance.list_hypotheses()}
    plan: list[str] = []
    added = 0

    for factor in FACTORS:
        reference = factor["reference"]
        if reference in existing:
            plan.append(f"skip  {reference}（已登记）")
            continue
        plan.append(f"add   {reference}（kind=factor, family={FAMILY}, 方向 {factor['direction']}）")
        added += 1
        if args.dry_run:
            continue
        governance.register_hypothesis(
            kind="factor", name=factor["name"], reference=reference,
            direction=factor["direction"],
            definition={"id": reference, "prior": factor["prior"], "family": FAMILY, **DOMAIN},
            params={"family": FAMILY, "design": DESIGN["design"],
                    "outcome_primary": DESIGN["outcome_primary"],
                    "horizon_days": 20},
            exploratory=True, trial_count=len(FACTORS), notes=factor["note"])

    scan_reference = "financial_factor_study_design@v1"
    if scan_reference in existing:
        plan.append(f"skip  {scan_reference}（已登记）")
    else:
        plan.append(f"add   {scan_reference}（kind=scan, 财务因子评价设计）")
        added += 1
        if not args.dry_run:
            governance.register_hypothesis(
                kind="scan", name="财务因子横截面 IC 评价设计（预注册）",
                reference=scan_reference,
                definition={"fields": [factor["reference"] for factor in FACTORS],
                            "family": FAMILY, **DOMAIN, **DESIGN},
                direction="无方向（设计登记）", exploratory=True, trial_count=len(FACTORS),
                notes="把『先声明设计、再评价』落成台账：因子集、方向、评估日频率、"
                      "可成交前向口径、统计量、分时段复现与 36 条计数的门槛都在看到数字之前写入。"
                      "同时**声明样本选择偏置**（按 2026-09-04 流动性选出的 202 只用于评价历史），"
                      "并要求报告量化披露，禁止外推为全市场结论。")

    batch_id = governance.current_batch_id()
    if batch_id and not args.dry_run:
        members = {(item["artifact_kind"], item["artifact_id"])
                   for item in governance.members(batch_id)}
        for hypothesis in governance.list_hypotheses():
            if hypothesis["reference"].startswith("financial") \
                    and ("hypothesis", hypothesis["id"]) not in members:
                plan.append(f"bind  {hypothesis['reference']} -> batch")
                governance.add_member(batch_id=batch_id, artifact_kind="hypothesis",
                                      artifact_id=hypothesis["id"])

    print("\n".join(plan) if plan else "无需变更。")
    print(f"\n{'（dry-run，未写入）' if args.dry_run else f'完成（新增 {added} 条）。当前批次 {batch_id}'}")

    if not args.dry_run and added:
        from backend.app.screener.events import _z_for_two_sided

        declared = len(FACTORS) * 2 * 3
        print(f"声明的比较条数 = {len(FACTORS)} 因子 × 2 口径 × 3 区间 = {declared}；"
              f"族级门槛 |t| ≥ {_z_for_two_sided(0.05 / declared):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
