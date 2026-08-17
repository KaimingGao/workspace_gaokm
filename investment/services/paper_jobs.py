"""PaperService · 调仓 job。"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from core.job_progress import paper_job
from core.paper import (
    load_paper,
    save_paper,
    append_operation_log,
    paper_write_lock,
)
from core.paper_rebalance_orchestrator import run_paper_rebalance


class PaperJobsMixin:
    def get_job(self) -> Dict[str, Any]:
        return {"ok": True, "job": paper_job.get()}

    def run(
        self,
        *,
        simulate_buy: bool = False,
        strategy: str = "short",
        dry_run: bool = False,
        on_progress=None,
    ) -> Dict[str, Any]:
        import copy

        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        cluster_prep = None
        try:
            from core.signal.cluster_live import prepare_cluster_for_daily

            cluster_prep = prepare_cluster_for_daily()
        except Exception as exc:
            cluster_prep = {
                "success": False,
                "error": str(exc),
                "task": "cluster_prepare_daily",
            }
        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            if dry_run:
                paper = copy.deepcopy(paper)
            result = run_paper_rebalance(
                paper,
                mode="holding_rules",
                simulate_buy=simulate_buy,
                strategy=strategy,
                on_progress=on_progress,
                dry_run=dry_run,
            )
            if dry_run:
                return {
                    "ok": True,
                    "preview": True,
                    "cluster_prepare": cluster_prep,
                    **result,
                }
            from core.paper_costs import fee_fields_from_trade, pnl_fields_from_trade

            buys = result.get("buy_trades") or result.get("new_trades") or []
            sells = result.get("sell_trades") or []
            for t in buys:
                fee_meta = fee_fields_from_trade(t)
                append_operation_log(
                    paper,
                    "buy",
                    detail=(
                        f"[调仓] 买入 {t.get('stock_name') or t.get('stock_code')} "
                        f"{t.get('shares')}股 @ {t.get('price')}"
                    ),
                    meta={
                        "stock_code": t.get("stock_code"),
                        "stock_name": t.get("stock_name"),
                        "shares": t.get("shares"),
                        "price": t.get("price"),
                        "amount": t.get("amount") or t.get("actual_cost"),
                        "origin": t.get("origin") or "strategy",
                        "score": t.get("score"),
                        "note": t.get("note"),
                        **fee_meta,
                    },
                )
            for t in sells:
                fee_meta = fee_fields_from_trade(t)
                pnl_meta = pnl_fields_from_trade(t)
                append_operation_log(
                    paper,
                    "sell",
                    detail=(
                        f"[调仓] 卖出 {t.get('stock_name') or t.get('stock_code')} "
                        f"{t.get('shares')}股 @ {t.get('price')}"
                    ),
                    meta={
                        "stock_code": t.get("stock_code"),
                        "stock_name": t.get("stock_name"),
                        "shares": t.get("shares"),
                        "price": t.get("price"),
                        "amount": t.get("amount") or t.get("actual_cost"),
                        "origin": t.get("origin") or "strategy",
                        "note": t.get("note"),
                        **fee_meta,
                        **pnl_meta,
                    },
                )
            if buys or sells:
                append_operation_log(
                    paper, "rebalance",
                    detail=f"策略{strategy} · 买入{len(buys)}笔 · 卖出{len(sells)}笔",
                    meta={"strategy": strategy, "buy_count": len(buys), "sell_count": len(sells)},
                )
            elif result.get("buys_blocked"):
                # risk_block 已在 run_daily_cycle 写入；再记一条调仓摘要便于列表扫描
                blocks = result.get("risk_blocks") or []
                append_operation_log(
                    paper,
                    "rebalance",
                    detail=f"策略{strategy} · 风控拦截加仓 · {len(blocks)} 条",
                    meta={
                        "strategy": strategy,
                        "buys_blocked": True,
                        "blocks": blocks,
                    },
                )
            save_paper(paper, self.path)
            return {"ok": True, "cluster_prepare": cluster_prep, **result}

    def start_run_job(
        self,
        *,
        simulate_buy: bool = False,
        strategy: str = "short",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """后台跑观察池，前端轮询 /api/jobs/paper（兼容 /api/paper/job）。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        if paper_job.is_running():
            return {"ok": False, "error": "已有纸面任务在运行", "job": paper_job.get()}

        paper = load_paper(self.path)
        # 使用持仓数量作为评分进度的总长度
        holdings = paper.get("holdings") or []
        holding_codes = [str(h.get("stock_code")) for h in holdings if h.get("stock_code")]
        total = max(len(holding_codes), 1) + 3
        if dry_run:
            kind = "paper_buy_preview" if simulate_buy else "paper_run_preview"
        else:
            kind = "paper_buy" if simulate_buy else "paper_run"
        job_id = paper_job.start(
            kind=kind,
            total=total,
            message="排队中",
        )

        def _worker() -> None:
            if not self._run_lock.acquire(blocking=False):
                paper_job.finish(error="纸面任务锁被占用")
                return
            try:
                def on_progress(cur: int, tot: int, msg: str) -> None:
                    paper_job.update(current=cur, total=tot, message=msg)

                result = self.run(
                    simulate_buy=simulate_buy,
                    strategy=strategy,
                    dry_run=dry_run,
                    on_progress=on_progress,
                )
                paper_job.finish(result=result)
            except Exception as e:
                paper_job.finish(error=str(e))
            finally:
                self._run_lock.release()

        threading.Thread(target=_worker, name=f"paper-job-{job_id}", daemon=True).start()
        return {"ok": True, "background": True, "job": paper_job.get()}

