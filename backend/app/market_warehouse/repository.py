"""Fixed DuckDB schema and queries; no caller-supplied SQL."""
from __future__ import annotations
from pathlib import Path
import duckdb
from .schema import ADJUSTMENT_FACTOR_COLUMNS, DAILY_COLUMNS, FROZEN_READONLY_DATASETS


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
