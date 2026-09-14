"""把「财务质量 × 涨停事件」交互假设登记进预注册台账（幂等）。

背景：`23` 号文档把 6 个财务因子单独评价了一遍——**未检出优势**（最大 |t| = 0.81 对门槛 3.197），
并按 **D65** 把价值转向**交互型假设**：财务质量本身不是信号，但它是否**改变涨停事件的后续表现**？
这是第一次把两条**互相独立**的数据（财务三表 vs 涨停池）放在一起。

**第 0 步实测（先于本登记完成，结果写进 eligible_domain）**：

| 项 | 实测值 |
|---|---|
| 202 只财务标的里出现在涨停池的 | **186 只** |
| 这些标的的涨停事件 | **2,846 条**，落在 **749 个交易日**（2022-01-04 → 2026-09-03） |
| 事件当日能拿到 PIT F-Score 的 | **2,846 条（100%）**，0 缺失 |
| F-Score 分布 | 0–7 全覆盖，均值 4.014、中位 4.0 |
| 同日有 ≥2 只不同标的的交易日 | **513** |

**门槛（登记时写死）**：机器自己的计数口径是「每个 (字段, 口径) = 4 变体 × 3 统计量 +
8 分时段复现 = 20 条」。本轮 2 字段 × 2 口径 = **80 条**。

- 新族读法（交互族先验 0）：本轮整体 80 条 → |t| ≥ **3.4205**；
- **保守读法（把两个母族已看过的 43 + 36 = 79 条也算进来）：159 条 → |t| ≥ 3.6031**。

**本登记采用保守读法**（方向只能更严、不能更松，见 D55）。

用法：

    .\\.venv\\Scripts\\python.exe scripts\\governance_register_financial_interaction.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\governance_register_financial_interaction.py
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

FAMILY = "limit_up_financial_interaction"
DESIGN_REFERENCE = "limit_up_financial_interaction_design@v1"
#: 传给 runner 的族级先验（每字段一致）：母族 79 + 本轮同族兄弟 20 + 另一口径 40 = 139。
#: 这样报告里 family_total = 20 + 139 = 159，门槛正好等于保守读法的 3.6031。
FAMILY_PRIOR = 139
CONSERVATIVE_TOTAL = 159
NEW_FAMILY_TOTAL = 80

DOMAIN = {
    "eligible_domain": "**涨停池事件**（limit_pools，pool=limit_up，2022-01-04 → 2026-09-03）中、"
                       "且该标的在**事件当日**能取到 point-in-time F-Score 的那些事件。"
                       "第 0 步实测：202 只财务标的里有 186 只进过涨停池，共 **2,846 条事件 / "
                       "749 个交易日**；其中 **100% 都能取到 PIT F-Score**（当前期与上年同季"
                       "都过闸门）。同日有 ≥2 只不同标的的交易日 **513** 个——这是"
                       "『按评估日聚合』能成立的下限。",
    "data_source": "local_duckdb:limit_pools（涨停池，人工同步）+ local_duckdb:financial_facts"
                   "（财务三表，人工同步，带披露日闸门）",
    "as_of": "**两条 as-of 纪律同时成立**：①涨停池只用事件当日及之前的池内数据（池子按日落库）；"
             "②财务取值只用 available_from_ms <= 事件日 的报告期，且**各张报表取共同期**、"
             "Δ 类对照期取**上年同季**（见 22 号文档 §3 与 D59）。",
    "known_bias": "**样本选择偏置（结构性、不可消除）**：财务数据集只覆盖 202 只"
                  "（按 2026-09-04 成交额前 200 + 已确认标的选出），因此本交互只在"
                  "**高流动性大票的涨停事件**上被检验过——23 号文档 §5 实测这些标的在当日"
                  "全市场成交额分位的**中位数 0.8903、最小 0.801**。小票涨停（占了涨停池的"
                  "大多数）**不在样本内**，结论不得外推到它们。",
    "coverage_note": "财务数据的可用期从 2016-Q4 起，涨停池从 2022-01 起 → 实际评价区间由"
                     "涨停池决定（749 个交易日）。",
}

DESIGN = {
    "design": "事件研究（与 15–21 号文档同一套估计量），分组变量是**外部财务字段**",
    "unit": "(评估日, 涨停事件)",
    "grouping": "按**当日池内中位数**把事件分成高/低 F-Score 两组（相对口径，不是绝对分值）；"
                "取不到 PIT 值的标的不给组（不填补、不用池内均值顶替）。",
    "control": "同日**同一 20 日成交额十分位**的非涨停标的等权收益；matched_* 变体再加"
               "事前 40 日动量三分位（与 16 号文档一致）。",
    "outcome_primary": "**next_open（T+1 开盘 → T+20 收盘）**——按 D53 与 21 号文档，"
                       "可成交口径才是主检验；close_T 同时跑，作为**声明的对照**"
                       "（21 号已证明封板标的当日买不到，且效应会被隔夜跳空吃掉）。",
    "statistic": "配对超额**按评估日聚合**后的时间序列 t 值（events._t_stat，与既有事件研究"
                 "同一条代码路径）",
    "variants": "4 个：base_all（主检验）/ base_first_board / base_multi_board / matched_all"
                "（与 17 号文档一致的池内分层，用于排除『只是连板结构的影子』）",
    "placebo": "对照置换（同日同层重抽 200 次），**仅机制自检**——显著性一律来自 t 值",
    "replication": "分时段复现：2022–2024 与 2025 起（涨停池覆盖的两段）",
    "multiplicity": (
        "每 (字段, 口径) = 4 变体 × 3 统计量 + 8 分时段复现 = **20 条**；本轮 2 字段 × 2 口径 "
        f"= **80 条**。族级门槛取**保守读法**：把两个母族已看过的比较（连板族 43 + 现金族 36 "
        f"= 79）也计入 → {CONSERVATIVE_TOTAL} 条 → |t| ≥ **3.6031**。"
        f"（若按『交互族是新族、先验 0』读，本轮 80 条 → 3.4205；报告同时给出两者，"
        "**结论以更严的 3.6031 为准**。）"),
    "why_conservative": "D55 的原则：族级计数**只能算严、不能算松**。交互假设是站在两个母族"
                        "已经看过的比较之上的『再问一次』，把这些计入不会冤枉自己，"
                        "漏计却会把门槛算松。",
    "forbidden": "① 看到结果后换分组方式（如按绝对分值 4/5 分界、按三分位）或换中位数口径；"
                 "② 换结果口径（T+5/T+10、T+1 均价）或换前瞻期；"
                 "③ 缩小/扩大样本域（例如把财务数据同步范围临时放宽到全市场再跑）；"
                 "④ 用 RSS/博查的新闻解释结果并当作证据（D62）；"
                 "⑤ 把结论外推到小票涨停或全市场（样本选择偏置已声明）。",
    "status": "已预注册，**结果尚未产生**",
}

FACTORS = (
    {"reference": "limit_up_financial:f_score@v1",
     "name": "涨停事件中的财务质量调节：Piotroski F-Score（本地口径）",
     "direction": "high",
     "prior": "涨停是资金/情绪驱动的短期事件。财务质量高的公司，涨停后更可能有基本面支撑、"
              "抛压更小 → 高 F-Score 组的后续超额收益更高。反面先验同样成立：涨停后的短期"
              "表现几乎全由情绪与资金决定，基本面不构成调节 → 两组无差异。"
              "**本条是检验，不是规律。**",
     "note": "字段由 screener.financial_ic.FinancialFactorProvider 按事件当日做 "
             "point-in-time 取值。"},
    {"reference": "limit_up_financial:f_score_ratio@v1",
     "name": "涨停事件中的财务质量调节：归一化 F-Score（得分 ÷ 可算项数）",
     "direction": "high",
     "prior": "同 limit_up_financial:f_score@v1，但剔除『可算项数随行业变化』"
              "（银行没有营业成本 → 只能算 6 项）带来的不可比。",
     "note": "**同源变体**（与 f_score 共用同一批判定），只为处理可比性，不是独立证据。"},
)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="登记『财务质量 × 涨停事件』交互假设")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    governance = GovernanceService(Storage(load_settings().db_path))
    existing = {item["reference"] for item in governance.list_hypotheses()}
    plan: list[str] = []
    added = 0

    for factor in FACTORS:
        if factor["reference"] in existing:
            plan.append(f"skip  {factor['reference']}（已登记）")
            continue
        plan.append(f"add   {factor['reference']}（kind=factor, family={FAMILY}）")
        added += 1
        if not args.dry_run:
            governance.register_hypothesis(
                kind="factor", name=factor["name"], reference=factor["reference"],
                direction=factor["direction"],
                definition={"id": factor["reference"], "prior": factor["prior"],
                            "family": FAMILY, **DOMAIN},
                params={"family": FAMILY, "design": DESIGN["design"],
                        "grouping": DESIGN["grouping"],
                        "outcome_primary": DESIGN["outcome_primary"], "horizon_days": 20},
                exploratory=True, trial_count=len(FACTORS), notes=factor["note"])

    if DESIGN_REFERENCE in existing:
        plan.append(f"skip  {DESIGN_REFERENCE}（已登记）")
    else:
        plan.append(f"add   {DESIGN_REFERENCE}（kind=scan, 交互评价设计）")
        added += 1
        if not args.dry_run:
            governance.register_hypothesis(
                kind="scan", name="『财务质量 × 涨停事件』交互评价设计（预注册）",
                reference=DESIGN_REFERENCE,
                definition={"fields": [factor["reference"] for factor in FACTORS],
                            "family": FAMILY, **DOMAIN, **DESIGN},
                direction="无方向（设计登记）", exploratory=True, trial_count=len(FACTORS),
                notes="第 0 步实测（交集规模）在登记**之前**完成并写进 eligible_domain；"
                      "分组口径、对照、可成交前向口径、变体、分时段与 80/159 条计数门槛"
                      "全部在看到数字之前写死；门槛采用保守读法 3.6031。")

    batch_id = governance.current_batch_id()
    if batch_id and not args.dry_run:
        members = {(item["artifact_kind"], item["artifact_id"])
                   for item in governance.members(batch_id)}
        for hypothesis in governance.list_hypotheses():
            if hypothesis["reference"].startswith("limit_up_financial") \
                    and ("hypothesis", hypothesis["id"]) not in members:
                plan.append(f"bind  {hypothesis['reference']} -> batch")
                governance.add_member(batch_id=batch_id, artifact_kind="hypothesis",
                                      artifact_id=hypothesis["id"])

    print("\n".join(plan) if plan else "无需变更。")
    print(f"\n{'（dry-run，未写入）' if args.dry_run else f'完成（新增 {added} 条）。当前批次 {batch_id}'}")
    print(f"门槛：保守读法 |t| ≥ 3.6031（{CONSERVATIVE_TOTAL} 条）｜"
          f"新族读法 |t| ≥ 3.4205（{NEW_FAMILY_TOTAL} 条）；runner 用 "
          f"--family-prior-statistics 'f_score={FAMILY_PRIOR},"
          f"f_score_ratio={FAMILY_PRIOR}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
