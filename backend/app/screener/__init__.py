"""C2 最小版：可复现的横截面候选池与因子评价（方案 02 §A，决策 D16/D19）。

**它不产出荐股结论。** 见 `service.py` 的模块 docstring。
"""

from .factors import FACTOR_BY_ID, FACTOR_IDS, FACTOR_SET_VERSION, factor_declarations
from .service import LIMITATIONS, PLACEBO_DRAWS, ScreenerService
from .universe import UNSUPPORTED_EXCLUSIONS, UniverseRules

__all__ = ("FACTOR_BY_ID", "FACTOR_IDS", "FACTOR_SET_VERSION", "LIMITATIONS", "PLACEBO_DRAWS",
           "ScreenerService", "UNSUPPORTED_EXCLUSIONS", "UniverseRules", "factor_declarations")
