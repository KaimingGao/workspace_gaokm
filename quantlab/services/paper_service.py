"""纸面账户服务（Web / CLI 共用边界）。

拆分为 account / jobs / trades mixin；本文件只组装 PaperService。
"""


import logging

logger = logging.getLogger(__name__)
import threading
from typing import Optional

from core.paths import PAPER_PATH
from services.paper_account import PaperAccountMixin
from services.paper_jobs import PaperJobsMixin
from services.paper_trades import PaperTradesMixin


class PaperService(PaperAccountMixin, PaperJobsMixin, PaperTradesMixin):
    """模拟账本应用服务：账户 · 调仓 job · 手动买卖。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or PAPER_PATH
        self._run_lock = threading.Lock()
