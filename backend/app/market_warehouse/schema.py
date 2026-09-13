SCHEMA_VERSION = "market_daily_actions_v2"
DAILY_COLUMNS = {"thscode", "date_ms", "open_price", "high_price", "low_price", "close_price", "volume", "turnover", "adjusted", "currency"}
ADJUSTMENT_FACTOR_COLUMNS = {
    "thscode", "ticker", "ex_date_ms", "dividend_per_share", "per_share_bonus",
    "allotment_ratio", "allotment_price", "currency",
}
PRIMARY_KEY = ("thscode", "date_ms")

#: 决策 D9（01 号文档 P0-4.1 选 (b)）：**只读冻结**的数据集。
#: 为什么冻结而不是重建：全仓库没有写入者、进度日志缺失、只有年报口径、
#: 全部 evidence_id 为空串——重建一个"可复现"的财务数据集要逐季拉取并补齐
#: ROA/留存收益等字段，代价远超第一期收益（D1：财务因子第一期不做）。
#: **任何取数或建模代码都不得假定这些数据集会按季刷新。**
FROZEN_READONLY_DATASETS = {
    "financial_statements": {
        "decision": "D9（01 号文档 P0-4.1 选 (b)：冻结为只读历史快照）",
        "reason": "来源不明且不可复现：全仓无 INSERT 写入者，声明的进度日志不存在",
        "limitations": [
            "只有年报口径（10 个 20XX-Q4），没有任何 Q1/Q2/Q3，与同步脚本声明的 quarterly 不符",
            "1,316,176 行的 evidence_id 全为空串，未回链证据账本（红线要求关键数值绑证据 ID）",
            "缺少 Z-Score 需要的 retained_earnings / market_value_equity，"
            "F-Score 需要的 ROA 也不在本表内（属 indicators 端点）",
        ],
        "forbidden": "禁止增量更新；禁止在因子/策略中假定它按时点刷新",
    },
}

#: 决策 D10（01 号文档 P0-4.2 选「登记」）：**来源存疑**的已导入数据集。
#: 它们的 `schema_version` 落后于当前代码常量，且 **`market_sync_runs` 为 0 行**——
#: 正常导入路径 `_ingest_file()` 会同时写 `market_datasets` 并把对应 run 置为
#: `completed`，所以这些行不可能来自正常流程（迁移前灌入 / 手工 / 记录被清理）。
#: 不重跑全量同步：那要重新拉取 5000+ 标的的全部历史，耗时且无正确性收益。
LEGACY_DATASETS = {
    "daily_k": "schema_version 记为 market_daily_v1，落后于当前常量；无对应 sync run",
    "daily_k_10d": "schema_version 记为 market_daily_v1，落后于当前常量；无对应 sync run",
    "adjustment_factors": "无对应 sync run；质量报告含 1 条重复与 3 条同日多事件",
}
