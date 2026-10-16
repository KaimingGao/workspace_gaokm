"""领域端口：core 只依赖此处协议，出站实现由 ``adapters.bind`` 注入。

约定：
- 内部 canonical 账本模块仍叫 paper（文件 paper.json、API /api/paper）
- 产品对外叫「模拟」/follow；本包不引入第四套词
"""

from core.ports.market import fetch_daily_bars, query_quote, quote_price
from core.ports.paper_cycle import (
    PaperCyclePort,
    clear_paper_cycle_factory,
    get_paper_cycle,
    is_paper_cycle_bound,
    set_paper_cycle_factory,
)
from core.ports.signal import build_signal_pool

__all__ = [
    "PaperCyclePort",
    "build_signal_pool",
    "clear_paper_cycle_factory",
    "fetch_daily_bars",
    "get_paper_cycle",
    "is_paper_cycle_bound",
    "query_quote",
    "quote_price",
    "set_paper_cycle_factory",
]
