"""QuantService · ① 模拟（T0 研究回测；账户执行见 PaperService）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional


class QuantFollowMixin:
    """① 模拟：量化侧桥接（纸面账户 CRUD 在 services.paper_service）。"""

    def run_t0_backtest(
        self,
        code: str = "",
        *,
        lookback: int = 30,
        initial_shares: float = 1000,
        rules: Optional[dict] = None,
        from_paper: bool = False,
        codes: Optional[list] = None,
        compare_optimistic: bool = True,
        use_minute: bool = True,
        compare_daily: bool = True,
    ) -> Dict[str, Any]:
        from quant.research.t0_backtest import (
            run_t0_backtest_for_code,
            run_t0_backtest_for_holdings,
        )

        if from_paper or codes is not None or not str(code or "").strip():
            holdings = self._t0_holdings_for_backtest(codes=codes, code=code)
            return run_t0_backtest_for_holdings(
                holdings,
                lookback=lookback,
                rules=rules,
                compare_optimistic=compare_optimistic,
                use_minute=use_minute,
                compare_daily=compare_daily,
            )

        return run_t0_backtest_for_code(
            code,
            lookback=lookback,
            initial_shares=initial_shares,
            rules=rules,
            compare_optimistic=compare_optimistic,
            use_minute=use_minute,
            compare_daily=compare_daily,
        )

    def _t0_holdings_for_backtest(
        self, *, codes: Optional[list] = None, code: str = ""
    ) -> list:
        import os

        from core.paper import load_paper
        from core.paths import PAPER_PATH

        def _code_key(c: object) -> str:
            s = str(c or "").strip().upper()
            if "." in s:
                s = s.split(".", 1)[0]
            return s

        if not os.path.isfile(PAPER_PATH):
            return []
        paper = load_paper(PAPER_PATH)
        holdings = list(paper.get("holdings") or [])
        want: set = set()
        if codes:
            want = {str(c).strip() for c in codes if str(c).strip()}
        elif str(code or "").strip():
            want = {str(code).strip()}
        if want:
            want_keys = {_code_key(w) for w in want}
            holdings = [
                h
                for h in holdings
                if _code_key(h.get("stock_code")) in want_keys
                or str(h.get("stock_name") or "").strip() in want
                or any(
                    w and w in str(h.get("stock_name") or "")
                    for w in want
                )
            ]
        return holdings

