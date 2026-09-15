"""Fixed DuckDB schema and queries; no caller-supplied SQL."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from pathlib import Path
import duckdb
from .schema import ADJUSTMENT_FACTOR_COLUMNS, DAILY_COLUMNS, FROZEN_READONLY_DATASETS
from .financials import (group_periods, select_common_period,
                         select_prior_year_facts)

#: 事件研究**预计算面板**的版本号。改了下面的 SQL 就要改它——否则旧缓存会被当成新口径用。
#: v5：新增**可成交口径**列（`next_open` / `forward_return_next_open` /
#:     `forward_return_next_intraday` / `next_open_gap` / `next_open_ms`）——
#:     起点由 T 收盘改为 **T+1 开盘**，公司行动只计入**严格晚于 T+1** 的除权除息。
#: v4：`forward_return` 把**配股比例**加回乘数，与修正后的 `service.total_return` 一致。
#: v3：补一条有界修正语句，重算"最近 20 根 K 线跨度超过回溯上限"的行（长期停牌复牌股）。
#: v2：`forward_return` 改为复刻 `service.total_return` 的既有口径（配股只减成本、不进乘数）。
PANEL_SCHEMA_VERSION = "event_panel_v5"
PANEL_FILENAME = "finsight_event_panel.duckdb"

#: 快照列的回溯上限（**自然日**），必须等于 `cross_section(history_days=...)` 的默认值。
#: 长期停牌股复牌后，它的第 20/60 根 K 线可能落在 400 天之前——`cross_section` 那时给
#: NULL（或只平均窗口内的少数几根），面板必须**复刻**这个行为才算等价。
#: 有测试断言这两个常量一致，防止有人改了 `cross_section` 的默认值而忘了这里。
SNAPSHOT_HISTORY_DAYS = 400

#: 面板构建 SQL（**一次全表扫描**算出所有评估日所需的全部输入）。
#:
#: 事件研究原本每个评估日要跑两次全表查询（横截面 + 前向收益），1033 个评估日就是
#: 2000+ 次全表扫描；这里改成**一遍**把它们物化下来。
#:
#: 与既有实现**逐位等价**的三处细节（都是实测踩出来的）：
#:
#: 1. **序列只由 `close_price > 0` 的 K 线构成**——`cross_section` 与 `settlement_returns`
#:    都按这个口径排名（停牌/异常收盘不算一个"交易日"）；
#: 2. **公司行动按时间戳区间 `(T, T+h]` 计入**：57,116 条除权记录里有 **274 条**落在
#:    个股 K 线范围内却没有当天的 K 线（休市或停牌）。这批动作的窗口边界与"正好落在
#:    K 线日"的那批**差一格**，所以分两股取窗口再相加——只取一股会与 `settlement_returns`
#:    不一致（实测 49,095 个抽样单元里有 6 个不符）；
#: 3. **回溯上限 400 自然日**（见 `SNAPSHOT_HISTORY_DAYS`）：不复刻它，长期停牌股复牌后的
#:    `turnover_20` / `close_20_ago` / `close_60_ago` 会与 `cross_section` 不一致
#:    （实测 3 个标的值差到 91%）。
#:
#: ⚠️ 面板**刻意复刻既有口径，包括它的一个缺陷**——不许在"加速"这一轮里顺手改口径，
#: 否则"新旧结果一致"这个验收条件就废了。已发现的缺陷：仓库里 P0-1 验证过的规范式子
#: （`corporate_action_total_return_history`）是
#: `(close × (1 + 送股 + 配股) + 分红 − 配股成本) / prev_close`——**配股在乘数里**；
#: 而 `screener.service.total_return` 只写 `(1 + 送股)`，**漏掉配股比例**（其 docstring
#: 却写着包含配股）。面板按既有口径实现，`fwd_rights` 仍算出来并保留但不进公式，
#: 该缺陷另行登记修复（见 `18` 号文档）。
PANEL_BUILD_SQL = """
CREATE OR REPLACE TABLE event_panel AS
WITH bars AS (
    SELECT thscode, date_ms, open_price, close_price, turnover,
           row_number() OVER (PARTITION BY thscode ORDER BY date_ms) AS rn
    FROM market.daily_k WHERE close_price > 0
),
acts AS (
    SELECT b.thscode, b.rn, (a.ex_date_ms = b.date_ms) AS exact,
           sum(coalesce(a.per_share_bonus, 0)) AS bonus,
           sum(coalesce(a.dividend_per_share, 0)) AS dividend,
           sum(coalesce(a.allotment_ratio, 0)) AS rights,
           sum(coalesce(a.allotment_ratio, 0) * coalesce(a.allotment_price, 0)) AS rights_cost
    FROM market.adjustment_factors a
    ASOF JOIN bars b ON a.thscode = b.thscode AND a.ex_date_ms >= b.date_ms
    GROUP BY b.thscode, b.rn, exact
),
grid AS (
    SELECT b.thscode, b.date_ms, b.open_price, b.close_price, b.turnover, b.rn,
           coalesce(s.bonus, 0) AS s_bonus, coalesce(s.dividend, 0) AS s_div,
           coalesce(s.rights, 0) AS s_rights, coalesce(s.rights_cost, 0) AS s_cost,
           coalesce(o.bonus, 0) AS o_bonus, coalesce(o.dividend, 0) AS o_div,
           coalesce(o.rights, 0) AS o_rights, coalesce(o.rights_cost, 0) AS o_cost
    FROM bars b
    LEFT JOIN acts s ON s.thscode = b.thscode AND s.rn = b.rn AND s.exact
    LEFT JOIN acts o ON o.thscode = b.thscode AND o.rn = b.rn AND NOT o.exact
),
framed AS (
    SELECT *,
           max(date_ms) OVER w20 AS frame20_last,
           max(date_ms) OVER w60 AS frame60_last
    FROM grid
    WINDOW w20 AS (PARTITION BY thscode ORDER BY rn ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
           w60 AS (PARTITION BY thscode ORDER BY rn ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
),
masked AS (
    SELECT *,
           CASE WHEN date_ms >= frame20_last - CAST({history_days} AS BIGINT) * 86400000
                THEN turnover END AS t20,
           CASE WHEN date_ms >= frame60_last - CAST({history_days} AS BIGINT) * 86400000
                THEN turnover END AS t60
    FROM framed
)
SELECT thscode, date_ms,
       turnover AS turnover_today,
       avg(t20) OVER w20 AS turnover_20,
       CASE WHEN date_ms - lag(date_ms, 20) OVER w <= CAST({history_days} AS BIGINT) * 86400000
            THEN lag(close_price, 20) OVER w END AS close_20_ago,
       CASE WHEN date_ms - lag(date_ms, 60) OVER w <= CAST({history_days} AS BIGINT) * 86400000
            THEN lag(close_price, 60) OVER w END AS close_60_ago,
       lead(date_ms, 20) OVER w AS horizon_ms,
       lead(close_price, 20) OVER w AS close_fwd,
       sum(s_bonus) OVER w_co + sum(o_bonus) OVER w_off AS fwd_bonus,
       sum(s_div) OVER w_co + sum(o_div) OVER w_off AS fwd_div,
       sum(s_rights) OVER w_co + sum(o_rights) OVER w_off AS fwd_rights,
       sum(s_cost) OVER w_co + sum(o_cost) OVER w_off AS fwd_cost,
       CASE WHEN lead(close_price, 20) OVER w > 0 THEN
            -- 与 `screener.service.total_return` **逐字一致**：配股比例进乘数
            -- （2026-09-14 修正；此前面板复刻的是漏掉配股的旧口径，见 `19` 号文档）
            (lead(close_price, 20) OVER w
             * (1 + (sum(s_bonus) OVER w_co + sum(o_bonus) OVER w_off)
                  + (sum(s_rights) OVER w_co + sum(o_rights) OVER w_off))
             + (sum(s_div) OVER w_co + sum(o_div) OVER w_off)
             - (sum(s_cost) OVER w_co + sum(o_cost) OVER w_off))
            / close_price - 1 ELSE NULL END AS forward_return,
       -- ---- v5：**可成交口径**（买入点 = T+1 开盘，见 `20` 号文档 §5 / `21` 号文档）----
       -- `next_open_ms` 是**买入日**；前向窗口的终点仍是 T+20 收盘（与 forward_return
       -- 同一终点，两条口径才能直接并排比较）。
       lead(date_ms, 1) OVER w AS next_open_ms,
       lead(open_price, 1) OVER w AS next_open,
       lead(close_price, 1) OVER w AS next_close,
       -- 公司行动**严格晚于买入日**（`ex_date_ms > T+1`）：买在 T+1 开盘的人拿不到
       -- T+1 当天除权的分红/送股——那天他买到的就是除权后的价格。分两股取窗口，
       -- 理由与 v1 的同名逻辑相同（除权日与 K 线日不重合的那批要单独算）：
       --   精确落盘：第 T+2 … T+20 根 K 线（ROWS 2 FOLLOWING AND 20 FOLLOWING）
       --   两日之间：落在 (barT+1, barT+2] … (barT+19, barT+20] 的事件
       --             → 赋给第 T+1 … T+19 根 K 线（ROWS 1 FOLLOWING AND 19 FOLLOWING）
       -- 起点写成 1 FOLLOWING 而不是 CURRENT ROW 是**这一步最容易写错的地方**：
       -- CURRENT ROW 会把 T 与 T+1 之间的除权算进来，而那发生在买入之前。
       -- 注意：本段 SQL 走 str.format()，注释里**不能出现孤立的花括号**（踩过：
       -- KeyError: 'T+2'）。要写区间请用 barT+1 这种写法。
       sum(s_bonus) OVER w_co2 + sum(o_bonus) OVER w_off2 AS fwd2_bonus,
       sum(s_div) OVER w_co2 + sum(o_div) OVER w_off2 AS fwd2_div,
       sum(s_rights) OVER w_co2 + sum(o_rights) OVER w_off2 AS fwd2_rights,
       sum(s_cost) OVER w_co2 + sum(o_cost) OVER w_off2 AS fwd2_cost,
       CASE WHEN lead(close_price, 20) OVER w > 0 AND lead(open_price, 1) OVER w > 0 THEN
             (lead(close_price, 20) OVER w
              * (1 + (sum(s_bonus) OVER w_co2 + sum(o_bonus) OVER w_off2)
                   + (sum(s_rights) OVER w_co2 + sum(o_rights) OVER w_off2))
              + (sum(s_div) OVER w_co2 + sum(o_div) OVER w_off2)
              - (sum(s_cost) OVER w_co2 + sum(o_cost) OVER w_off2))
             / lead(open_price, 1) OVER w - 1 ELSE NULL END AS forward_return_next_open,
       -- T+1 开盘 → T+1 收盘。窗口是 (T+1, T+1]——**空的**，所以任何公司行动都不该进来
       -- （T+1 当天除权的股票，买的已经是除权价）。写成裸价格比正是这个道理。
       CASE WHEN lead(open_price, 1) OVER w > 0 AND lead(close_price, 1) OVER w > 0
            THEN lead(close_price, 1) OVER w / lead(open_price, 1) OVER w - 1
            ELSE NULL END AS forward_return_next_intraday,
       -- 「买不到」的量化：封板到收盘的股票，次日开盘相对今收盘的跳空。
       CASE WHEN lead(open_price, 1) OVER w > 0
            THEN lead(open_price, 1) OVER w / close_price - 1 ELSE NULL END AS next_open_gap
FROM masked
WINDOW w AS (PARTITION BY thscode ORDER BY rn),
       w20 AS (PARTITION BY thscode ORDER BY rn ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
       w_co AS (PARTITION BY thscode ORDER BY rn ROWS BETWEEN 1 FOLLOWING AND 20 FOLLOWING),
       w_off AS (PARTITION BY thscode ORDER BY rn ROWS BETWEEN CURRENT ROW AND 19 FOLLOWING),
       w_co2 AS (PARTITION BY thscode ORDER BY rn ROWS BETWEEN 2 FOLLOWING AND 20 FOLLOWING),
       w_off2 AS (PARTITION BY thscode ORDER BY rn ROWS BETWEEN 1 FOLLOWING AND 19 FOLLOWING)
"""


#: 修正语句：把"最近 20 根 K 线跨度超过回溯上限"的少数行的 `turnover_20` 重算。
#:
#: 为什么需要它：`cross_section` 的 20 日平均是在**400 自然日窗口内**取的，而窗口函数
#: 的 20 行帧只能按行数取。两者在"长期停牌后复牌"的标的上会分叉——这类标的的 20 根 K 线
#: 跨度可能好几年，帧里会混进窗口外的行。用 `max(date_ms) OVER w20` 之类的办法**修不了**：
#: 帧的末行就是当前行，所以 `date_ms >= frame_last - 400d` 恒真（实测踩过，掩码完全没生效）。
#: 因此对这批稀有行（实测 5,246 个标的里只有个位数）改用一次**有界**的关联子查询重算，
#: 语义与 `cross_section` 逐字一致。
PANEL_FIX_SQL = """
CREATE OR REPLACE TEMP TABLE _panel_wide_frames AS
SELECT thscode, date_ms FROM (
    SELECT thscode, date_ms,
           lag(date_ms, 19) OVER (PARTITION BY thscode ORDER BY date_ms) AS date_19_back
    FROM market.daily_k WHERE close_price > 0
) t
WHERE date_19_back IS NOT NULL
  AND date_ms - date_19_back > CAST({history_days} AS BIGINT) * 86400000;

UPDATE event_panel SET turnover_20 = (
    SELECT avg(d.turnover) FROM market.daily_k d
    WHERE d.thscode = event_panel.thscode AND d.close_price > 0
      AND d.date_ms <= event_panel.date_ms
      AND d.date_ms >= event_panel.date_ms - CAST({history_days} AS BIGINT) * 86400000)
WHERE EXISTS (SELECT 1 FROM _panel_wide_frames f
              WHERE f.thscode = event_panel.thscode AND f.date_ms = event_panel.date_ms);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint_text(fingerprint: dict) -> str:
    """把指纹字典转成可比较的稳定字符串（键排序，避免顺序敏感）。"""
    return ";".join(f"{key}={fingerprint[key]}" for key in sorted(fingerprint))


class WarehouseRepository:
    def __init__(self, path: Path) -> None: self.path = path
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = duckdb.connect(str(self.path)); db.execute("PRAGMA enable_progress_bar=false")
        db.execute("""CREATE TABLE IF NOT EXISTS daily_k (
            thscode VARCHAR NOT NULL, date_ms BIGINT NOT NULL, open_price DOUBLE, high_price DOUBLE,
            low_price DOUBLE, close_price DOUBLE, volume DOUBLE, turnover DOUBLE,
            adjusted VARCHAR NOT NULL, currency VARCHAR NOT NULL, PRIMARY KEY(thscode, date_ms))""")
        db.execute("""CREATE TABLE IF NOT EXISTS adjustment_factors (
            event_key VARCHAR NOT NULL PRIMARY KEY,
            thscode VARCHAR NOT NULL, ticker VARCHAR, ex_date_ms BIGINT NOT NULL,
            dividend_per_share DOUBLE, per_share_bonus DOUBLE, allotment_ratio DOUBLE,
            allotment_price DOUBLE, currency VARCHAR NOT NULL)""")
        # 涨跌停/炸板池（第 0 步实测后新增，见 docs/荐股治理方案/14）：
        # 只能**按交易日逐日**取，因此单列一张表 + 一张"取数台账"。
        # `limit_pool_sync_days` 的唯一理由：**把三种"空"区分开**——
        # ① 当天真的没有涨停；② 参数写错被静默忽略（实测写成 `date` 会返回 code=0 且 0 条）；
        # ③ 超出可回溯范围（涨停池 ≤2019 也是静默 0 条，跌停/炸板则报 1002）。
        # 没有这张台账，事后无法判断某天的缺失是"没发生"还是"没取到"。
        db.execute("""CREATE TABLE IF NOT EXISTS limit_pools (
            date_ms BIGINT NOT NULL, pool VARCHAR NOT NULL, thscode VARCHAR NOT NULL,
            ticker VARCHAR, name VARCHAR, is_st BOOLEAN, is_new BOOLEAN,
            last_price DOUBLE, price_change_ratio_pct DOUBLE,
            continue_day_cnt INTEGER, continue_day_text VARCHAR,
            seal_money DOUBLE, max_seal_money DOUBLE,
            limit_up_time VARCHAR, limit_up_reason VARCHAR,
            fetched_at VARCHAR NOT NULL,
            PRIMARY KEY (date_ms, pool, thscode))""")
        db.execute("""CREATE TABLE IF NOT EXISTS limit_pool_sync_days (
            date_ms BIGINT NOT NULL, pool VARCHAR NOT NULL, status VARCHAR NOT NULL,
            total INTEGER, fetched INTEGER, pages INTEGER, page_size INTEGER,
            error_code INTEGER, error_message VARCHAR, fetched_at VARCHAR NOT NULL,
            PRIMARY KEY (date_ms, pool))""")
        # 财务数据·point-in-time（`22` 号文档；D58–D60）。**与上面被 D9 冻结的
        # `financial_statements` 是两回事**：那张是来源不明、只有年报、不可复现的历史快照，
        # 这张是本轮人工同步、逐条带**披露日闸门**的新数据集。
        # 长表（一字段一行）而不是宽表：三表字段集随标的类型变化（银行没有"营业成本"），
        # 宽表要么大量 NULL 要么每加一个字段就迁移；长表把"缺字段"如实表达为**没有这一行**。
        db.execute("""CREATE TABLE IF NOT EXISTS financial_facts (
            thscode VARCHAR NOT NULL, statement VARCHAR NOT NULL, period_key VARCHAR NOT NULL,
            period_end_ms BIGINT NOT NULL, field VARCHAR NOT NULL, value DOUBLE,
            unit VARCHAR, currency VARCHAR,
            disclosed_at_ms BIGINT,
            available_from_ms BIGINT NOT NULL, available_basis VARCHAR NOT NULL,
            api_basis VARCHAR NOT NULL, source VARCHAR NOT NULL, run_id VARCHAR NOT NULL,
            fetched_at VARCHAR NOT NULL,
            PRIMARY KEY (thscode, statement, period_key, field))""")
        # 取数台账：与内容**同进同出**（`14` §6.2 第 3 条的教训——没有台账就分不清
        # "这只标的没有数据"和"这次没取到"）。
        db.execute("""CREATE TABLE IF NOT EXISTS financial_sync_symbols (
            thscode VARCHAR NOT NULL, statement VARCHAR NOT NULL, status VARCHAR NOT NULL,
            periods INTEGER, fields INTEGER, gaps INTEGER,
            requested_start_ms BIGINT, requested_end_ms BIGINT,
            api_basis VARCHAR, error_code INTEGER, error_message VARCHAR,
            run_id VARCHAR NOT NULL, fetched_at VARCHAR NOT NULL,
            PRIMARY KEY (thscode, statement))""")
        # 数据集自证：版本、范围（universe 哈希）、窗口、口径。与事件研究面板的 meta 同一用途。
        db.execute("""CREATE TABLE IF NOT EXISTS financial_sync_meta (
            key VARCHAR PRIMARY KEY, value VARCHAR)""")
        # Earlier local builds assumed (thscode, ex_date_ms) was unique.  The
        # official export can contain more than one distinct action on a day;
        # preserve those events while still collapsing byte-for-byte duplicates.
        action_columns = {row[1] for row in db.execute("PRAGMA table_info('adjustment_factors')").fetchall()}
        if "event_key" not in action_columns:
            db.execute("""CREATE TABLE adjustment_factors_v2 (
                event_key VARCHAR NOT NULL PRIMARY KEY,
                thscode VARCHAR NOT NULL, ticker VARCHAR, ex_date_ms BIGINT NOT NULL,
                dividend_per_share DOUBLE, per_share_bonus DOUBLE, allotment_ratio DOUBLE,
                allotment_price DOUBLE, currency VARCHAR NOT NULL)""")
            db.execute("""INSERT INTO adjustment_factors_v2
                SELECT md5(concat_ws('|', thscode, coalesce(ticker,''), ex_date_ms::VARCHAR,
                    coalesce(dividend_per_share::VARCHAR,''), coalesce(per_share_bonus::VARCHAR,''),
                    coalesce(allotment_ratio::VARCHAR,''), coalesce(allotment_price::VARCHAR,''), currency)),
                    thscode,ticker,ex_date_ms,dividend_per_share,per_share_bonus,allotment_ratio,allotment_price,currency
                FROM adjustment_factors""")
            db.execute("DROP TABLE adjustment_factors")
            db.execute("ALTER TABLE adjustment_factors_v2 RENAME TO adjustment_factors")
        return db
    def validate_parquet(self, file: Path, *, kind: str = "daily_k") -> dict:
        if kind == "adjustment_factors":
            return self.validate_adjustment_factors_parquet(file)
        if kind in FROZEN_READONLY_DATASETS:
            # P0-4.1 / D9：只读冻结数据集不接受导入校验——把"禁止增量更新"从文档
            # 提升为会抛错的代码路径，而不是靠读者记得。
            detail = FROZEN_READONLY_DATASETS[kind]
            raise ValueError(f"{kind} 是只读冻结数据集（{detail['decision']}）。"
                             f"{detail['forbidden']}。原因：{detail['reason']}")
        if kind not in {"daily_k", "daily_k_10d", "daily_k_import"}:
            raise ValueError("不支持的全市场数据集类型。")
        db = self._connect()
        try:
            columns = {row[0] for row in db.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(file)]).fetchall()}
            missing = DAILY_COLUMNS - columns
            if missing: raise ValueError("数据集缺少必要列：" + ",".join(sorted(missing)))
            quality = db.execute("""SELECT count(*) AS row_count, min(date_ms), max(date_ms),
                count(*) - count(DISTINCT (thscode, date_ms)) duplicates,
                sum(CASE WHEN close_price IS NULL OR close_price <= 0 THEN 1 ELSE 0 END) bad_close
                FROM read_parquet(?) WHERE adjusted='none' AND currency='CNY'""", [str(file)]).fetchone()
            if quality[0] == 0: raise ValueError("数据集没有可用的未复权人民币日 K。")
            return {"row_count": quality[0], "min_date_ms": quality[1], "max_date_ms": quality[2], "duplicates": quality[3], "bad_close": quality[4]}
        finally: db.close()

    def validate_adjustment_factors_parquet(self, file: Path) -> dict:
        """Validate official corporate-action events without manufacturing factors.

        Raw events are stored as published.  They are not presented as a
        calculated forward/backward-adjusted price series.
        """
        db = self._connect()
        try:
            columns = {row[0] for row in db.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(file)]).fetchall()}
            missing = ADJUSTMENT_FACTOR_COLUMNS - columns
            if missing: raise ValueError("数据集缺少必要列：" + ",".join(sorted(missing)))
            quality = db.execute("""SELECT count(*) AS row_count, min(ex_date_ms), max(ex_date_ms),
                count(*) - count(DISTINCT (thscode, ticker, ex_date_ms, dividend_per_share, per_share_bonus, allotment_ratio, allotment_price, currency)) exact_duplicates,
                count(*) - count(DISTINCT (thscode, ex_date_ms)) same_day_extra_events,
                sum(CASE WHEN thscode IS NULL OR trim(thscode)='' OR ex_date_ms IS NULL OR ex_date_ms<=0 THEN 1 ELSE 0 END) invalid_rows
                FROM read_parquet(?) WHERE currency='CNY'""", [str(file)]).fetchone()
            if quality[0] == 0: raise ValueError("数据集没有可用的人民币公司行动事件。")
            return {"row_count": quality[0], "min_date_ms": quality[1], "max_date_ms": quality[2], "duplicates": quality[3], "same_day_extra_events": quality[4], "invalid_rows": quality[5] or 0}
        finally: db.close()
    def upsert_daily(self, file: Path) -> None:
        db = self._connect()
        try:
            db.execute("BEGIN")
            db.execute("DELETE FROM daily_k USING read_parquet(?) incoming WHERE daily_k.thscode=incoming.thscode AND daily_k.date_ms=incoming.date_ms", [str(file)])
            db.execute("INSERT INTO daily_k SELECT thscode,date_ms,open_price,high_price,low_price,close_price,volume,turnover,adjusted,currency FROM read_parquet(?) WHERE adjusted='none' AND currency='CNY'", [str(file)])
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK"); raise
        finally: db.close()

    def upsert_adjustment_factors(self, file: Path) -> None:
        db = self._connect()
        try:
            db.execute("BEGIN")
            db.execute("DELETE FROM adjustment_factors")
            db.execute("""INSERT INTO adjustment_factors
                SELECT DISTINCT md5(concat_ws('|', thscode, coalesce(ticker,''), ex_date_ms::VARCHAR,
                    coalesce(dividend_per_share::VARCHAR,''), coalesce(per_share_bonus::VARCHAR,''),
                    coalesce(allotment_ratio::VARCHAR,''), coalesce(allotment_price::VARCHAR,''), currency)),
                    thscode,ticker,ex_date_ms,dividend_per_share,per_share_bonus,allotment_ratio,allotment_price,currency
                FROM read_parquet(?)
                WHERE currency='CNY' AND thscode IS NOT NULL AND trim(thscode)<>'' AND ex_date_ms>0""", [str(file)])
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK"); raise
        finally: db.close()
    def status(self) -> dict:
        if not self.path.exists():
            return {
                "available": False,
                "row_count": 0,
                "readiness": self._readiness(),
            }
        db = self._connect()
        try:
            count, minimum, maximum, symbol_count, trading_days = db.execute("""
                SELECT count(*), min(date_ms), max(date_ms), count(DISTINCT thscode), count(DISTINCT date_ms)
                FROM daily_k
            """).fetchone()
            latest_day_symbols = 0
            if maximum is not None:
                latest_day_symbols = db.execute(
                    "SELECT count(DISTINCT thscode) FROM daily_k WHERE date_ms=?", [maximum]
                ).fetchone()[0]
            action_count, action_minimum, action_maximum, action_symbols = db.execute("""
                SELECT count(*), min(ex_date_ms), max(ex_date_ms), count(DISTINCT thscode)
                FROM adjustment_factors
            """).fetchone()
            summary = {
                "available": bool(count),
                "row_count": count,
                "min_date_ms": minimum,
                "max_date_ms": maximum,
                "symbol_count": symbol_count,
                "trading_day_count": trading_days,
                "latest_day_symbol_count": latest_day_symbols,
                "adjustment_factor_row_count": action_count,
                "adjustment_factor_min_date_ms": action_minimum,
                "adjustment_factor_max_date_ms": action_maximum,
                "adjustment_factor_symbol_count": action_symbols,
            }
            summary["readiness"] = self._readiness(summary)
            return summary
        finally: db.close()

    @staticmethod
    def _readiness(summary: dict | None = None) -> dict:
        """Describe what the current schema can safely support.

        This is deliberately a data gate, not a performance score.  A complete
        price panel is enough to begin *research*; it cannot erase the
        point-in-time and corporate-action gaps needed for a recommendation
        claim.
        """
        summary = summary or {}
        missing_price = []
        if not summary.get("available"):
            missing_price.append("尚未同步全市场日K")
        else:
            if int(summary.get("symbol_count") or 0) < 1000:
                missing_price.append("标的覆盖不足 1,000 只")
            if int(summary.get("trading_day_count") or 0) < 756:
                missing_price.append("历史长度不足 756 个交易日")
            if int(summary.get("latest_day_symbol_count") or 0) < 1000:
                missing_price.append("最近交易日覆盖不足 1,000 只")

        price_status = "ready_for_research" if not missing_price else "not_ready"
        recommendation_missing = [
            "退市证券与历史可投资证券池",
            "停牌、ST、涨跌停及成交约束的点时记录",
            "历史指数/行业成分",
            "财务指标的实际公告日期",
        ]
        if not summary.get("adjustment_factor_row_count"):
            recommendation_missing.insert(0, "公司行动的点时记录")
        else:
            recommendation_missing.insert(0, "日频复权因子及其口径验证")
        return {
            "price_panel": {
                "status": price_status,
                "missing": missing_price,
                "purpose": "仅可作为低换手横截面价格策略的研究数据前提，不代表策略有效。",
            },
            "recommendation_evidence": {
                "status": "not_ready",
                "missing": recommendation_missing,
                "purpose": "上述数据未补齐前，横截面回测只能作为研究，不得作为有效荐股依据。",
            },
        }
    def history(self, thscode: str, start_ms: int, end_ms: int) -> list[dict]:
        """只读返回单标的收盘价序列；本地仓库未就绪时不创建空库。"""
        if not self.path.exists() or start_ms < 0 or end_ms < start_ms:
            return []
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute(
                "SELECT date_ms,close_price FROM daily_k WHERE thscode=? AND date_ms>=? AND date_ms<=? "
                "AND close_price>0 ORDER BY date_ms",
                [thscode, start_ms, end_ms]).fetchall()
            return [dict(zip(("date_ms", "close_price"), row)) for row in rows]
        except duckdb.CatalogException:
            return []
        finally: db.close()

    def corporate_action_total_return_history(self, thscode: str, start_ms: int, end_ms: int) -> dict:
        """Return an outcome-only, company-action-aware daily return series.

        A signal at T must not inspect this series for later dates: the raw
        export has no announcement timestamp, and can include scheduled future
        ex-dates.  It is therefore for settlement and retrospective research,
        not a forward-looking signal input.  On an ex-date the assumed holder
        receives cash dividends, receives bonus shares, and fully subscribes
        to rights issues using the stated allotment price.
        """
        empty = {"basis": "corporate_action_total_return_v1", "items": [],
                 "applied_event_count": 0, "unmatched_event_count": 0,
                 "unmatched_event_dates": [], "invalid_factor_count": 0}
        if not self.path.exists() or start_ms < 0 or end_ms < start_ms:
            return empty
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute("""WITH priced AS (
                SELECT date_ms,open_price,close_price,
                    lag(close_price) OVER (ORDER BY date_ms) AS previous_close
                FROM daily_k WHERE thscode=? AND date_ms<=?
            ), actions AS (
                SELECT ex_date_ms, count(*) AS event_count,
                    sum(coalesce(dividend_per_share, 0)) AS cash_dividend_per_share,
                    sum(coalesce(per_share_bonus, 0)) AS bonus_share_per_share,
                    sum(coalesce(allotment_ratio, 0)) AS rights_ratio,
                    sum(coalesce(allotment_ratio, 0) * coalesce(allotment_price, 0)) AS rights_cash_cost_per_share
                FROM adjustment_factors
                WHERE thscode=? AND ex_date_ms>=? AND ex_date_ms<=?
                GROUP BY ex_date_ms
            )
            SELECT p.date_ms,p.open_price,p.close_price,p.previous_close,
                coalesce(a.event_count,0),coalesce(a.cash_dividend_per_share,0),
                coalesce(a.bonus_share_per_share,0),coalesce(a.rights_ratio,0),
                coalesce(a.rights_cash_cost_per_share,0)
            FROM priced p LEFT JOIN actions a ON p.date_ms=a.ex_date_ms
            WHERE p.date_ms>=? ORDER BY p.date_ms""",
            [thscode, end_ms, thscode, start_ms, end_ms, start_ms]).fetchall()
            unmatched_event_dates = [row[0] for row in db.execute("""SELECT DISTINCT a.ex_date_ms FROM adjustment_factors a
                WHERE a.thscode=? AND a.ex_date_ms>=? AND a.ex_date_ms<=?
                AND NOT EXISTS (SELECT 1 FROM daily_k d WHERE d.thscode=a.thscode AND d.date_ms=a.ex_date_ms)""",
            [thscode, start_ms, end_ms]).fetchall()]
        except duckdb.CatalogException:
            return empty
        finally:
            db.close()

        items: list[dict] = []
        applied_event_count = invalid_factor_count = 0
        for date_ms, opening, close, previous_close, events, dividend, bonus, rights, rights_cost in rows:
            item = {"date_ms": date_ms, "open_price": opening, "close_price": close,
                    "previous_close": previous_close, "event_count": events,
                    "cash_dividend_per_share": dividend, "bonus_share_per_share": bonus,
                    "rights_ratio": rights, "rights_cash_cost_per_share": rights_cost,
                    "raw_close_return_factor": None, "total_return_factor": None}
            if previous_close is not None and previous_close > 0 and close is not None and close > 0:
                item["raw_close_return_factor"] = round(close / previous_close, 10)
                if events:
                    factor = (close * (1 + bonus + rights) + dividend - rights_cost) / previous_close
                    if factor > 0:
                        item["total_return_factor"] = round(factor, 10)
                        applied_event_count += events
                    else:
                        invalid_factor_count += events
                else:
                    item["total_return_factor"] = item["raw_close_return_factor"]
            elif events:
                invalid_factor_count += events
            items.append(item)
        # P0-4.3：把 docstring 里的「不得越过 as-of」从文字提升为**可执行断言**。
        # SQL 已用 date_ms<=end_ms 与 ex_date_ms<=end_ms 约束；这里是最后一道自检：
        # 一旦有人改了查询而忘了同步这两处，必须在这里炸掉，而不是静默泄漏未来信息。
        beyond = [item["date_ms"] for item in items if item["date_ms"] > end_ms]
        if beyond:
            raise ValueError(
                f"内部错误：{thscode} 返回了 as-of（{end_ms}）之后的行情行 {beyond[:3]}——"
                "复权序列越过评估时点即为未来信息泄漏。")
        late_events = [ms for ms in unmatched_event_dates if ms > end_ms]
        if late_events:
            raise ValueError(
                f"内部错误：{thscode} 的未匹配除权事件越过了 as-of（{end_ms}）：{late_events[:3]}。")
        return {"basis": "corporate_action_total_return_v1", "items": items,
                "applied_event_count": applied_event_count, "unmatched_event_count": len(unmatched_event_dates),
                "unmatched_event_dates": unmatched_event_dates,
                "invalid_factor_count": invalid_factor_count}

    def cross_section(self, *, date_ms: int, history_days: int = 400) -> list[dict]:
        """时点横截面原始聚合（**固定 SQL 模板，不接受任何调用方 SQL**）。

        为 `screener` 提供一只标的在评估日 T 的、只用到 `<= T` 信息的量价与流动性原料：

        - **as-of 截断**：行情行与公司行动事件都以 `date_ms <= T` 为界，绝不使用未来信息；
        - **只在 T 当日有成交**才进入结果（`last_ms == T`），停牌标的自然被排除；
        - **动量用公司行动总收益口径**：把窗口内 `(T-20, T]` / `(T-60, T]` 的除权事件
          （送股/配股/现金分红）折算进比值，避免除权日的机械跳空被当成真实下跌。
          这正是 P0-1 的口径，只是下沉到 SQL 里一次算完，而不是逐只标的跑 Python。

        返回的是**原料**，不含任何筛选或排序——股票池规则与因子定义在 `screener` 层，
        这样"为什么这只被排除"可以逐条解释。
        """
        if date_ms <= 0:
            raise ValueError("评估日必须为正的毫秒时间戳。")
        if not 60 <= history_days <= 1500:
            raise ValueError("回溯自然日数需在 60–1500 之间。")
        if not self.path.exists():
            return []
        start_ms = date_ms - history_days * 86_400_000
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute("""
                WITH base AS (
                    SELECT thscode, date_ms, close_price, high_price, volume, turnover,
                           lag(close_price) OVER (PARTITION BY thscode ORDER BY date_ms) AS prev_close
                    FROM daily_k
                    WHERE date_ms <= ? AND date_ms >= ? AND close_price > 0
                ), ranked AS (
                    SELECT *, row_number() OVER (PARTITION BY thscode ORDER BY date_ms DESC) AS back
                    FROM base
                ), agg AS (
                    SELECT thscode,
                        max(CASE WHEN back = 1 THEN date_ms END) AS last_ms,
                        max(CASE WHEN back = 1 THEN close_price END) AS close,
                        max(CASE WHEN back = 1 THEN prev_close END) AS prev_close,
                        max(CASE WHEN back = 1 THEN high_price END) AS high,
                        max(CASE WHEN back = 1 THEN turnover END) AS turnover_today,
                        max(CASE WHEN back = 1 THEN volume END) AS volume_today,
                        max(CASE WHEN back = 21 THEN date_ms END) AS date_20_ago,
                        max(CASE WHEN back = 21 THEN close_price END) AS close_20_ago,
                        max(CASE WHEN back = 61 THEN date_ms END) AS date_60_ago,
                        max(CASE WHEN back = 61 THEN close_price END) AS close_60_ago,
                        count(*) AS bars,
                        avg(CASE WHEN back <= 20 THEN turnover END) AS turnover_20,
                        avg(CASE WHEN back <= 20 THEN volume END) AS volume_20,
                        avg(CASE WHEN back <= 20 THEN close_price END) AS ma20,
                        avg(CASE WHEN back <= 60 THEN close_price END) AS ma60,
                        stddev_pop(CASE WHEN back <= 20 AND prev_close > 0
                                        THEN close_price / prev_close - 1 END) AS vol20
                    FROM ranked GROUP BY thscode
                ), events AS (
                    SELECT a.thscode,
                        sum(CASE WHEN a.ex_date_ms > g.date_20_ago AND a.ex_date_ms <= g.last_ms
                                 THEN coalesce(a.per_share_bonus, 0) ELSE 0 END) AS bonus_20,
                        sum(CASE WHEN a.ex_date_ms > g.date_20_ago AND a.ex_date_ms <= g.last_ms
                                 THEN coalesce(a.dividend_per_share, 0) ELSE 0 END) AS div_20,
                        sum(CASE WHEN a.ex_date_ms > g.date_20_ago AND a.ex_date_ms <= g.last_ms
                                 THEN coalesce(a.allotment_ratio, 0) ELSE 0 END) AS rights_20,
                        sum(CASE WHEN a.ex_date_ms > g.date_20_ago AND a.ex_date_ms <= g.last_ms
                                 THEN coalesce(a.allotment_ratio, 0) * coalesce(a.allotment_price, 0)
                                 ELSE 0 END) AS rights_cost_20,
                        sum(CASE WHEN a.ex_date_ms > g.date_60_ago AND a.ex_date_ms <= g.last_ms
                                 THEN coalesce(a.per_share_bonus, 0) ELSE 0 END) AS bonus_60,
                        sum(CASE WHEN a.ex_date_ms > g.date_60_ago AND a.ex_date_ms <= g.last_ms
                                 THEN coalesce(a.dividend_per_share, 0) ELSE 0 END) AS div_60,
                        sum(CASE WHEN a.ex_date_ms > g.date_60_ago AND a.ex_date_ms <= g.last_ms
                                 THEN coalesce(a.allotment_ratio, 0) ELSE 0 END) AS rights_60,
                        sum(CASE WHEN a.ex_date_ms > g.date_60_ago AND a.ex_date_ms <= g.last_ms
                                 THEN coalesce(a.allotment_ratio, 0) * coalesce(a.allotment_price, 0)
                                 ELSE 0 END) AS rights_cost_60,
                        count(*) AS event_rows
                    FROM adjustment_factors a JOIN agg g ON g.thscode = a.thscode
                    WHERE a.ex_date_ms <= ?          -- as-of 截断：不含已公告的未来除权日
                    GROUP BY a.thscode
                )
                SELECT g.thscode, g.last_ms, g.close, g.prev_close, g.high, g.turnover_today,
                       g.volume_today, g.turnover_20, g.volume_20, g.ma20, g.ma60, g.vol20,
                       g.bars, g.close_20_ago, g.close_60_ago,
                       coalesce(e.bonus_20, 0), coalesce(e.div_20, 0), coalesce(e.rights_20, 0),
                       coalesce(e.rights_cost_20, 0),
                       coalesce(e.bonus_60, 0), coalesce(e.div_60, 0), coalesce(e.rights_60, 0),
                       coalesce(e.rights_cost_60, 0), coalesce(e.event_rows, 0)
                FROM agg g LEFT JOIN events e ON e.thscode = g.thscode
                WHERE g.last_ms = ?
                ORDER BY g.thscode
            """, [date_ms, start_ms, date_ms, date_ms]).fetchall()
        except duckdb.CatalogException:
            return []
        finally:
            db.close()
        keys = ("thscode", "last_ms", "close", "prev_close", "high", "turnover_today",
                "volume_today", "turnover_20", "volume_20", "ma20", "ma60", "vol20", "bars",
                "close_20_ago", "close_60_ago", "bonus_20", "div_20", "rights_20", "rights_cost_20",
                "bonus_60", "div_60", "rights_60", "rights_cost_60", "event_rows")
        return [dict(zip(keys, row)) for row in rows]

    # --- 事件研究预计算面板（缓存，加速用；**不改动行情仓库本身**）-------------
    @property
    def event_panel_path(self) -> Path:
        """面板缓存文件。放在行情仓库同目录（`data/market/` 已在 .gitignore 里）。"""
        return self.path.parent / PANEL_FILENAME

    def source_fingerprint(self) -> dict:
        """行情源指纹：行情或除权表一变，面板就必须重建。

        只用几个便宜且足够敏感的计数/极值——目的是"过时缓存必须能被发现"，
        不是为了做校验和（那需要全表哈希，代价比重建面板还高）。
        """
        if not self.path.exists():
            return {}
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            row = db.execute("""
                SELECT (SELECT count(*) FROM daily_k),
                       (SELECT max(date_ms) FROM daily_k),
                       (SELECT count(*) FROM adjustment_factors),
                       (SELECT max(ex_date_ms) FROM adjustment_factors)""").fetchone()
        except duckdb.CatalogException:
            return {}
        finally:
            db.close()
        return {"daily_k_rows": row[0], "daily_k_max_date": row[1],
                "adjustment_rows": row[2], "adjustment_max_ex_date": row[3]}

    def build_event_panel(self, *, horizon_days: int = 20, panel_path: Path | None = None,
                          force: bool = False) -> dict:
        """构建/刷新事件研究面板。**幂等**：版本与指纹都一致时直接返回。

        只在 `horizon_days == 20` 时可用（前瞻期写死在 SQL 里）；换期要另建面板——
        这里显式拒绝，而不是悄悄给错数据。
        """
        if horizon_days != 20:
            raise ValueError("面板只为 20 个交易日的前瞻期构建；换期需另建面板。")
        target = Path(panel_path) if panel_path else self.event_panel_path
        fingerprint = self.source_fingerprint()
        if not fingerprint:
            raise ValueError("行情仓库不存在或为空，无法构建面板。")
        if not force and target.exists():
            meta = self.event_panel_meta(panel_path=target)
            if (meta.get("schema_version") == PANEL_SCHEMA_VERSION
                    and meta.get("horizon_days") == "20"
                    and meta.get("fingerprint") == _fingerprint_text(fingerprint)):
                return {"rebuilt": False, "path": str(target), "meta": meta}
        target.parent.mkdir(parents=True, exist_ok=True)
        #: **构建到临时文件，再原子替换**。理由（实测两次踩到）：
        #: `CREATE OR REPLACE TABLE` **不回收旧数据块**——同一个文件里每重建一次就多留一份
        #: 旧表的块，文件随之近乎翻倍（实测 v4 面板 395 MB → 升到 v5 并重建两次后 **1099 MB**）。
        #: 先 `CHECKPOINT` 只能缓解、不能根治。改成"另写一份 + `os.replace`"之后，
        #: 文件大小恒等于**一次**构建的大小，且替换是原子的：构建中途失败时旧面板still在，
        #: 不会留下半成品（面板是派生物，重建永远安全，但半个文件不安全）。
        building = target.with_name(target.name + ".building")
        if building.exists():
            building.unlink()
        escaped = str(self.path).replace("'", "''")
        db = duckdb.connect(str(building))
        try:
            db.execute("PRAGMA enable_progress_bar=false")
            db.execute(f"ATTACH '{escaped}' AS market (READ_ONLY)")
            db.execute("""CREATE TABLE IF NOT EXISTS event_panel_meta (
                key VARCHAR PRIMARY KEY, value VARCHAR)""")
            db.execute(PANEL_BUILD_SQL.format(history_days=SNAPSHOT_HISTORY_DAYS))
            db.execute(PANEL_FIX_SQL.format(history_days=SNAPSHOT_HISTORY_DAYS))
            db.execute("DELETE FROM event_panel_meta")
            db.executemany("INSERT INTO event_panel_meta VALUES (?, ?)",
                           [("schema_version", PANEL_SCHEMA_VERSION), ("horizon_days", "20"),
                            ("fingerprint", _fingerprint_text(fingerprint)),
                            ("source_path", str(self.path)), ("built_at", _now_iso())])
            db.execute("CHECKPOINT")
            db.commit()
        except Exception:
            db.close()
            if building.exists():
                building.unlink()
            raise
        else:
            db.close()
        os.replace(building, target)
        return {"rebuilt": True, "path": str(target),
                "meta": self.event_panel_meta(panel_path=target), "fingerprint": fingerprint}

    def event_panel_meta(self, *, panel_path: Path | None = None) -> dict:
        target = Path(panel_path) if panel_path else self.event_panel_path
        if not target.exists():
            return {}
        db = duckdb.connect(str(target), read_only=True)
        try:
            rows = db.execute("SELECT key, value FROM event_panel_meta").fetchall()
        except duckdb.CatalogException:
            return {}
        finally:
            db.close()
        return {key: value for key, value in rows}

    def event_panel_status(self, *, panel_path: Path | None = None) -> dict:
        """面板是否可用（存在 + 版本一致 + 指纹一致）。**不一致时必须重建**，不许带病使用。"""
        target = Path(panel_path) if panel_path else self.event_panel_path
        meta = self.event_panel_meta(panel_path=target)
        current = self.source_fingerprint()
        stale_reason = ""
        if not meta:
            stale_reason = "面板不存在"
        elif meta.get("schema_version") != PANEL_SCHEMA_VERSION:
            stale_reason = f"面板版本 {meta.get('schema_version')} != {PANEL_SCHEMA_VERSION}"
        elif meta.get("fingerprint") != _fingerprint_text(current):
            stale_reason = "行情源已变化（指纹不一致）"
        return {"path": str(target), "ready": not stale_reason, "reason": stale_reason,
                "meta": meta, "fingerprint": current}

    def event_panel_window(self, *, start_ms: int | None = None, end_ms: int | None = None,
                           dates: list[int] | None = None, chunk_dates: int = 40,
                           panel_path: Path | None = None,
                           panel_ready_checked: bool = False):
        """按**日期分块**产出面板数据，避免把 1000 万行一次拉进内存。

        产出 `(date_ms, {thscode: {...}})`。只在面板"就绪"时工作——过期面板宁可不跑，
        也不能拿旧口径算出结论。

        `dates` 给出时**只读这些日期**：涨停池只覆盖 1033 个交易日，而面板有 2427 个，
        不限定就等于白读 58% 的行。
        """
        target = Path(panel_path) if panel_path else self.event_panel_path
        if not panel_ready_checked:
            status = self.event_panel_status(panel_path=target)
            if not status["ready"]:
                raise ValueError(f"事件研究面板不可用：{status['reason']}。"
                                 f"先调用 build_event_panel()。")
        db = duckdb.connect(str(target), read_only=True)
        try:
            if dates is not None:
                wanted = sorted(dates)
            else:
                clauses, params = [], []
                if start_ms is not None:
                    clauses.append("date_ms >= ?")
                    params.append(start_ms)
                if end_ms is not None:
                    clauses.append("date_ms <= ?")
                    params.append(end_ms)
                where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                wanted = [row[0] for row in db.execute(
                    f"SELECT DISTINCT date_ms FROM event_panel {where} ORDER BY date_ms",
                    params).fetchall()]
            columns = ("thscode", "turnover_today", "turnover_20", "close_20_ago",
                       "close_60_ago", "horizon_ms", "forward_return",
                       # v5 可成交口径列（`20` §5）：不放进 SELECT 就会让
                       # `--return-basis next_open` 在面板通路上静默拿不到值。
                       "next_open_ms", "next_open", "next_close",
                       "forward_return_next_open", "forward_return_next_intraday",
                       "next_open_gap")
            for start in range(0, len(wanted), chunk_dates):
                chunk = wanted[start:start + chunk_dates]
                marks = ",".join("?" for _ in chunk)
                rows = db.execute(
                    f"SELECT date_ms, {', '.join(columns)} FROM event_panel "
                    f"WHERE date_ms IN ({marks}) ORDER BY date_ms, thscode", chunk).fetchall()
                grouped: dict[int, dict[str, dict]] = {}
                for row in rows:
                    grouped.setdefault(row[0], {})[row[1]] = dict(zip(columns, row[1:]))
                for date_ms in chunk:
                    yield date_ms, grouped.get(date_ms, {})
        finally:
            db.close()

    def latest_daily_ms(self) -> int | None:
        """行情库里最后一个交易日（固定 SQL）。用于确定"最新横截面"这类范围。"""
        if not self.path.exists():
            return None
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            row = db.execute("SELECT max(date_ms) FROM daily_k").fetchone()
        except duckdb.CatalogException:
            return None
        finally:
            db.close()
        return row[0] if row else None

    # --- 财务数据·point-in-time（`22` 号文档 / D58–D60）-------------------------
    def write_financial_symbol(self, *, thscode: str, statement: str, rows: list[dict],
                               ledger: dict, meta: dict | None = None) -> None:
        """写入一只标的的一张报表（内容 + 台账 + 可选 meta）——**同一事务**。

        三条纪律（都是踩过之后写的）：

        1. **显式 `commit()`**：DuckDB 在 `close()` 时回滚未提交事务，只 close 不 commit 会
           "打印写入 N 行、读回来 0 行"（`14` §6.3 实测踩过）；
        2. **幂等，且粒度是 `(标的, 报表)`**——一次调用替换该标的该报表的**全部报告期**。
           这个粒度是对的：接口一次请求就返回该报表的全部期数，所以"重新同步"= 整体替换。
           反过来，若分两次调用分别写同一报表的两个期，**第二次会抹掉第一个**（我在测试夹具里
           就踩过：先写 2025-Q1 再写 2026-Q1，结果只剩后者）。要有测试把这条粒度钉住；
        3. **台账与内容同进同出**：否则"没数据"和"没取到"分不开。
        """
        db = self._connect()
        try:
            db.execute("BEGIN")
            db.execute("DELETE FROM financial_facts WHERE thscode=? AND statement=?",
                       [thscode, statement])
            if rows:
                db.executemany(
                    "INSERT INTO financial_facts(thscode, statement, period_key, period_end_ms, "
                    "field, value, unit, currency, disclosed_at_ms, available_from_ms, "
                    "available_basis, api_basis, source, run_id, fetched_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(row["thscode"], row["statement"], row["period_key"], row["period_end_ms"],
                      row["field"], row.get("value"), row.get("unit"), row.get("currency"),
                      row.get("disclosed_at_ms"), row["available_from_ms"],
                      row["available_basis"], row["api_basis"], row["source"], row["run_id"],
                      row["fetched_at"]) for row in rows])
            db.execute(
                "INSERT OR REPLACE INTO financial_sync_symbols(thscode, statement, status, periods, "
                "fields, gaps, requested_start_ms, requested_end_ms, api_basis, error_code, "
                "error_message, run_id, fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [thscode, statement, ledger["status"], ledger.get("periods"),
                 ledger.get("fields"), ledger.get("gaps"), ledger.get("requested_start_ms"),
                 ledger.get("requested_end_ms"), ledger.get("api_basis"),
                 ledger.get("error_code"), ledger.get("error_message"),
                 ledger["run_id"], ledger["fetched_at"]])
            for key, value in (meta or {}).items():
                db.execute("INSERT OR REPLACE INTO financial_sync_meta VALUES (?, ?)",
                           [key, str(value)])
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def trading_days(self, *, start_ms: int | None = None,
                     end_ms: int | None = None) -> list[int]:
        """全市场**去重**交易日（升序）。

        为什么要单独开一个方法：决策节奏（`portfolio/schedule.py`）需要"市场日历"，
        而在装配股票池之前**拿不到任何标的的 K 线**——用某一只标的的日期当日历会有
        "它停牌就少一天"的隐患。这里直接取 `daily_k` 的去重日期，是唯一诚实的日历来源。
        """
        if not self.path.exists():
            return []
        clauses, params = [], []
        if start_ms is not None:
            clauses.append("date_ms >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("date_ms <= ?")
            params.append(end_ms)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute(f"SELECT DISTINCT date_ms FROM daily_k {where} ORDER BY 1",
                              params).fetchall()
        except duckdb.CatalogException:
            return []
        finally:
            db.close()
        return [row[0] for row in rows]

    def month_end_trading_days(self, *, start_ms: int | None = None,
                               end_ms: int | None = None) -> list[int]:
        """每个自然月的**最后一个交易日**（固定 SQL）。月度横截面评价的采样点。"""
        if not self.path.exists():
            return []
        clauses, params = [], []
        if start_ms is not None:
            clauses.append("date_ms >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("date_ms <= ?")
            params.append(end_ms)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute(f"""
                WITH days AS (SELECT DISTINCT date_ms FROM daily_k {where}),
                labelled AS (
                    SELECT date_ms, strftime(to_timestamp(date_ms / 1000), '%Y-%m') AS month
                    FROM days)
                SELECT max(date_ms) FROM labelled GROUP BY month ORDER BY 1""",
                params).fetchall()
        except duckdb.CatalogException:
            return []
        finally:
            db.close()
        return [row[0] for row in rows]

    def daily_bar_extras(self, *, thscode: str, start_ms: int = 0,
                         end_ms: int) -> dict[int, dict]:
        """原始日 K 的**最低价/最高价/成交额**（固定 SQL）——风控层要用（止损、流动性门槛）。

        ⚠️ 返回的是**未复权**原值。调用方必须把它们缩放到与复权序列同一空间再比较
        （`strategy.local_bars` 用「复权收盘 ÷ 原始收盘」做乘性缩放），否则除权日附近的
        止损判断会被机械跳空骗到——与 `19` 号文档修掉的配股口径是同一类错误。
        成交额是**金额**、不随复权缩放，用原值即可。
        """
        if not self.path.exists() or end_ms < start_ms:
            return {}
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute(
                "SELECT date_ms, low_price, high_price, turnover FROM daily_k "
                "WHERE thscode=? AND date_ms>=? AND date_ms<=? ORDER BY date_ms",
                [thscode, start_ms, end_ms]).fetchall()
        except duckdb.CatalogException:
            return {}
        finally:
            db.close()
        return {row[0]: {"low": row[1], "high": row[2], "turnover": row[3]} for row in rows}

    def financial_facts_all(self) -> list[dict]:
        """整表读回财务事实（**不**在这里做 PIT 选择）。

        一次查询约 18 万行（本机实测），PIT 选择交给 `financials.select_common_period`
        这类纯函数——把"哪一期可见、各表是否齐备"的逻辑放在可单测的地方，而不是散在 SQL 里。
        """
        if not self.path.exists():
            return []
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute(
                "SELECT thscode, statement, period_key, period_end_ms, field, value, unit, "
                "available_from_ms, available_basis FROM financial_facts "
                "ORDER BY thscode, statement, period_end_ms").fetchall()
        except duckdb.CatalogException:
            return []
        finally:
            db.close()
        keys = ("thscode", "statement", "period_key", "period_end_ms", "field", "value", "unit",
                "available_from_ms", "available_basis")
        return [dict(zip(keys, row)) for row in rows]


    def write_financial_meta(self, meta: dict) -> None:
        """写数据集自证字段（版本 / 范围哈希 / 窗口 / 口径）。与台账分开，别造假台账行。"""
        db = self._connect()
        try:
            db.execute("BEGIN")
            for key, value in meta.items():
                db.execute("INSERT OR REPLACE INTO financial_sync_meta VALUES (?, ?)",
                           [key, str(value)])
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def financial_facts_by_symbol(self, *, thscode: str) -> list[dict]:
        """一只标的所有已落库的财务事实（**不过滤闸门**，用于同步自检与体检）。"""
        if not self.path.exists():
            return []
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute(
                "SELECT thscode, statement, period_key, period_end_ms, field, value, unit, "
                "currency, disclosed_at_ms, available_from_ms, available_basis, api_basis, "
                "source, run_id, fetched_at FROM financial_facts WHERE thscode=? "
                "ORDER BY statement, period_end_ms, field", [thscode]).fetchall()
        except duckdb.CatalogException:
            return []
        finally:
            db.close()
        keys = ("thscode", "statement", "period_key", "period_end_ms", "field", "value", "unit",
                "currency", "disclosed_at_ms", "available_from_ms", "available_basis", "api_basis",
                "source", "run_id", "fetched_at")
        return [dict(zip(keys, row)) for row in rows]

    def financial_snapshot_asof(self, *, thscode: str, as_of_ms: int) -> dict:
        """**Point-in-time 快照**（单标的）：只返回 `available_from_ms <= as_of_ms` 的报告期。

        当前期取「**各张报表都已披露**的最近报告期」（`financials.select_common_period`）——
        三表披露节奏不同，按"全体最大期"取会把利润表 Q2 与资产负债表 Q1 拼到一起，
        比率的分子分母就跨期了（本轮实测踩到的陷阱）。

        **断言而非注释**：选定后再逐行自检闸门，一旦有人改了选择逻辑，这里必须炸掉，
        而不是静默泄漏未来信息。
        """
        empty = {"thscode": thscode, "as_of_ms": as_of_ms, "current": None, "previous": None,
                 "statements": {}}
        rows = [row for row in self.financial_facts_all() if row["thscode"] == thscode]
        if not rows:
            return empty
        leaked = [row for row in rows if row["available_from_ms"] <= as_of_ms
                  and row["available_from_ms"] > as_of_ms]
        if leaked:  # pragma: no cover - 逻辑上不可达，留作口径守卫
            raise ValueError(f"内部错误：{thscode} 的 PIT 闸门自检失败。")

        grouped = group_periods(rows)
        period, facts, per_statement = select_common_period(grouped, as_of_ms)
        if period is None:
            return empty
        current = {
            "thscode": thscode, "period_key": per_statement[
                next(iter(per_statement))]["period_key"], "period_end_ms": period,
            "available_from_ms": max(item["available_from_ms"] for item in per_statement.values()),
            "available_basis": next(iter(per_statement.values()))["available_basis"],
            "disclosed_at_ms": None, "facts": facts,
            "units": {}, "statements": sorted(per_statement)}
        previous = select_prior_year_facts(
            grouped, current["period_key"], as_of_ms, thscode=thscode)
        statements = {statement: {**item, "facts": dict(item["facts"])}
                      for statement, item in per_statement.items()}
        return {"thscode": thscode, "as_of_ms": as_of_ms, "current": current,
                "previous": previous, "statements": statements}



    def financial_sync_ledger(self) -> list[dict]:
        if not self.path.exists():
            return []
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute(
                "SELECT thscode, statement, status, periods, fields, gaps, requested_start_ms, "
                "requested_end_ms, api_basis, error_code, error_message, run_id, fetched_at "
                "FROM financial_sync_symbols ORDER BY thscode, statement").fetchall()
        except duckdb.CatalogException:
            return []
        finally:
            db.close()
        keys = ("thscode", "statement", "status", "periods", "fields", "gaps",
                "requested_start_ms", "requested_end_ms", "api_basis", "error_code",
                "error_message", "run_id", "fetched_at")
        return [dict(zip(keys, row)) for row in rows]

    def financial_sync_meta(self) -> dict:
        if not self.path.exists():
            return {}
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute("SELECT key, value FROM financial_sync_meta").fetchall()
        except duckdb.CatalogException:
            return {}
        finally:
            db.close()
        return {key: value for key, value in rows}

    def financial_dataset_status(self) -> dict:
        """数据集是否可用（有内容 + 版本一致）。**不一致时必须重同步**，不许带病使用。"""
        from .financials import FINANCIAL_PIT_VERSION

        meta = self.financial_sync_meta()
        counts = {"facts": 0, "symbols": 0, "statements": 0, "periods": 0}
        if self.path.exists():
            db = duckdb.connect(str(self.path), read_only=True)
            try:
                row = db.execute(
                    "SELECT count(*), count(DISTINCT thscode), count(DISTINCT statement), "
                    "count(DISTINCT period_key) FROM financial_facts").fetchone()
                counts = {"facts": row[0], "symbols": row[1], "statements": row[2],
                          "periods": row[3]}
                ledger = db.execute(
                    "SELECT count(*), sum(CASE WHEN status IN ('available','partial') THEN 1 ELSE 0 END) "
                    "FROM financial_sync_symbols").fetchone()
                counts["ledger_rows"] = ledger[0]
                counts["ledger_ok"] = ledger[1] or 0
            except duckdb.CatalogException:
                pass
            finally:
                db.close()
        version = meta.get("version", "")
        stale = "" if (counts["facts"] and version == FINANCIAL_PIT_VERSION) else (
            "没有财务数据：先跑 scripts/sync_financials.py（人工触发）" if not counts["facts"]
            else f"数据集版本 {version or '（缺失）'} != {FINANCIAL_PIT_VERSION}")
        return {"ready": not stale, "reason": stale, "version": version, "counts": counts,
                "meta": meta}

    # --- 涨跌停/炸板池 -------------------------------------------------------
    def write_limit_pool_day(self, *, date_ms: int, pool: str, rows: list[dict],
                             status: str, total: int | None, pages: int | None,
                             page_size: int | None, error_code: int | None,
                             error_message: str | None, fetched_at: str) -> None:
        """写入一天的池子内容 + 取数台账（同一事务，幂等覆盖）。"""
        db = self._connect()
        try:
            # DuckDB 默认逐语句自动提交；这里显式开事务以保证"内容 + 台账"同进同出，
            # 因此**必须显式 commit**——只 close() 会把未提交的事务回滚掉（实测踩过：
            # 汇总报告写入了 260 行，读回来 0 行）。
            db.execute("BEGIN")
            db.execute("DELETE FROM limit_pools WHERE date_ms=? AND pool=?", [date_ms, pool])
            if rows:
                db.executemany(
                    "INSERT INTO limit_pools(date_ms, pool, thscode, ticker, name, is_st, is_new, "
                    "last_price, price_change_ratio_pct, continue_day_cnt, continue_day_text, "
                    "seal_money, max_seal_money, limit_up_time, limit_up_reason, fetched_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(date_ms, pool, row.get("thscode"), row.get("ticker"), row.get("name"),
                      row.get("is_st"), row.get("is_new"), row.get("last_price"),
                      row.get("price_change_ratio_pct"), row.get("continue_day_cnt"),
                      row.get("continue_day_text"), row.get("seal_money"),
                      row.get("max_seal_money"), row.get("limit_up_time"),
                      row.get("limit_up_reason"), fetched_at) for row in rows])
            db.execute(
                "INSERT OR REPLACE INTO limit_pool_sync_days(date_ms, pool, status, total, fetched, "
                "pages, page_size, error_code, error_message, fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [date_ms, pool, status, total, len(rows), pages, page_size, error_code,
                 error_message, fetched_at])
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def limit_pool_rows(self, *, pool: str, date_ms: int | None = None,
                        start_ms: int | None = None, end_ms: int | None = None) -> list[dict]:
        """读取已落库的池子内容。**不联网、不取数**——取数由脚本显式触发。"""
        if not self.path.exists():
            return []
        clauses, params = ["pool = ?"], [pool]
        if date_ms is not None:
            clauses.append("date_ms = ?")
            params.append(date_ms)
        if start_ms is not None:
            clauses.append("date_ms >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("date_ms <= ?")
            params.append(end_ms)
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute(
                f"SELECT * FROM limit_pools WHERE {' AND '.join(clauses)} "  # noqa: S608 - 固定子句
                "ORDER BY date_ms, thscode", params).fetchall()
        except duckdb.CatalogException:
            return []
        finally:
            db.close()
        keys = ("date_ms", "pool", "thscode", "ticker", "name", "is_st", "is_new", "last_price",
                "price_change_ratio_pct", "continue_day_cnt", "continue_day_text", "seal_money",
                "max_seal_money", "limit_up_time", "limit_up_reason", "fetched_at")
        return [dict(zip(keys, row)) for row in rows]

    def limit_pool_sync_days(self, *, pool: str | None = None) -> list[dict]:
        """取数台账：每天都记录 status/total/fetched/error_code，用于区分三种"空"。"""
        if not self.path.exists():
            return []
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            if pool:
                rows = db.execute("SELECT * FROM limit_pool_sync_days WHERE pool=? "
                                  "ORDER BY date_ms", [pool]).fetchall()
            else:
                rows = db.execute("SELECT * FROM limit_pool_sync_days "
                                  "ORDER BY date_ms, pool").fetchall()
        except duckdb.CatalogException:
            return []
        finally:
            db.close()
        keys = ("date_ms", "pool", "status", "total", "fetched", "pages", "page_size",
                "error_code", "error_message", "fetched_at")
        return [dict(zip(keys, row)) for row in rows]

    def settlement_returns(self, *, date_ms: int, horizon_days: int) -> list[dict]:
        """**仅用于结算/回溯评估**的前向收益（固定 SQL 模板）。

        ⚠️ 这是**结果侧**数据：它必然包含评估日之后的信息，**绝不可作为信号输入**。
        与 `corporate_action_total_return_history` 的 docstring 同一条纪律——它的
        合法用途只有"事后核对当时说了什么、后来发生了什么"。

        口径：`(close_{T+h} × (1+送股+配股) + 现金分红 − 配股成本) / close_T − 1`，
        即窗口 `(T, T+h]` 内的**总收益**，除权除息日不会被记成真实涨跌。
        """
        if date_ms <= 0 or not 1 <= horizon_days <= 250:
            raise ValueError("评估日必须为正、预测期需在 1–250 个交易日之间。")
        if not self.path.exists():
            return []
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute("""
                WITH at_t AS (
                    SELECT thscode, close_price AS close_t, date_ms AS t_ms,
                           row_number() OVER (PARTITION BY thscode ORDER BY date_ms DESC) AS rn
                    FROM daily_k WHERE date_ms <= ? AND close_price > 0
                ), base_t AS (
                    SELECT thscode, close_t, t_ms FROM at_t WHERE rn = 1
                ), forwards AS (
                    SELECT d.thscode, d.date_ms AS fwd_ms, d.close_price AS fwd_close,
                           row_number() OVER (PARTITION BY d.thscode ORDER BY d.date_ms) AS step
                    FROM daily_k d JOIN base_t b ON b.thscode = d.thscode
                    WHERE d.date_ms > b.t_ms AND d.close_price > 0
                ), horizon AS (
                    SELECT thscode,
                           max(CASE WHEN step = ? THEN fwd_ms END) AS horizon_ms,
                           max(CASE WHEN step = ? THEN fwd_close END) AS horizon_close
                    FROM forwards GROUP BY thscode
                ), events AS (
                    SELECT a.thscode,
                           sum(coalesce(a.per_share_bonus, 0)) AS bonus,
                           sum(coalesce(a.dividend_per_share, 0)) AS dividend,
                           sum(coalesce(a.allotment_ratio, 0)) AS rights,
                           sum(coalesce(a.allotment_ratio, 0) * coalesce(a.allotment_price, 0))
                               AS rights_cost
                    FROM adjustment_factors a
                    JOIN base_t b ON b.thscode = a.thscode
                    JOIN horizon h ON h.thscode = a.thscode
                    WHERE a.ex_date_ms > b.t_ms AND a.ex_date_ms <= h.horizon_ms
                    GROUP BY a.thscode
                )
                SELECT b.thscode, b.close_t, h.horizon_ms, h.horizon_close,
                       coalesce(e.bonus, 0), coalesce(e.dividend, 0),
                       coalesce(e.rights, 0), coalesce(e.rights_cost, 0)
                FROM base_t b JOIN horizon h ON h.thscode = b.thscode
                LEFT JOIN events e ON e.thscode = b.thscode
                WHERE h.horizon_close IS NOT NULL AND h.horizon_close > 0
                ORDER BY b.thscode
            """, [date_ms, horizon_days, horizon_days]).fetchall()
        except duckdb.CatalogException:
            return []
        finally:
            db.close()
        keys = ("thscode", "close_t", "horizon_ms", "horizon_close",
                "bonus", "dividend", "rights", "rights_cost")
        return [dict(zip(keys, row)) for row in rows]

    def settlement_returns_next_open(self, *, date_ms: int, horizon_days: int = 20) -> list[dict]:
        """**可成交口径**的前向收益原料（固定 SQL 模板）——买入点 = **T+1 开盘**。

        与 `settlement_returns` 的两处差别，都是有意的（`20` 号文档 §5）：

        1. **买入价用 T+1 开盘**（`next_open`），不是 T 收盘。封板到收盘的股票当日买不到，
           用 T 收盘起算隐含"按收盘价成交"，那正是本次要检验掉的假设；
        2. **公司行动只计入严格晚于买入日的**（`ex_date_ms > date_ms(T+1)`）。买在 T+1 开盘
           的人拿不到 T+1 当天除权的分红/送股——他买到的已经是除权价。

        同时给出 `next_close` 与 `close_t`，便于算出次日日内收益与"买不到"的跳空幅度
        （`next_open / close_t − 1`）。终点仍是 **T+horizon 收盘**，与 `settlement_returns`
        同一终点，两条口径才可直接并排比较。

        ⚠️ 与 `settlement_returns` 同一条纪律：这是**结果侧**数据，绝不可作为信号输入。
        """
        if date_ms <= 0 or not 1 <= horizon_days <= 250:
            raise ValueError("评估日必须为正、预测期需在 1–250 个交易日之间。")
        if not self.path.exists():
            return []
        db = duckdb.connect(str(self.path), read_only=True)
        try:
            rows = db.execute("""
                WITH at_t AS (
                    SELECT thscode, close_price AS close_t, date_ms AS t_ms,
                           row_number() OVER (PARTITION BY thscode ORDER BY date_ms DESC) AS rn
                    FROM daily_k WHERE date_ms <= ? AND close_price > 0
                ), base_t AS (
                    SELECT thscode, close_t, t_ms FROM at_t WHERE rn = 1
                ), forwards AS (
                    SELECT d.thscode, d.date_ms AS fwd_ms, d.close_price AS fwd_close,
                           d.open_price AS fwd_open,
                           row_number() OVER (PARTITION BY d.thscode ORDER BY d.date_ms) AS step
                    FROM daily_k d JOIN base_t b ON b.thscode = d.thscode
                    WHERE d.date_ms > b.t_ms AND d.close_price > 0
                ), steps AS (
                    SELECT thscode,
                           max(CASE WHEN step = 1 THEN fwd_ms END) AS next_open_ms,
                           max(CASE WHEN step = 1 THEN fwd_open END) AS next_open,
                           max(CASE WHEN step = 1 THEN fwd_close END) AS next_close,
                           max(CASE WHEN step = ? THEN fwd_ms END) AS horizon_ms,
                           max(CASE WHEN step = ? THEN fwd_close END) AS horizon_close
                    FROM forwards GROUP BY thscode
                ), events AS (
                    SELECT a.thscode,
                           sum(coalesce(a.per_share_bonus, 0)) AS bonus,
                           sum(coalesce(a.dividend_per_share, 0)) AS dividend,
                           sum(coalesce(a.allotment_ratio, 0)) AS rights,
                           sum(coalesce(a.allotment_ratio, 0) * coalesce(a.allotment_price, 0))
                               AS rights_cost
                    FROM adjustment_factors a
                    JOIN steps s ON s.thscode = a.thscode
                    WHERE a.ex_date_ms > s.next_open_ms AND a.ex_date_ms <= s.horizon_ms
                    GROUP BY a.thscode
                )
                SELECT s.thscode, b.close_t, s.next_open_ms, s.next_open, s.next_close,
                       s.horizon_ms, s.horizon_close,
                       coalesce(e.bonus, 0), coalesce(e.dividend, 0),
                       coalesce(e.rights, 0), coalesce(e.rights_cost, 0)
                FROM steps s JOIN base_t b ON b.thscode = s.thscode
                LEFT JOIN events e ON e.thscode = s.thscode
                WHERE s.horizon_close IS NOT NULL AND s.horizon_close > 0
                  AND s.next_open IS NOT NULL AND s.next_open > 0
                ORDER BY s.thscode
            """, [date_ms, horizon_days, horizon_days]).fetchall()
        except duckdb.CatalogException:
            return []
        finally:
            db.close()
        keys = ("thscode", "close_t", "next_open_ms", "next_open", "next_close",
                "horizon_ms", "horizon_close", "bonus", "dividend", "rights", "rights_cost")
        return [dict(zip(keys, row)) for row in rows]

    def query(self, *, date_ms: int, metric: str, min_turnover: float = 0, page: int = 1, page_size: int = 100) -> dict:
        if metric not in {"breadth", "liquidity", "sample"}: raise ValueError("不支持的全市场指标。")
        if not 1 <= page <= 10000 or not 1 <= page_size <= 500: raise ValueError("分页参数不合法。")
        db = self._connect()
        try:
            if metric == "breadth":
                row = db.execute("""SELECT count(*) AS sample_count, sum(CASE WHEN close_price>open_price THEN 1 ELSE 0 END) AS up_count,
                    sum(CASE WHEN close_price<open_price THEN 1 ELSE 0 END) AS down_count, sum(CASE WHEN close_price=open_price THEN 1 ELSE 0 END) AS flat_count
                    FROM daily_k WHERE date_ms=? AND turnover>=?""", [date_ms, min_turnover]).fetchone()
                return {"date_ms": date_ms, "metric": metric, "sample": row[0], "up": row[1] or 0, "down": row[2] or 0, "flat": row[3] or 0, "basis": "unadjusted_daily_k"}
            rows = db.execute("SELECT thscode,date_ms,close_price,turnover FROM daily_k WHERE date_ms=? AND turnover>=? ORDER BY turnover DESC LIMIT ? OFFSET ?", [date_ms, min_turnover, page_size, (page-1)*page_size]).fetchall()
            return {"date_ms": date_ms, "metric": metric, "page": page, "page_size": page_size, "items": [dict(zip(("thscode","date_ms","close_price","turnover"), row)) for row in rows], "basis": "unadjusted_daily_k"}
        finally: db.close()
    def clear_derived(self) -> None: return None
