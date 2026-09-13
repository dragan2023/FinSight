# 数据面评估：同花顺 API 手册能补齐什么、补不齐什么

> 起因：`13` 号文档的结论是"下一步真正的瓶颈不是因子数量，而是**数据面**"——
> 缺 ST 标志、缺退市证券池、缺真实停牌/涨跌停、缺财务因子。
> 本文核对官方手册 [`https://fuyao.aicubes.cn/llms-full.txt`](https://fuyao.aicubes.cn/llms-full.txt)
> （378 KB / 8712 行，2026-09-13 版），逐项给出**接口、字段与手册行号**。
>
> 结论：**两样可补、一样部分可补、一样仍缺**；另外有三个附带发现，其中一个直接解除了
> P0-4.1「财务数据不可复现」的前提。

---

## 0. 一句话结论

| 缺失项 | 手册里有没有 | 结论 |
|---|---|---|
| **真实涨跌停 / 炸板** | **有**（4 个接口，按交易日查） | **可补** —— 能把现在的"疑似涨停代理"换成真实池 |
| **F-Score 所需财务字段** | **有**（ROA、经营现金流、资产总计、资产负债率、流动比率、毛利率、总资产周转） | **可补** —— F-Score 可以真正算出来 |
| **ST / \*ST 标志** | **部分有**：只在**涨停股票池**的 item 里（`is_st`），且注明"仅展示，不作为筛选入参" | **部分可补** —— 覆盖不全，**不能**用它做全市场 ST 剔除 |
| **退市证券池** | **没有**。全文 0 匹配"退市/风险警示/摘牌/\*ST"；含**上市状态**的 `stock-basics` 标注"正在规划中" | **仍缺** —— 幸存者偏差只能继续常驻披露 |
| **Z-Score 的留存收益 / 权益市值** | **没有**。A 股资产负债表只返回 7 个字段（无未分配利润）；估值快照只有 PE/PB/PS/PCF（无市值） | **仍缺** —— Z-Score 继续诚实降级 |

**三个附带发现（价值可能超过上面四项）**：

1. **财务三表都返回 `report_date_ms`（披露日）** → **point-in-time 财务数据可以正确做**，
   这正是 `02` 号文档 §A.0.3 悬而未决的那一项；
2. **`period=quarterly` 是支持的** → 本地库"只有 10 个年报期"是**同步口径的选择**，不是接口限制；
3. **三表支持 `start`/`end` 时间区间取数（跨度 ≤10 年）** → 历史可回补。

---

## 1. 逐项证据

### 1.1 真实涨跌停 / 炸板：**有**

「涨跌停与炸板数据」章节（手册 6904–6926）提供四个接口：

| 接口 | 路径 | 说明 | 手册行 |
|---|---|---|---|
| 涨停股票池 | `GET /api/a-share/special-data/limit-up-pool` | 按交易日返回涨停/连板股票池 | 6923 |
| 跌停股票池 | `GET /api/a-share/special-data/limit-down-pool` | 按交易日返回跌停股票池 | 6924 |
| 炸板股票池 | `GET /api/a-share/special-data/limit-break-pool` | 按交易日返回涨停炸板股票池 | 6925 |
| 连板天梯 | `GET /api/a-share/special-data/limit-up-ladder` | 近 30 个交易日的连板梯队矩阵 | 6926 |

参数是 `date_ms`（query，查询交易日毫秒戳；省略时回退到服务端当前自然日）+ 分页 + 排序，
排序字段 `last_price` / `continue_day_cnt` / `seal_money` / `limit_up_time`（手册 6939–6948）。

涨停池 item 还带 `limit_up_time`（封板时间）、`continue_day_text` / `continue_day_cnt`（几连板）、
`seal_money` / `max_seal_money`（封单额）、`limit_up_reason`（涨停原因）（手册 6978–6985）。

**这意味着**：现在 universe 里那条「涨幅 ≥ 9.8% 且收盘=最高」的**代理指标可以删掉**，
换成真实的涨停/跌停/炸板名单。

### 1.2 F-Score 所需字段：**有**

`f_score.py` 需要 9 项输入，逐项对照手册：

| F-Score 输入 | 手册字段 | 接口 | 手册行 |
|---|---|---|---|
| `total_assets_net_ratio`（ROA） | `total_assets_net_ratio` 总资产收益率 | `/api/a-share/financials/indicators` | 2580 |
| `act_cash_flow_net` | `act_cash_flow_net` 经营活动现金流净额 | `/api/a-share/financials/cash-flow-statements` | 1361 |
| `assets_total` | `assets_total` 资产总计 | `/api/a-share/financials/balance-sheets` | 1286 |
| `assets_debt_ratio` | `assets_debt_ratio` 资产负债率 | indicators（偿债能力） | 2590 |
| `current_ratio` | `current_ratio` 流动比率 | indicators（偿债能力） | 2588 |
| `sale_gross_margin` | `sale_gross_margin` 销售毛利率 | indicators（盈利能力） | 2578 |
| `total_assets_turnover_ratio` | `total_assets_turnover_ratio` 总资产周转率 | indicators（营运能力） | 2599 |
| `is_st` | 只在涨停池 item 里 | — | 6976 / 7018 |
| `listed_days` | **没有**（属规划中的 `stock-basics`） | — | 7665–7669 |

**结论**：F-Score 的 7 个数据项全部可取得；缺的两项恰好是**资格判断**（ST、上市未满一年），
缺了它们 F-Score 仍可计算，只是**不能自动剔除 ST 与次新股**——这是可披露的限制，
不是算不出来的阻塞（对照 F11 的原始结论"Z-Score 永处降级态、F-Score 只在顾问链可用"）。

### 1.3 ST / \*ST：**部分有**

`is_st` 出现在**涨停股票池**的响应 item 中（手册 6976），字段说明是
「是否 ST，**仅展示，不作为筛选入参**」（手册 7018）。

**因此**：只能知道"当日涨停的票里哪些是 ST"，**不能**对全市场做 ST 剔除。
全市场 ST 标志要等规划中的 `stock-basics`。

### 1.4 退市证券池：**没有**

全文检索 `退市` / `风险警示` / `摘牌` / `*ST` / `暂停上市` → **0 命中**。

最接近的是「股票基础信息」章节（手册 7661–7670）：

> **股票基础信息** 接口正在规划中。
> 将提供 A 股标的基本资料、**上市状态**、行业归属、市值等核心字段查询。

**结论**：幸存者偏差**只能继续常驻披露**。不要自己拼一个退市名单——那需要外部数据源，
且容易变成不可核验的手工数据（与"不伪造证据"的口径冲突）。

### 1.5 Z-Score 的两个字段：**仍缺**

- **留存收益**：`zscore.py` 要 `retained_earnings`。A 股资产负债表**只返回 7 个字段**
  （`assets_total` / `total_current_assets` / `non_current_nets_total` / `cash` /
  `accounts_receivable` / `total_debt` / `holder_equity_total`，手册 1286–1292）——
  **没有未分配利润**。手册里唯一出现 `undistributed_profit`（未分配利润）的地方是
  **基金**资产负债表（手册 3227），对 A 股不可用。
- **权益市值**：`market_value_equity`。估值快照**固定只返回 PE(TTM/MRQ)/PB(MRQ)/PS(TTM)/PCF(TTM)**
  五个字段，"不提供历史估值"（手册 7687 / 7703）——**没有市值**。手册里唯一的市值字段是
  集合竞价快照的 `float_market_cap`（**流通**市值，手册 2052），而 Z-Score 要的是**权益市值**。
  总市值与上市状态一样，属规划中的 `stock-basics`。

**结论**：**Z-Score 继续诚实降级**（`available="false"` + `missing` 列明），不猜、不填零。

---

## 2. 三个附带发现

### 2.1 `report_date_ms`（披露日）——这一条最重要

三张报表的共有响应字段里都有（手册 1124）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `report_date_ms` | long | **披露日**（毫秒） |
| `period_end_ms` | long | 报告期末 |

`02` 号文档 §A.0.3 悬着一个问题：「`disclosed_at_ms` 的可用性与一致性——若不可靠，
point-in-time 退化为 `period_end_ms + 固定滞后`，并必须在报告中披露」。

**现在有答案了**：官方接口直接给披露日，**point-in-time 财务数据可以正确做**，
不需要用"期末 + 固定滞后"这种近似。这也正好接上 P0-4.1：
当初选 (b)"冻结为只读快照"的理由是**不可复现**（全仓无写入者、只有年报口径、`evidence_id` 全空），
现在这条路已经打开。

### 2.2 `period=quarterly` 是支持的

手册 1108：`period` = `annual`（仅 Q4 报告期）/ `quarterly`（每个季度末）。

本地库 `financial_statements` 只有 10 个 `20XX-Q4`，而同步脚本声明的是 `quarterly`
（P0-4.1 记录的不一致）——**那是当年的同步口径问题，不是接口做不到**。

### 2.3 三表支持时间区间取数

手册 1109–1111：`start` + `end`（毫秒戳）闭区间，**窗口跨度不超过 10 年**；
与 `limit` 互斥。→ **历史可回补**。

---

## 3. 决策（D28–D32）

| # | 决策 | 理由 |
|---|---|---|
| **D28** | **第二期把「补数据面」作为唯一目标**，按优先级：① 财务指标（`indicators`）② 三表（含 `report_date_ms`）③ 涨跌停/炸板池 | 这三样都能**直接**提升评价质量：涨跌停池替换掉代理指标；财务指标让 F-Score 真正可用；三表的披露日让 point-in-time 成立 |
| **D29** | **退市证券池不做**，继续常驻披露幸存者偏差 | 手册里没有；自己拼名单不可核验，且与"不伪造证据"冲突。等上游 `stock-basics` |
| **D30** | **Z-Score 第二期仍不做**，保持诚实降级 | 留存收益与权益市值都缺；用流通市值近似权益市值会引入口径错误，不值当 |
| **D31** | **ST 剔除仍不做全市场版**；涨停池里的 `is_st` 只用于**标注**，不用于剔除 | 覆盖不全（只有涨停票），用它剔除会制造"看起来剔了 ST 其实没有"的假安全感 |
| **D32** | **同步必须显式人工触发**，且落库时**带上披露日**（`report_date_ms`）与**接口返回的指标口径** | 这是 D6（自动化只建立在本地 DuckDB 上）与 point-in-time 纪律的组合：回补历史可以，但不能变成后台定时拉取 |

---

## 4. 下一步的具体计划

**第 0 步（验收前置，必做）**：**实测能否用历史日期回补涨跌停池**。
手册写了 `date_ms` 参数但没写可回溯范围——`limit-up-ladder` 明确只给"近 30 个交易日"，
涨停池没写。所以在设计同步表之前，必须先用**一个历史交易日**实测一次：

- 若可回补 → 涨跌停历史表可建，universe 的代理指标可以删掉；
- 若只能取当日 → 涨跌停数据只有**前向**价值（从今天起逐日累积），
  且必须在文档里写明"历史区间无法回补、universe 仍用代理指标"。

**这一步必须在写表结构之前做**：否则可能建了一张永远填不满的表。

**第 1 步**：财务指标与三表的本地落库设计（新表 + 披露日 + 口径字段），同步脚本人工触发。

**第 2 步**：把涨跌停池接入 `screener/universe.py`，删掉 9.8% 代理；
`is_st` 进输出字段（标注用）。

**第 3 步**：F-Score 从"顾问链专用"升级为**本地可算**，并作为第二个因子入预注册台账
（按 D19 必须同时声明安慰剂通过率）。

---

## 5. 复现

```powershell
# 下载手册（本机需经本地代理，git/curl 不读 WinINET 设置）
Invoke-WebRequest -Uri 'https://fuyao.aicubes.cn/llms-full.txt' `
  -OutFile .cache\ths-manual.txt -Proxy http://127.0.0.1:7890

# 检索四样东西（行号可直接对齐本文档）
Select-String -Path .cache\ths-manual.txt -Pattern 'is_st|listed_days|退市|停牌' -Encoding utf8
Select-String -Path .cache\ths-manual.txt -Pattern 'limit-up-pool|limit-down-pool|limit-break-pool' -Encoding utf8
Select-String -Path .cache\ths-manual.txt -Pattern 'retained_earnings|total_assets_net_ratio|未分配利润|市值' -Encoding utf8
Select-String -Path .cache\ths-manual.txt -Pattern 'report_date_ms|quarterly' -Encoding utf8
```

手册版本：2026-09-13 下载，378,022 字节 / 8712 行。**上游会变**——
本文档的行号与字段以该版本为准；每次据此改代码前，先用上面的检索命令复核一遍。
