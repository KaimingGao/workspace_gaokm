"""PaperService · 纸面任务槽。

持仓规则日循环（``/api/paper/run``）已下线。开盘调仓走 ``rebalance``。
"""


import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

from core.platform.job_progress import paper_job

_RETIRED = "持仓规则日循环已下线。开盘调仓在交易执行。"


class PaperJobsMixin:
    def get_job(self) -> Dict[str, Any]:
        return {"ok": True, "job": paper_job.get()}

    def run(
        self,
        *,
        simulate_buy: bool = False,
        strategy: str = "short_conservative",
        dry_run: bool = False,
        on_progress=None,
    ) -> Dict[str, Any]:
        _ = (simulate_buy, strategy, dry_run, on_progress)
        return {"ok": False, "retired": True, "error": _RETIRED}

    def start_run_job(
        self,
        *,
        simulate_buy: bool = False,
        strategy: str = "short_conservative",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        _ = (simulate_buy, strategy, dry_run)
        return {"ok": False, "retired": True, "error": _RETIRED, "job": paper_job.get()}
