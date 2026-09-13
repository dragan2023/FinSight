"""可成交口径（面板 v5）的守卫测试。

这一轮的核心风险**不是**"跑不出来"，而是**算错了还看不出来**：

1. **除权截断点写错**——买在 T+1 开盘的人拿不到 T+1 当天除权的分红/送股（他买到的就是
   除权价），但 T+2 及以后的必须算进去。差一格就会系统性高估或低估可成交收益，
   而且因为方向一致，**在汇总层面完全看不出来**。所以这里用一个"人为构造的两种口径
   必然不同"的夹具把公式钉死（`test_corporate_action_cutoff_*`）。
2. **口径悄悄回落到不可成交的那条**——`return_basis` 拼写错了必须报错，不许默认成
   `close_t` 然后拿"封板买不到"的价格当成交价汇报。
3. **描述量被当成检验端点**——`next_open_gap` 只报均值，不给 t 值、不入门槛计数。
"""
from __future__ import annotations

import pytest

from backend.app.market_warehouse.repository import (
    PANEL_SCHEMA_VERSION,
    WarehouseRepository,
)
from backend.app.screener.events import (
    OUTCOME_BASES,
    OUTCOME_BASIS_DESIGNS,
    LimitUpEventStudy,
)

DAY_MS = 86_400_000
SYMBOL = "AAA.SZ"
#: 评估日 T = 第 10 根 K 线；买入日 T+1 = 第 11 根。
T_MS = DAY_MS * 10
BUY_MS = DAY_MS * 11
BARS = 30
CLOSE = 12.0
BUY_OPEN = 8.5                 # T+1 开盘（故意不取"除权价"的理论值，好让两种口径分开）
BONUS_ON_BUY_DAY = 0.5         # T+1 当天除权：可成交口径**不得**计入
BONUS_AFTER_BUY_DAY = 0.2      # T+2 除权：可成交口径**必须**计入
DIVIDEND_ON_BUY_DAY = 0.3
RIGHTS_ON_BUY_DAY_RATIO, RIGHTS_ON_BUY_DAY_PRICE = 0.1, 5.0   # 配股成本 0.5


def _fixture(repository: WarehouseRepository) -> None:
    db = repository._connect()
    try:
        db.execute("BEGIN")
        db.executemany(
            "INSERT INTO daily_k VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(SYMBOL, DAY_MS * step, BUY_OPEN if step == 11 else CLOSE,
              CLOSE * 1.01, CLOSE * 0.99, CLOSE, 1e6, 1e7, "none", "CNY")
             for step in range(1, BARS + 1)])
        db.executemany(
            "INSERT INTO adjustment_factors VALUES (?,?,?,?,?,?,?,?,?)",
            [("key-1", SYMBOL, SYMBOL, BUY_MS, DIVIDEND_ON_BUY_DAY, BONUS_ON_BUY_DAY,
              RIGHTS_ON_BUY_DAY_RATIO, RIGHTS_ON_BUY_DAY_PRICE, "CNY"),
             ("key-2", SYMBOL, SYMBOL, DAY_MS * 12, 0.0, BONUS_AFTER_BUY_DAY,
              None, None, "CNY")])
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def repository(tmp_path_factory):
    repository = WarehouseRepository(tmp_path_factory.mktemp("tradability") / "market.duckdb")
    _fixture(repository)
    repository.build_event_panel(force=True)
    return repository


def _raw_panel_row(repository: WarehouseRepository, columns: str) -> tuple:
    """直接读面板表：`fwd_bonus` / `fwd2_bonus` 这类**中间量**不在导出列里，
    但它们正是"截断点对不对"的直接证据。"""
    import duckdb

    db = duckdb.connect(str(repository.event_panel_path), read_only=True)
    try:
        return db.execute(
            f"SELECT {columns} FROM event_panel WHERE thscode = ? AND date_ms = ?",
            [SYMBOL, T_MS]).fetchone()
    finally:
        db.close()


def _panel_row(repository: WarehouseRepository) -> dict:
    for date_ms, rows in repository.event_panel_window(dates=[T_MS], panel_ready_checked=True):
        assert date_ms == T_MS
        return rows[SYMBOL]
    raise AssertionError("面板没有产出该评估日")


# --- 除权截断点：公式必须钉死，不能靠汇总数看不出差别 ---------------------

def test_panel_next_open_excludes_the_buy_day_action_and_keeps_later_ones(repository):
    """可成交口径：`(close_{T+20} × (1+送股) + 分红 − 配股成本) / open_{T+1} − 1`，
    其中送股/分红/配股**只取严格晚于买入日**的那批。"""
    row = _panel_row(repository)

    assert row["next_open"] == pytest.approx(BUY_OPEN)
    assert row["next_open_ms"] == BUY_MS
    # T 收盘口径：窗口 (T, T+20] → 包含 T+1 与 T+2 的**全部**公司行动
    # （送股 0.5+0.2、配股 0.1、分红 0.3、配股成本 0.5）
    assert row["forward_return"] == pytest.approx(
        (CLOSE * (1 + 0.7 + 0.1) + DIVIDEND_ON_BUY_DAY
         - RIGHTS_ON_BUY_DAY_RATIO * RIGHTS_ON_BUY_DAY_PRICE) / CLOSE - 1)
    # 可成交口径：窗口 (T+1, T+20] → **只有** T+2 的 0.2 送股；T+1 当天的 0.5 送股、
    # 0.3 分红、配股都不该出现
    assert row["forward_return_next_open"] == pytest.approx(
        (CLOSE * (1 + BONUS_AFTER_BUY_DAY)) / BUY_OPEN - 1)
    # 中间量（不在导出列里）——它们是"截断点对不对"的直接证据：
    # 窗口 (T,T+20] 的送股 = 0.5+0.2；窗口 (T+1,T+20] 只剩 0.2
    bonus_all, bonus_after, div_after, cost_after, rights_after = _raw_panel_row(
        repository, "fwd_bonus, fwd2_bonus, fwd2_div, fwd2_cost, fwd2_rights")
    assert bonus_all == pytest.approx(BONUS_ON_BUY_DAY + BONUS_AFTER_BUY_DAY)
    assert bonus_after == pytest.approx(BONUS_AFTER_BUY_DAY)
    assert div_after == 0.0 and cost_after == 0.0 and rights_after == 0.0


def test_live_path_applies_the_same_cutoff(repository):
    """live 通路必须与面板逐位一致——否则等价比对失去意义。"""
    row = next(item for item in repository.settlement_returns_next_open(
        date_ms=T_MS, horizon_days=20) if item["thscode"] == SYMBOL)

    assert row["next_open"] == pytest.approx(BUY_OPEN)
    assert row["next_open_ms"] == BUY_MS
    assert row["bonus"] == pytest.approx(BONUS_AFTER_BUY_DAY), "T+1 当天的送股不得计入"
    assert row["dividend"] == 0.0 and row["rights_cost"] == 0.0

    panel = _panel_row(repository)
    from backend.app.screener.service import total_return_between
    live_value = total_return_between(row["next_open"], row["horizon_close"], row["bonus"],
                                      row["dividend"], row["rights"], row["rights_cost"])
    assert live_value == pytest.approx(panel["forward_return_next_open"])


def test_intraday_basis_never_credits_an_action(repository):
    """T+1 开盘 → T+1 收盘：窗口 (T+1, T+1] 是空的，任何公司行动都不该进来。"""
    row = _panel_row(repository)
    assert row["forward_return_next_intraday"] == pytest.approx(CLOSE / BUY_OPEN - 1)


def test_next_open_gap_is_the_raw_overnight_jump(repository):
    """`next_open_gap` 是**裸价格比**（用于量化「当日买不到」），不做任何除权折算。"""
    row = _panel_row(repository)
    assert row["next_open_gap"] == pytest.approx(BUY_OPEN / CLOSE - 1)


def test_panel_version_was_bumped_for_the_new_columns():
    """面板 SQL 改了就必须升版本，否则旧缓存会被当成新口径用。"""
    assert PANEL_SCHEMA_VERSION == "event_panel_v5"


# --- 口径登记表与拒绝非法口径 ---------------------------------------------

def test_every_basis_is_fully_declared(repository):
    required = {"name", "panel_column", "settlement_rows", "builder", "buy_point",
                "corporate_action_cutoff", "tradable", "note"}
    for basis, spec in OUTCOME_BASES.items():
        assert required <= set(spec), f"{basis} 缺少 {required - set(spec)}"
        assert basis in OUTCOME_BASIS_DESIGNS, f"{basis} 没有对应的预注册 design reference"

    # 面板必须**真的**把这些列导出来，否则新口径会在面板通路上静默拿不到值
    exported = set(_panel_row(repository))
    for basis, spec in OUTCOME_BASES.items():
        assert spec["panel_column"] in exported, f"{basis} 的列 {spec['panel_column']} 未导出"

    assert OUTCOME_BASES["close_t"]["tradable"] is False
    assert OUTCOME_BASES["next_open"]["tradable"] is True
    assert OUTCOME_BASES["next_intraday"]["tradable"] is True


def test_live_and_panel_expose_the_same_field_names(repository):
    """面板列名与 live 行键名必须一致。

    实测踩过：面板列叫 `next_open_ms`，而 live 行的键当时叫 `next_ms` ——
    等价比对于是把"**键名不同**"报成"**值不同**"，49,095 个单元全红，
    而真正的公式其实处处正确。键名漂移会把等价性证据直接废掉，所以单独钉住。
    """
    live_keys = set(repository.settlement_returns_next_open(
        date_ms=T_MS, horizon_days=20)[0])
    panel_keys = set(_panel_row(repository))
    for key in ("next_open_ms", "next_open", "next_close"):
        assert key in live_keys, f"live 结算行缺少 {key}（键名漂移会让等价比对失效）"
        assert key in panel_keys, f"面板列缺少 {key}"


def test_rebuild_does_not_inflate_the_panel_file(repository):
    """重建必须**另写一份再原子替换**，不能往旧文件里追加数据块。

    实测踩过：`CREATE OR REPLACE TABLE` 不回收旧块，每重建一次文件就近乎翻倍
    （v4 面板 395 MB → 升 v5 并重建两次后 **1099 MB**）。这条测试钉住"大小恒等于一次构建"，
    并确认临时文件不会残留。
    """
    building = repository.event_panel_path.with_name(
        repository.event_panel_path.name + ".building")
    repository.build_event_panel(force=True)
    first = repository.event_panel_path.stat().st_size
    repository.build_event_panel(force=True)
    second = repository.event_panel_path.stat().st_size

    assert second <= first * 1.05, f"重建后文件膨胀：{first} → {second}"
    assert not building.exists(), "临时构建文件必须被替换掉，不能残留"
    assert repository.event_panel_status()["ready"] is True


def test_unknown_return_basis_is_refused_instead_of_falling_back(repository):
    """拼错口径必须报错——悄悄回落到 `close_t` 就等于拿"买不到"的价格当成交价汇报。"""
    with pytest.raises(ValueError, match="return_basis"):
        LimitUpEventStudy(repository).run_fields(fields=("seal_ratio",),
                                                 return_basis="next-opne")
