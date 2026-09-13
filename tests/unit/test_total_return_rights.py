"""公司行动总收益口径的守卫（2026-09-14 修正配股漏项）。

## 修的是什么

`screener.service.total_return` 与 `_factor_value` 的动量项此前写成 `(1 + 送股)`，
**漏掉配股比例**，而：

- 它们自己的 docstring 写的是"含送股与配股"；
- 仓库里 P0-1 验证过的规范式子（`repository.corporate_action_total_return_history`）
  是 `(close × (1 + 送股 + 配股) + 分红 − 配股成本) / prev_close`。

经济学上：持有 1 股，按比例 `r`、价格 `p` 参与配股后持有 `1+r` 股，成本 `r×p`，
所以总收益是 `((1+r)×close_h + 分红 − r×p) / close_t − 1`。
只减成本、不加股份，会把**有配股的标的**系统性算低。

实测影响面：真实库 49,095 个抽样单元里 **6 处**（0.012%）——量小，但方向明确，
且会让"总收益口径"这件事在仓库里出现两种算法。
"""

from __future__ import annotations

import pytest

from backend.app.screener.service import _factor_value, total_return


def _settlement_row(**overrides) -> dict:
    row = {"close_t": 10.0, "horizon_close": 11.0, "bonus": 0.0, "dividend": 0.0,
           "rights": 0.0, "rights_cost": 0.0}
    row.update(overrides)
    return row


def test_rights_ratio_enters_the_multiplier():
    """**这条就是本次修正的核心**：配股必须增加持股数，而不只是扣成本。

    用真实库里踩到的那一例：000977.SZ，close_t=17.63、窗口末收盘 16.25、
    10 配 3（rights=0.3，即每股配 0.3 股）、配股价 10.34 → 成本 3.102。
    正确：(16.25×1.3 − 3.102)/17.63 − 1 = +2.229%；
    错误（旧实现）：(16.25 − 3.102)/17.63 − 1 = −25.42%。
    """
    row = _settlement_row(close_t=17.63, horizon_close=16.25, rights=0.3, rights_cost=3.102)
    assert total_return(row) == pytest.approx(0.02229154849688042, abs=1e-12)
    wrong = (16.25 - 3.102) / 17.63 - 1
    assert wrong == pytest.approx(-0.2542257515598412, abs=1e-12)
    assert total_return(row) > 0 > wrong, "两者符号相反，说明这个差别不是舍入级别的"


def test_cash_dividend_and_bonus_shares_still_behave():
    """没配股时口径不变（防止修配股时改坏送股与分红）。"""
    assert total_return(_settlement_row(dividend=1.0)) == pytest.approx(0.2)
    assert total_return(_settlement_row(bonus=1.0)) == pytest.approx(1.2)
    assert total_return(_settlement_row()) == pytest.approx(0.1)
    # 只有成本、没有股份（理论上不该出现）时仍需可算
    assert total_return(_settlement_row(rights_cost=2.0)) == pytest.approx(-0.1)


def test_momentum_uses_the_same_convention_as_the_settlement_return():
    """动量因子与结算收益必须用同一个公司行动口径，否则两个评价互相打架。"""
    row = {"close": 11.0, "close_60_ago": 10.0, "bonus_60": 0.0, "div_60": 0.0,
           "rights_60": 0.5, "rights_cost_60": 3.0}
    expected = (11.0 * 1.5 - 3.0) / 10.0 - 1
    assert _factor_value(row, "momentum_60d") == pytest.approx(expected)
    assert expected == pytest.approx(0.35)
    # 与 total_return 在同样输入下给出的口径一致（两者是同一件事的两个用途）
    settlement = _settlement_row(close_t=10.0, horizon_close=11.0, rights=0.5,
                                 rights_cost=3.0)
    assert total_return(settlement) == pytest.approx(expected)
