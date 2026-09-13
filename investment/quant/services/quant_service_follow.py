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
        initial_cash: Optional[float] = None,
        rules: Optional[dict] = None,
        from_paper: bool = False,
        codes: Optional[list] = None,
        use_minute: bool = True,
    ) -> Dict[str, Any]:
        """研究做T回测：强制 5m 第一触达（已删除日线模拟）。"""
        from quant.research.t0_backtest import (
            T0_BT_VIRTUAL_CASH,
            T0_BT_VIRTUAL_SHARES,
            run_t0_backtest_for_code,
            run_t0_backtest_for_holdings,
        )

        _ = use_minute
        try:
            v_shares = float(initial_shares if initial_shares is not None else T0_BT_VIRTUAL_SHARES)
        except (TypeError, ValueError):
            v_shares = T0_BT_VIRTUAL_SHARES
        v_shares = max(100.0, min(v_shares, 100_000.0))
        try:
            v_cash = float(initial_cash if initial_cash is not None else T0_BT_VIRTUAL_CASH)
        except (TypeError, ValueError):
            v_cash = T0_BT_VIRTUAL_CASH
        v_cash = max(10_000.0, min(v_cash, 1.0e8))
        if from_paper or codes is not None or not str(code or "").strip():
            holdings, paper = self._t0_holdings_for_backtest(codes=codes, code=code)
            out = run_t0_backtest_for_holdings(
                holdings,
                lookback=lookback,
                rules=rules,
                paper=paper if from_paper else None,
                use_minute=True,
                virtual_shares=v_shares,
                virtual_cash=v_cash,
            )
        else:
            out = run_t0_backtest_for_code(
                code,
                lookback=lookback,
                initial_shares=v_shares,
                initial_cash=v_cash,
                rules=rules,
                use_minute=True,
            )
        if isinstance(out, dict) and out.get("success"):
            out.setdefault(
                "request",
                {
                    "lookback": lookback,
                    "code": str(code or "").strip(),
                    "from_paper": bool(from_paper),
                    "codes": list(codes) if codes else None,
                    "initial_shares": v_shares,
                    "initial_cash": v_cash,
                },
            )
            try:
                from core.backtest_result_store import save_last_t0_backtest

                save_last_t0_backtest(out)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in quant_service_follow.py", exc_info=True)
                logger.warning("上次做 T 回测结果落盘失败", exc_info=True)
        return out

    def load_last_t0_backtest(self) -> Dict[str, Any]:
        from core.backtest_result_store import load_last_t0_backtest as load_snap

        return load_snap()

    def _t0_holdings_for_backtest(
        self, *, codes: Optional[list] = None, code: str = ""
    ) -> tuple:
        import os

        from core.paper import load_paper
        from core.paths import PAPER_PATH

        def _code_key(c: object) -> str:
            s = str(c or "").strip().upper()
            if "." in s:
                s = s.split(".", 1)[0]
            return s

        if not os.path.isfile(PAPER_PATH):
            return [], None
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
        return holdings, paper

