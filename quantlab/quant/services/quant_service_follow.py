"""QuantService · ① 模拟（T0 研究回测；账户执行见 PaperService）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Callable, Dict, Optional


def _attach_t0_scope_label(out: Dict[str, Any], *, code: str = "") -> None:
    if not isinstance(out, dict) or not out.get("success"):
        return
    # 手动候选池的文案已在引擎里写好（含虚拟股数 / 本金）
    if out.get("manual_pool"):
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
        score_model_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """研究做T回测：强制 5m 第一触达（已删除日线模拟）。"""
        from quant.research.t0_backtest import (
            T0_BT_VIRTUAL_CASH,
            T0_BT_VIRTUAL_SHARES,
            run_t0_backtest_for_code,
            run_t0_backtest_for_holdings,
        )
        from core.research.holdout import normalize_backtest_model_role

        role = normalize_backtest_model_role(score_model_role)
        manual_pool = codes is not None

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
        from core.research.materialize.consumer import materialized_consumer

        try:
            with materialized_consumer():
                if from_paper or codes is not None or not str(code or "").strip():
                    holdings, paper = self._t0_holdings_for_backtest(codes=codes, code=code)
                    out = run_t0_backtest_for_holdings(
                        holdings,
                        lookback=lookback,
                        rules=rules,
                        paper=paper if from_paper or manual_pool else None,
                        use_minute=True,
                        virtual_shares=v_shares,
                        virtual_cash=v_cash,
                        progress_cb=progress_cb,
                        cancel_cb=cancel_cb,
                        score_model_role=role,
                        manual_pool=manual_pool,
                    )
                else:
                    if progress_cb:
                        try:
                            progress_cb(0, 1, f"回测 {code or '标的'}…")
                        except Exception:  # noqa: BLE001
                            logger.debug("t0 progress_cb failed", exc_info=True)
                    _, paper = self._t0_holdings_for_backtest()
                    out = run_t0_backtest_for_code(
                        code,
                        lookback=lookback,
                        initial_shares=v_shares,
                        initial_cash=v_cash,
                        rules=rules,
                        paper=paper,
                        use_minute=True,
                        progress_cb=progress_cb,
                        score_model_role=role,
                    )
            if isinstance(out, dict) and out.get("success"):
                out.setdefault(
                    "request",
                    {
                        "lookback": lookback,
                        "code": str(code or "").strip(),
                        "from_paper": bool(from_paper),
                        "manual_pool": bool(manual_pool),
                        "codes": list(codes) if codes else None,
                        "initial_shares": v_shares,
                        "initial_cash": v_cash,
                        "score_model_role": role,
                    },
                )
                _attach_t0_scope_label(out, code=str(code or "").strip())
                try:
                    from core.backtest_result_store import save_last_t0_backtest

                    save_last_t0_backtest(out)
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.warning("上次做 T 回测结果落盘失败", exc_info=True)
            return out
        finally:
            try:
                from core.research.horizon_l1 import release_allocator_cache

                release_allocator_cache()
            except Exception:  # noqa: BLE001
                logger.debug("t0 release allocator cache failed", exc_info=True)

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
        score_model_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """后台做 T 回测；轮询 ``GET /api/jobs/t0-backtest``。"""
        import threading

        from core.job_progress import t0_backtest_job

        t0_backtest_job.reclaim_if_stale()
        if from_paper or codes is not None or not str(code or "").strip():
            holdings, _paper = self._t0_holdings_for_backtest(codes=codes, code=code)
            n_hold = max(1, len(holdings))
        else:
            n_hold = 1
        # 原子开槽。is_running 与 start 分开时，连点会各起一条线程。
        job_id, started_or_running = t0_backtest_job.try_start(
            kind="t0_backtest",
            total=n_hold,
            message="回测入队…",
        )
        if job_id is None:
            return {
                "ok": True,
                "success": True,
                "background": True,
                "reused": True,
                "job": started_or_running or t0_backtest_job.get(),
            }

        def _progress(cur: int = 0, tot: int = 0, msg: str = "") -> None:
            t0_backtest_job.update(
                current=max(0, int(cur or 0)),
                total=max(1, int(tot or n_hold)),
                message=str(msg or "回测中…"),
                job_id=job_id,
            )

        def _stop() -> bool:
            """本线程已不是槽主，或槽主被取消。槽被换掉后旧回测应停。"""
            return bool(t0_backtest_job.lost_or_cancelled(job_id))

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
                if _stop():
                    t0_backtest_job.finish(error="已取消", job_id=job_id)
                    return
                t0_backtest_job.update(
                    current=0,
                    total=n_hold,
                    message="准备回测…",
                    job_id=job_id,
                )
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
                    cancel_cb=_stop,
                    score_model_role=score_model_role,
                )
                if _stop():
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
        """成交名单。

        ``codes is None`` 且未指定单票：整份纸面持仓。
        传入 ``codes``（含空列表）：按顺序做成交名单，不在持仓里的票补成虚拟行。
        最多 ``T0_BT_MANUAL_POOL_MAX`` 只。纸面文件缺失时仍可只跑手动名单。
        """
        import os

        from core.paper import load_paper
        from core.paths import PAPER_PATH
        from quant.research.t0_backtest import T0_BT_MANUAL_POOL_MAX

        def _code_key(c: object) -> str:
            s = str(c or "").strip().upper()
            if "." in s:
                s = s.split(".", 1)[0]
            return s

        paper = None
        holdings: list = []
        if os.path.isfile(PAPER_PATH):
            paper = load_paper(PAPER_PATH)
            holdings = list((paper or {}).get("holdings") or [])

        want_raw = None
        if codes is not None:
            want_raw = list(codes)[: max(1, int(T0_BT_MANUAL_POOL_MAX))]
        elif str(code or "").strip():
            want_raw = [str(code).strip()]
        if want_raw is None:
            return holdings, paper

        by_key: Dict[str, dict] = {}
        by_name: Dict[str, dict] = {}
        for h in holdings:
            if not isinstance(h, dict):
                continue
            key = _code_key(h.get("stock_code"))
            if key and key not in by_key:
                by_key[key] = h
            name = str(h.get("stock_name") or "").strip()
            if name:
                by_name.setdefault(name, h)

        out: list = []
        seen: set = set()
        for raw in want_raw:
            explicit_name = ""
            token = raw
            if isinstance(raw, dict):
                token = (
                    raw.get("stock_code")
                    or raw.get("code")
                    or raw.get("stock_name")
                    or raw.get("name")
                    or ""
                )
                explicit_name = str(raw.get("stock_name") or raw.get("name") or "").strip()
            token = str(token or "").strip()
            if not token:
                continue
            key = _code_key(token)
            dedupe = key or token
            if dedupe in seen:
                continue
            seen.add(dedupe)
            hit = by_key.get(key) or by_name.get(token)
            if isinstance(hit, dict):
                row = dict(hit)
                if explicit_name and not str(row.get("stock_name") or "").strip():
                    row["stock_name"] = explicit_name
                out.append(row)
                continue
            out.append(
                {
                    "stock_code": key or token,
                    "stock_name": explicit_name,
                }
            )
        return out, paper
