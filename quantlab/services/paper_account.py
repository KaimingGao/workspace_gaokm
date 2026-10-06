"""PaperService · 账户状态 / 资金 / 策略晋升。"""


import logging

logger = logging.getLogger(__name__)
import os
from typing import Any, Dict, Optional

from core.paper import (
    _now_iso,
    append_operation_log,
    append_snapshot,
    init_from_example,
    load_paper,
    mark_to_market,
    paper_write_lock,
    save_paper,
    snapshots_for_ui,
)
from core.paper.costs import enrich_operation_log_with_trade_fees
from services.paper_helpers import _build_score_formula


class PaperAccountMixin:
    def _operation_log_for_ui(self, paper: dict, *, limit: int = 50) -> list:
        logs = (paper.get("operation_log") or [])[-max(1, int(limit or 50)) :]
        return enrich_operation_log_with_trade_fees(
            logs,
            paper.get("trades") or [],
            cost_model=paper.get("cost_model"),
        )

    def _compute_holding_scores(
        self, paper: dict, summary: dict, *, offline_only: bool = True
    ) -> dict:
        """计算当前持仓的评分，合并到 summary.holdings 中。

        经 SignalService ``score_one``（默认 ``offline_only``，带超时）。
        表列 ŷ 用 09:30–10:00 调仓因果前缀（``use_minute_tau=True``）。
        **不**经 observation_pool / min_score TopN。
        """
        holdings = paper.get("holdings") or []
        holding_codes = [str(h.get("stock_code")) for h in holdings if h.get("stock_code")]
        if not holding_codes:
            return summary

        try:
            import logging

            from core.signal.score_display import annotate_score_gate, selection_min_score
            from core.signal.service import get_default_signal_service

            log = logging.getLogger(__name__)
            svc = get_default_signal_service()
            try:
                from core.signal.config import get_scoring_horizon_days

                horizon = int(get_scoring_horizon_days())
            except Exception:  # noqa: BLE001
                log.debug("get_scoring_horizon_days failed", exc_info=True)
                horizon = 1
            gate = selection_min_score(paper)
            try:
                from core.paper.rebalance.path_matrix import get_path_matrix_cfg

                rank_cfg = get_path_matrix_cfg(paper=paper)
            except Exception:  # noqa: BLE001
                log.debug("holding rank_cfg skipped", exc_info=True)
                rank_cfg = None

            def _pack_item(item: dict, *, cluster_mode=None) -> Dict[str, Any]:
                return svc.pack_holding_row(
                    item, cluster_mode=cluster_mode, rank_cfg=rank_cfg
                )

            score_by_code: Dict[str, Any] = {}
            missing = list(holding_codes)

            # 串行 + 仅缓存：避免线程池在 Web 进程里叠请求卡死
            for code in missing:
                try:
                    result = svc.score_one(
                        code,
                        horizon_days=horizon,
                        skip_fundamentals=True,
                        skip_sentiment=True,
                        offline_only=bool(offline_only),
                        quote_timeout=3.0 if offline_only else 8.0,
                        use_minute_tau=True,
                    )
                    raw = (
                        result.as_dict()
                        if hasattr(result, "as_dict")
                        else (result or {})
                    )
                except Exception as e:
                    log.warning("holding score fail %s: %s", code, e)
                    continue
                if not isinstance(raw, dict) or raw.get("success") is False:
                    continue
                item = raw.get("signal_item")
                if not isinstance(item, dict):
                    continue
                try:
                    from core.signal.dual_score import align_trade_score_fields

                    align_trade_score_fields(
                        item, write_score=False, refresh_window=False
                    )
                except Exception:  # noqa: BLE001
                    log.debug("align holding score failed %s", code, exc_info=True)
                score_by_code[code] = _pack_item(
                    item, cluster_mode=raw.get("cluster_mode")
                )

            # 持仓经济字段勿被评分包覆盖；其余与数据中心同源透传
            _HOLDING_KEEP = frozenset(
                {
                    "stock_code",
                    "stock_name",
                    "shares",
                    "cost",
                    "avg_cost",
                    "price",
                    "open",
                    "prev_close",
                    "change_pct",
                    "market_value",
                    "market_value_approx",
                    "pnl",
                    "pnl_pct",
                    "bought_date",
                    "bought_at",
                    "hold_days",
                    "sellable_shares",
                    "locked_shares",
                    "lots",
                    "origin",
                    "origin_label",
                    "currency",
                    "unit",
                    "market",
                }
            )
            enriched_holdings = []
            try:
                from core.t0.intraday import load_holding_t0_status_by_code

                t0_by_code = load_holding_t0_status_by_code()
            except Exception:  # noqa: BLE001
                logger.debug("holding t0 intraday status skipped", exc_info=True)
                t0_by_code = {}
            for h in summary.get("holdings") or []:
                code = str(h.get("stock_code") or "")
                enriched = dict(h)
                score_info = score_by_code.get(code)
                if score_info:
                    for k, v in score_info.items():
                        if k in _HOLDING_KEEP:
                            continue
                        enriched[k] = v
                    if score_info.get("reasons") is not None:
                        enriched["score_reasons"] = score_info.get("reasons")
                    if not enriched.get("score_formula"):
                        enriched["score_formula"] = _build_score_formula(score_info)
                    gate_meta = annotate_score_gate(
                        score_info.get("score"),
                        paper=paper,
                        min_score=gate,
                        item=score_info if isinstance(score_info, dict) else enriched,
                    )
                    enriched["min_score"] = gate_meta["min_score"]
                    enriched["below_min_score"] = gate_meta["below_min_score"]
                    if gate_meta.get("gate_score") is not None:
                        enriched["eod_gate_score"] = gate_meta.get("gate_score")
                else:
                    enriched["score"] = None
                    enriched["min_score"] = gate
                    enriched["below_min_score"] = False
                try:
                    from core.signal.dual_score.co import hydrate_holding_co_fields

                    hydrate_holding_co_fields(enriched)
                except Exception as e:
                    log.debug("holding on hydrate skipped %s: %s", code, e)
                try:
                    from core.paper.rebalance.rank_lots import ranking_pct_of
                    from core.signal.yhat_windows import ranking_open_px, ranking_price_tau

                    o = ranking_open_px(enriched)
                    p = ranking_price_tau(enriched)
                    yf = ranking_pct_of(enriched, rank_cfg, open_px=o, price_tau=p)
                    if yf is not None:
                        enriched["ranking"] = yf
                    if o is not None:
                        enriched["day_open"] = o
                    if p is not None:
                        enriched["price_tau"] = p
                except Exception:  # noqa: BLE001
                    log.debug("holding remaining ranking skipped %s", code, exc_info=True)
                enriched.pop("in_book", None)
                t0_st = t0_by_code.get(code)
                if t0_st:
                    enriched["t0_intraday"] = t0_st
                enriched_holdings.append(enriched)

            summary["holdings"] = enriched_holdings
            summary["selection_min_score"] = gate
        except Exception as e:
            # 评分失败不阻断账户摘要；保留持仓行，score 留空
            import logging

            logging.getLogger(__name__).warning(
                "holding scores unavailable: %s", e, exc_info=True
            )
        return summary

    def holding_scores(self, *, offline_only: bool = True) -> Dict[str, Any]:
        """持仓 ŷ。默认仅本地日线/分钟缓存；``offline_only=False`` 可补远端。"""
        import threading

        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        lock = getattr(self, "_holding_scores_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._holding_scores_lock = lock
        if not lock.acquire(blocking=False):
            return {
                "ok": True,
                "busy": True,
                "by_code": {},
                "scored": 0,
                "note": "持仓算分进行中",
                "offline_only": bool(offline_only),
            }
        try:
            paper = load_paper(self.path)
            holdings = paper.get("holdings") or []
            skeleton = {
                "holdings": [
                    {
                        "stock_code": h.get("stock_code"),
                        "stock_name": h.get("stock_name"),
                    }
                    for h in holdings
                    if h.get("stock_code")
                ]
            }
            enriched = self._compute_holding_scores(
                paper, skeleton, offline_only=bool(offline_only)
            )
            by_code: Dict[str, Any] = {}
            for row in enriched.get("holdings") or []:
                code = str((row or {}).get("stock_code") or "").strip()
                if code:
                    by_code[code] = row
            return {
                "ok": True,
                "busy": False,
                "by_code": by_code,
                "scored": len(by_code),
                "selection_min_score": enriched.get("selection_min_score"),
                "offline_only": bool(offline_only),
            }
        finally:
            lock.release()

    def _strategy_label(self, paper: Optional[dict]) -> Optional[str]:
        sid = (paper or {}).get("strategy_id") if isinstance(paper, dict) else None
        if not sid:
            return None
        try:
            from core.strategy import get_strategy_spec

            return get_strategy_spec(str(sid)).get("label")
        except Exception:  # noqa: BLE001
            logger.debug("strategy_label resolve failed", exc_info=True)
            return None

    def _execution_view(self, paper: dict) -> Dict[str, Any]:
        """生效 ExecutionSpec（纸面 channel）供 Web / API。"""
        try:
            from core.execution import (
                execution_public_view,
                resolve_effective_execution,
            )

            bundle = resolve_effective_execution(
                strategy=paper.get("strategy_id"),
                paper=paper,
                channel="paper",
            )
            return execution_public_view(bundle)
        except Exception as e:
            logger.exception('unexpected error in _execution_view')
            return {"ok": False, "error": str(e)}

    def execution_status(self, channel: str = "paper") -> Dict[str, Any]:
        """GET /api/paper/execution。"""
        if not os.path.isfile(self.path):
            from core.execution import (
                execution_public_view,
                resolve_effective_execution,
            )

            bundle = resolve_effective_execution(channel=channel or "paper")
            out = execution_public_view(bundle)
            out["initialized"] = False
            out["path"] = self.path
            return out
        paper = load_paper(self.path)
        from core.execution import execution_public_view, resolve_effective_execution

        bundle = resolve_effective_execution(
            strategy=paper.get("strategy_id"),
            paper=paper,
            channel=channel or "paper",
        )
        out = execution_public_view(bundle)
        out["initialized"] = True
        out["path"] = self.path
        out["locked"] = bool(paper.get("t0_rules_locked"))
        out["paper_t0"] = ((paper.get("rules") or {}).get("t0") or {})
        return out

    def execution_diff(self) -> Dict[str, Any]:
        """纸面 Execution vs 策略 Spec 默认。"""
        from core.execution import execution_diff_against_strategy

        if not os.path.isfile(self.path):
            return execution_diff_against_strategy(paper=None)
        paper = load_paper(self.path)
        return execution_diff_against_strategy(paper)

    def save_execution(
        self,
        patch: dict,
        *,
        note: str = "",
    ) -> Dict[str, Any]:
        """写入账户级 t0 / coupling 覆盖。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化模拟账户")
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            resolve_effective_execution,
        )

        paper = load_paper(self.path)
        applied = apply_execution_patch_to_paper(paper, patch, note=note or "")
        if not applied.get("ok"):
            return {"ok": False, "success": False, "errors": applied.get("errors") or []}
        paper["updated_at"] = _now_iso()
        append_operation_log(
            paper,
            "settings",
            detail="更新 Execution/做T 覆盖",
            meta={
                "t0_keys": list((applied.get("normalized") or {}).get("t0") or {}),
                "coupling": (applied.get("normalized") or {}).get("coupling"),
                "note": note or "",
            },
        )
        save_paper(paper, self.path)
        bundle = resolve_effective_execution(
            strategy=paper.get("strategy_id"),
            paper=paper,
            channel="paper",
        )
        view = execution_public_view(bundle)
        return {
            "ok": True,
            "success": True,
            "message": "已保存账户级做T/耦合覆盖",
            "execution": view,
            "locked": bool(paper.get("t0_rules_locked")),
        }

    def reset_execution(self) -> Dict[str, Any]:
        """清除账户级 Execution 覆盖。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化模拟账户")
        from core.execution import (
            execution_public_view,
            reset_paper_execution_overlay,
            resolve_effective_execution,
        )

        paper = load_paper(self.path)
        reset_paper_execution_overlay(paper)
        paper["updated_at"] = _now_iso()
        append_operation_log(paper, "settings", detail="重置 Execution/做T 为策略默认")
        save_paper(paper, self.path)
        bundle = resolve_effective_execution(
            strategy=paper.get("strategy_id"),
            paper=paper,
            channel="paper",
        )
        return {
            "ok": True,
            "success": True,
            "message": "已恢复策略默认 Execution",
            "execution": execution_public_view(bundle),
            "locked": False,
        }

    def fill_pending(self, *, now=None) -> Dict[str, Any]:
        """开盘窗 / 盘中追价成交挂单。无挂单或非交易时段则跳过。"""
        if not os.path.isfile(self.path):
            return {"ok": True, "filled": False, "reason": "no_paper", "trades": []}
        from core.paper.open_fill import fill_pending_at_open

        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            po = paper.get("pending_orders")
            if not isinstance(po, dict) or not (po.get("legs") or []):
                return {"ok": True, "filled": False, "reason": "no_pending", "trades": []}
            fill = fill_pending_at_open(paper, now=now)
            if fill.get("filled") or fill.get("skips"):
                if fill.get("filled"):
                    trades = fill.get("trades") or []
                    n_buy = sum(1 for t in trades if t.get("side") == "buy")
                    n_sell = sum(1 for t in trades if t.get("side") == "sell")
                    n_skip = len(fill.get("skips") or [])
                    mode = str(fill.get("mode") or "open")
                    verb = {
                        "open": "开盘成交挂单",
                        "chase": "中点追价成交挂单",
                        "eod_force": "收盘追价成交挂单",
                    }.get(mode, "成交挂单")
                    bits = []
                    if n_buy or n_sell:
                        bits.append(f"买{n_buy} · 卖{n_sell}")
                    if n_skip:
                        bits.append(f"跳过{n_skip}")
                    append_operation_log(
                        paper,
                        "rebalance",
                        detail=verb + (f" · {' · '.join(bits)}" if bits else ""),
                        meta={
                            "fill_action": mode,
                            "buy_count": n_buy,
                            "sell_count": n_sell,
                            "skip_count": n_skip,
                        },
                    )
                save_paper(paper, self.path)
            return fill

    def status(self, *, lite: bool = False) -> Dict[str, Any]:
        """账户摘要。

        ``lite=True``：只读落盘（持仓码/流水），不盯市、不打分——供数据中心
        快速标「已持」，避免与行情锁互相拖死。
        """
        if not os.path.isfile(self.path):
            return {"ok": True, "initialized": False, "path": self.path, "lite": lite}
        paper = load_paper(self.path)
        if not lite:
            po = paper.get("pending_orders")
            if isinstance(po, dict) and (po.get("legs") or []):
                try:
                    fill = self.fill_pending()
                    if fill.get("filled"):
                        paper = load_paper(self.path)
                except Exception:  # noqa: BLE001
                    logger.debug("fill_pending in status skipped", exc_info=True)
        if lite:
            raw_holdings = []
            from core.paper.tplus1 import snapshot_tplus1

            try:
                from core.t0.intraday import load_holding_t0_status_by_code

                t0_by_code = load_holding_t0_status_by_code()
            except Exception:  # noqa: BLE001
                logger.debug("lite holding t0 intraday status skipped", exc_info=True)
                t0_by_code = {}
            for h in paper.get("holdings") or []:
                t1 = snapshot_tplus1(h)
                code = str(h.get("stock_code") or "")
                row = {
                    "stock_code": h.get("stock_code"),
                    "stock_name": h.get("stock_name"),
                    "shares": h.get("shares"),
                    "cost": h.get("cost"),
                    "bought_at": h.get("bought_at"),
                    "sellable_shares": t1["sellable_shares"],
                    "locked_shares": t1["locked_shares"],
                    "origin": h.get("origin"),
                }
                t0_st = t0_by_code.get(code)
                if t0_st:
                    row["t0_intraday"] = t0_st
                raw_holdings.append(row)
            return {
                "ok": True,
                "initialized": True,
                "lite": True,
                "path": self.path,
                "name": paper.get("name"),
                "strategy_id": paper.get("strategy_id") or "short_conservative",
                "strategy_label": self._strategy_label(paper),
                "strategy_version": paper.get("strategy_version"),
                "summary": {
                    "cash": paper.get("cash"),
                    "holdings": raw_holdings,
                },
                "operation_log": self._operation_log_for_ui(paper, limit=50),
            }
        summary = mark_to_market(paper)
        # 持仓 ŷ 不在本接口同步算（并发会卡死「加载中…」）；见 GET /api/paper/holding-scores
        north_star = paper.get("last_north_star")
        if not isinstance(north_star, dict):
            try:
                from core.north_star import build_north_star_report

                north_star = build_north_star_report(paper)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                north_star = None
        exposure = None
        try:
            from core.risk.exposure import build_exposure_matrix

            exposure = build_exposure_matrix(paper, summary)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            exposure = None
        ops = paper.get("last_ops_report") or None
        if isinstance(ops, dict) and exposure and not ops.get("exposure"):
            ops = dict(ops)
            ops["exposure"] = {
                "sectors": (exposure.get("sectors") or [])[:12],
                "styles": (exposure.get("styles") or [])[:8],
                "size_buckets": (exposure.get("size_buckets") or [])[:6],
                "over_limit_sectors": exposure.get("over_limit_sectors") or [],
                "over_limit_names": exposure.get("over_limit_names") or [],
                "limits": exposure.get("limits"),
                "note": exposure.get("note"),
            }
        return {
            "ok": True,
            "initialized": True,
            "lite": False,
            "path": self.path,
            "name": paper.get("name"),
            "version": paper.get("version"),
            "strategy_id": paper.get("strategy_id") or "short_conservative",
            "strategy_label": self._strategy_label(paper),
            "strategy_version": paper.get("strategy_version"),
            "cost_model": paper.get("cost_model") or "simple_cn",
            "cost_params": paper.get("cost_params") or {},
            "ops_report": ops,
            "exposure": exposure,
            "north_star": north_star,
            "rules": paper.get("rules") or {},
            "summary": summary,
            "snapshots": snapshots_for_ui(paper, summary),
            "operation_log": self._operation_log_for_ui(paper, limit=50),
            "recent_trades": (paper.get("trades") or [])[-10:],
            "pending_orders": paper.get("pending_orders"),
            "execution": self._execution_view(paper),
            "config_preview": {
                "version": paper.get("version"),
                "name": paper.get("name"),
                "initial_cash": paper.get("initial_cash"),
                "cash": paper.get("cash"),
                "holdings": paper.get("holdings") or [],
                "rules": paper.get("rules") or {},
                "cost_model": paper.get("cost_model") or "simple_cn",
                "strategy_id": paper.get("strategy_id"),
                "strategy_version": paper.get("strategy_version"),
            },
        }

    def init(self) -> Dict[str, Any]:
        if os.path.isfile(self.path):
            raise FileExistsError("模拟账户已存在")
        path = init_from_example(self.path)
        paper = load_paper(path)
        append_operation_log(paper, "init", detail="初始化模拟账户")
        save_paper(paper, self.path)
        return {"ok": True, "path": path, "name": paper.get("name")}

    def set_cost_model(self, model: str) -> Dict[str, Any]:
        """切换成交成本模型：zero | simple_cn。"""
        from core.paper.costs import COST_MODELS, resolve_cost_model

        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化模拟账户")
        raw = str(model or "").strip().lower()
        if raw not in COST_MODELS:
            raise ValueError(f"cost_model 须为 {', '.join(COST_MODELS)}")
        paper = load_paper(self.path)
        paper["cost_model"] = raw
        paper["cost_model_locked"] = True
        paper["updated_at"] = _now_iso()
        append_operation_log(
            paper,
            "settings",
            detail=f"成本模型切换为 {raw}",
            meta={"cost_model": raw},
        )
        save_paper(paper, self.path)
        out = self.status()
        out["message"] = (
            "已切换为零成本假设"
            if resolve_cost_model(paper) == "zero"
            else "已切换为 A 股简化成本（佣金+印花税）"
        )
        return out

    def deposit(self, amount: float) -> Dict[str, Any]:
        """假账注资：增加现金，同步抬高 initial_cash，避免盈亏被注资扭曲。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        amt = float(amount)
        if amt <= 0:
            raise ValueError("注资金额须大于 0")
        if amt > 100_000_000:
            raise ValueError("单次注资不超过 1 亿")

        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            paper["cash"] = round(float(paper.get("cash") or 0) + amt, 2)
            paper["initial_cash"] = round(float(paper.get("initial_cash") or 0) + amt, 2)
            paper["updated_at"] = _now_iso()
            summary = mark_to_market(paper)
            append_snapshot(paper, summary)
            append_operation_log(paper, "deposit", detail=f"注资 {amt:g} 元", meta={"amount": amt})
            save_paper(paper, self.path)
        out = self.status()
        out["deposited"] = amt
        out["message"] = f"已注资 {amt:g} · 现金 {out.get('summary', {}).get('cash')}"
        return out

    def withdraw(self, amount: float) -> Dict[str, Any]:
        """假账减资：减少现金，同步下调 initial_cash（不低于 0）。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        amt = float(amount)
        if amt <= 0:
            raise ValueError("减资金额须大于 0")
        if amt > 100_000_000:
            raise ValueError("单次减资不超过 1 亿")

        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            cash = float(paper.get("cash") or 0)
            if cash <= 0:
                raise ValueError("当前无可用现金可减")
            if amt > cash:
                raise ValueError(f"减资不能超过现金余额（可用 {round(cash, 2)}）")

            paper["cash"] = round(cash - amt, 2)
            initial = float(paper.get("initial_cash") or 0)
            paper["initial_cash"] = round(max(0.0, initial - amt), 2)
            paper["updated_at"] = _now_iso()
            summary = mark_to_market(paper)
            append_snapshot(paper, summary)
            append_operation_log(paper, "withdraw", detail=f"减资 {amt:g} 元", meta={"amount": amt})
            save_paper(paper, self.path)
        out = self.status()
        out["withdrawn"] = amt
        out["message"] = f"已减资 {amt:g} · 现金 {out.get('summary', {}).get('cash')}"
        return out

    def reset(self) -> Dict[str, Any]:
        """回零（测试基线）：保留持仓与现金，把当前净值当作新起点，清空曲线/成交记录。

        同时写入当日 ``pnl_anchor``（回零价），使「今日收益」当日不再相对昨收，
        与累计收益同起点；下一自然日自动失效，恢复按昨收。
        """
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")

        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            summary = mark_to_market(paper)
            equity = float(summary.get("equity") or paper.get("cash") or 0)
            if equity <= 0:
                equity = float(paper.get("cash") or 0)
            # 以当前净值作为新的初始本金，盈亏从 0% 起算
            paper["initial_cash"] = round(equity, 2)
            paper["trades"] = []
            paper["signal_log"] = []
            ts = _now_iso()
            prices: Dict[str, float] = {}
            for h in summary.get("holdings") or []:
                code = str((h or {}).get("stock_code") or "").strip()
                px = (h or {}).get("price")
                if not code or px is None:
                    continue
                try:
                    prices[code] = float(px)
                except (TypeError, ValueError):
                    continue
            paper["pnl_anchor"] = {
                "ts": ts,
                "date": ts[:10],
                "equity": round(equity, 2),
                "cash": summary.get("cash"),
                "prices": prices,
            }
            paper["snapshots"] = [
                {
                    "ts": ts,
                    "equity": round(equity, 2),
                    "cash": summary.get("cash"),
                    "stock_value": summary.get("stock_value"),
                    "total_pnl_pct": 0.0,
                    "position_count": summary.get("position_count"),
                }
            ]
            paper["updated_at"] = ts
            append_operation_log(
                paper,
                "reset",
                detail=f"回零 · 基线 {round(equity, 2)} · 今日改相对回零价",
                meta={"equity": round(equity, 2), "anchor_n": len(prices)},
            )
            save_paper(paper, self.path)
        out = self.status()
        out["message"] = (
            f"已回零 · 基线 {out.get('summary', {}).get('equity')} · "
            f"持仓 {out.get('summary', {}).get('position_count', 0)} 只保留 · "
            f"今日收益自回零价起算"
        )
        return out

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def clear_records(self, category: str) -> Dict[str, Any]:
        """清除交易记录或资金记录。category: 'trading' | 'fund'。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        paper = load_paper(self.path)
        logs = paper.get("operation_log") or []
        trading_types = {
            "buy",
            "sell",
            "rebalance",
            "cluster_pool_rebalance",
            "t0_batch",
            "sync_paper",
        }
        fund_types = {"init", "deposit", "withdraw", "reset"}

        if category == "trading":
            before = len(logs)
            paper["operation_log"] = [l for l in logs if l.get("type") not in trading_types]
            after = len(paper["operation_log"])
            # Also clear trades array
            paper["trades"] = []
            cleared = before - after
        elif category == "fund":
            before = len(logs)
            paper["operation_log"] = [l for l in logs if l.get("type") not in fund_types]
            after = len(paper["operation_log"])
            cleared = before - after
        else:
            raise ValueError(f"未知的记录类型: {category}")

        save_paper(paper, self.path)
        return {"ok": True, "cleared": cleared, "category": category}

    def list_strategies(self) -> Dict[str, Any]:
        from core.strategy import list_strategy_specs, load_promoted

        return {
            "ok": True,
            "strategies": list_strategy_specs(),
            "promoted": load_promoted(),
        }

    def promote_strategy(
        self,
        strategy: str = "short_conservative",
        *,
        note: str = "",
        apply_to_paper: bool = False,
        overrides: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """显式晋级策略规格；可选写入当前模拟账户。"""
        from core.strategy import apply_strategy_to_paper, promote_strategy

        entry = promote_strategy(strategy, note=note, overrides=overrides)
        applied = False
        if apply_to_paper and os.path.isfile(self.path):
            paper = load_paper(self.path)
            apply_strategy_to_paper(paper, strategy)
            spec = entry.get("spec") or {}
            if overrides:
                rules = dict(paper.get("rules") or {})
                for k, v in overrides.items():
                    if v is not None and v != "":
                        rules[k] = v
                paper["rules"] = rules
            paper["updated_at"] = _now_iso()
            append_operation_log(
                paper,
                "settings",
                detail=f"晋级策略 {spec.get('strategy_id')}@{spec.get('version')}",
                meta={"strategy_id": spec.get("strategy_id"), "version": spec.get("version")},
            )
            save_paper(paper, self.path)
            applied = True
        return {"ok": True, "promoted": entry, "applied_to_paper": applied}

    def list_risk_blocks(self, *, limit: int = 40) -> Dict[str, Any]:
        from core.risk.block_outcome import list_risk_blocks

        if not os.path.isfile(self.path):
            return {"ok": True, "blocks": [], "initialized": False}
        paper = load_paper(self.path)
        blocks = list_risk_blocks(paper.get("operation_log") or [], limit=limit)
        return {"ok": True, "initialized": True, "blocks": blocks, "count": len(blocks)}

    def annotate_risk_block(
        self,
        *,
        outcome: str,
        index: Optional[int] = None,
        ts: Optional[str] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        from core.north_star import build_north_star_report
        from core.risk.block_outcome import annotate_risk_block

        if not os.path.isfile(self.path):
            raise FileNotFoundError("模拟账户不存在")
        paper = load_paper(self.path)
        out = annotate_risk_block(
            paper, index=index, ts=ts, outcome=outcome, note=note
        )
        if not out.get("ok"):
            return out
        save_paper(paper, self.path)
        try:
            paper["last_north_star"] = build_north_star_report(paper)
            save_paper(paper, self.path)
            out["north_star_risk_blocks"] = (paper["last_north_star"] or {}).get(
                "risk_blocks"
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            pass
        return out

