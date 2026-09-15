"""QuantService · ① 模拟（T0 研究回测；账户执行见 PaperService）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Callable, Dict, Optional


def _attach_t0_scope_label(out: Dict[str, Any], *, code: str = "") -> None:
    if not isinstance(out, dict) or not out.get("success"):
        return
    if out.get("from_holdings") and int(out.get("ok_count") or 0) > 1:
        out["scope_label"] = f"模拟持仓 {out.get('ok_count')} 只"
    elif out.get("from_holdings"):
        out["scope_label"] = (
            f"模拟持仓 · {out.get('stock_name') or out.get('stock_code') or '单票'}"
        )
    else:
        out["scope_label"] = f"研究标的 · {out.get('stock_name') or code or '—'}"


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
        progress_cb: Optional[Callable[..., None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
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
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
            )
        else:
            if progress_cb:
                try:
                    progress_cb(0, 1, f"回测 {code or '标的'}…")
                except Exception:  # noqa: BLE001
                    logger.debug("t0 progress_cb failed", exc_info=True)
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
            _attach_t0_scope_label(out, code=str(code or "").strip())
            try:
                from core.backtest_result_store import save_last_t0_backtest

                save_last_t0_backtest(out)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in quant_service_follow.py", exc_info=True)
                logger.warning("上次做 T 回测结果落盘失败", exc_info=True)
        return out

    def start_t0_backtest_job(
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
        """后台做 T 回测；轮询 ``GET /api/jobs/t0-backtest``。"""
        import threading

        from core.job_progress import t0_backtest_job

        t0_backtest_job.reclaim_if_stale()
        if t0_backtest_job.is_running():
            return {
                "ok": True,
                "success": True,
                "background": True,
                "reused": True,
                "job": t0_backtest_job.get(),
            }

        if from_paper or codes is not None or not str(code or "").strip():
            holdings, _paper = self._t0_holdings_for_backtest(codes=codes, code=code)
            n_hold = max(1, len(holdings))
        else:
            n_hold = 1
        job_id = t0_backtest_job.start(
            kind="t0_backtest",
            total=n_hold,
            message="回测入队…",
        )

        def _progress(cur: int = 0, tot: int = 0, msg: str = "") -> None:
            t0_backtest_job.update(
                current=max(0, int(cur or 0)),
                total=max(1, int(tot or n_hold)),
                message=str(msg or "回测中…"),
                job_id=job_id,
            )

        def _worker() -> None:
            stop_hb = threading.Event()

            def _heartbeat() -> None:
                while not stop_hb.wait(8.0):
                    if not t0_backtest_job.touch(job_id=job_id):
                        return

            hb = threading.Thread(
                target=_heartbeat, name=f"t0-backtest-hb-{job_id}", daemon=True
            )
            hb.start()
            try:
                if t0_backtest_job.is_cancel_requested():
                    t0_backtest_job.finish(error="已取消", job_id=job_id)
                    return
                result = self.run_t0_backtest(
                    code,
                    lookback=lookback,
                    initial_shares=initial_shares,
                    initial_cash=initial_cash,
                    rules=rules,
                    from_paper=from_paper,
                    codes=codes,
                    use_minute=use_minute,
                    progress_cb=_progress,
                    cancel_cb=t0_backtest_job.is_cancel_requested,
                )
                if t0_backtest_job.is_cancel_requested():
                    t0_backtest_job.finish(error="已取消", job_id=job_id)
                    return
                if not isinstance(result, dict):
                    t0_backtest_job.finish(error="回测无返回", job_id=job_id)
                    return
                if not result.get("success"):
                    t0_backtest_job.finish(
                        error=str(result.get("error") or "回测失败"),
                        result=result,
                        job_id=job_id,
                    )
                    return
                t0_backtest_job.update(
                    current=n_hold,
                    total=n_hold,
                    message="完成",
                    job_id=job_id,
                )
                t0_backtest_job.finish(result=result, job_id=job_id)
            except Exception as e:
                logger.exception("unexpected error in t0 backtest worker")
                t0_backtest_job.finish(error=str(e), job_id=job_id)
            finally:
                stop_hb.set()

        threading.Thread(target=_worker, name=f"t0-backtest-{job_id}", daemon=True).start()
        return {
            "ok": True,
            "success": True,
            "background": True,
            "job": t0_backtest_job.get(),
        }

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
