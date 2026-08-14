"""PaperService · 账户状态 / 资金 / 策略晋升。"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.paper import (
    init_from_example,
    load_paper,
    mark_to_market,
    save_paper,
    append_snapshot,
    append_operation_log,
    paper_write_lock,
    _now_iso,
)
from core.paper_costs import enrich_operation_log_with_trade_fees
from services.paper_helpers import _build_score_formula


class PaperAccountMixin:
    def _operation_log_for_ui(self, paper: dict, *, limit: int = 50) -> list:
        logs = (paper.get("operation_log") or [])[-max(1, int(limit or 50)) :]
        return enrich_operation_log_with_trade_fees(
            logs,
            paper.get("trades") or [],
            cost_model=paper.get("cost_model"),
        )

    def _compute_holding_scores(self, paper: dict, summary: dict) -> dict:
        """计算当前持仓的评分，合并到 summary.holdings 中。

        优先读 active 分池簿（与观察/调仓同源 tip 字段，毫秒级）；簿外票才
        直调 ``score_stock``（带超时）。**不**经 observation_pool / min_score TopN。
        """
        holdings = paper.get("holdings") or []
        holding_codes = [str(h.get("stock_code")) for h in holdings if h.get("stock_code")]
        if not holding_codes:
            return summary

        try:
            import concurrent.futures
            import logging

            from core.signal.dual_score import dual_score_book_fields
            from core.signal.score_display import annotate_score_gate, selection_min_score
            from core.signal.score_stock import score_stock

            log = logging.getLogger(__name__)
            rules = paper.get("rules") or {}
            horizon = max(1, min(int(rules.get("horizon_days") or 3), 3))
            gate = selection_min_score(paper)

            def _pack_item(item: dict, *, cluster_mode=None) -> Dict[str, Any]:
                out = {
                    "score": item.get("score", item.get("predicted_score")),
                    "predicted_score": item.get(
                        "predicted_score", item.get("score")
                    ),
                    "sub_scores": item.get("sub_scores"),
                    "factor_contrib": item.get("factor_contrib"),
                    "reasons": item.get("reasons") or item.get("score_reasons"),
                    "hard_reject": item.get("hard_reject"),
                    "reject_reason": item.get("reject_reason"),
                    "weight_source": item.get("weight_source"),
                    "cluster_label": item.get("cluster_label"),
                    "cluster_mode": item.get("cluster_mode") or cluster_mode,
                    "cluster_version": item.get("cluster_version"),
                    "score_global": item.get("score_global"),
                    "score_cluster": item.get("score_cluster"),
                    "return_model_source": item.get("return_model_source"),
                    "return_model": item.get("return_model"),
                    "factor_coefficients": item.get("factor_coefficients"),
                    "score_formula": item.get("score_formula"),
                    "score_formula_terms": item.get("score_formula_terms"),
                }
                try:
                    out.update(dual_score_book_fields(item))
                except Exception:
                    pass
                return out

            score_by_code: Dict[str, Any] = {}
            # 1) 分池簿快路径（持仓几乎都在 scored_all 里）
            try:
                from core.signal.cluster_live import load_active_cluster_book

                book_doc = load_active_cluster_book() or {}
                book_rows = list(book_doc.get("scored_all") or []) + list(
                    book_doc.get("book") or []
                )
                book_meta = book_doc.get("meta") or {}
                book_mode = book_meta.get("cluster_mode") or book_meta.get("mode")
                for row in book_rows:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("stock_code") or row.get("code") or "").strip()
                    if not code or code in score_by_code:
                        continue
                    if code not in holding_codes:
                        continue
                    score_by_code[code] = _pack_item(row, cluster_mode=book_mode)
            except Exception as e:
                log.debug("cluster book tip hydrate skipped: %s", e)

            missing = [c for c in holding_codes if c not in score_by_code]
            # 2) 簿外才 live 打分；单票超时，避免拖死 /api/paper
            if missing:
                per_timeout = 8.0
                workers = min(len(missing), 6)

                def _one(code: str) -> tuple:
                    try:
                        result = score_stock(
                            code,
                            horizon_days=horizon,
                            skip_fundamentals=True,
                        )
                    except Exception as e:
                        return code, {"success": False, "error": str(e)}
                    return code, result or {}

                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    futs = {ex.submit(_one, c): c for c in missing}
                    try:
                        for fut in concurrent.futures.as_completed(
                            futs, timeout=per_timeout * max(1, len(missing) / workers) + 2
                        ):
                            code = futs[fut]
                            try:
                                _, result = fut.result(timeout=per_timeout)
                            except Exception as e:
                                log.warning("holding score timeout/fail %s: %s", code, e)
                                continue
                            item = (
                                (result.get("signal_item") or {})
                                if result.get("success")
                                else {}
                            )
                            if not item:
                                continue
                            score_by_code[code] = _pack_item(
                                item, cluster_mode=result.get("cluster_mode")
                            )
                    except concurrent.futures.TimeoutError:
                        log.warning(
                            "holding scores partial timeout; book=%d live_pending=%d",
                            len(holding_codes) - len(missing),
                            len(missing),
                        )

            enriched_holdings = []
            for h in summary.get("holdings") or []:
                code = str(h.get("stock_code") or "")
                enriched = dict(h)
                score_info = score_by_code.get(code)
                if score_info:
                    enriched["score"] = score_info.get("score")
                    enriched["predicted_score"] = score_info.get("predicted_score")
                    enriched["sub_scores"] = score_info.get("sub_scores")
                    enriched["factor_contrib"] = score_info.get("factor_contrib")
                    enriched["score_reasons"] = score_info.get("reasons")
                    enriched["hard_reject"] = score_info.get("hard_reject")
                    enriched["reject_reason"] = score_info.get("reject_reason")
                    enriched["weight_source"] = score_info.get("weight_source")
                    enriched["cluster_label"] = score_info.get("cluster_label")
                    enriched["cluster_mode"] = score_info.get("cluster_mode")
                    enriched["cluster_version"] = score_info.get("cluster_version")
                    enriched["score_global"] = score_info.get("score_global")
                    enriched["score_cluster"] = score_info.get("score_cluster")
                    enriched["return_model_source"] = score_info.get("return_model_source")
                    enriched["factor_coefficients"] = score_info.get("factor_coefficients")
                    enriched["score_formula_terms"] = score_info.get("score_formula_terms")
                    enriched["score_formula"] = score_info.get(
                        "score_formula"
                    ) or _build_score_formula(score_info)
                    for k in (
                        "predicted_score_tau",
                        "score_rem",
                        "predicted_score_rem",
                        "gap_pct",
                        "event_prior",
                        "as_of_tau",
                        "y_spec_tau",
                        "features_tau",
                        "formula_terms_tau",
                        "score_formula_terms_tau",
                        "score_formula_tau",
                        "factor_coefficients_tau",
                        "dual_score_fusion",
                        "dual_score_weights",
                        "predicted_score_blend",
                        "predicted_score_eod",
                    ):
                        if k in score_info:
                            enriched[k] = score_info.get(k)
                    gate_meta = annotate_score_gate(
                        score_info.get("score"), paper=paper, min_score=gate
                    )
                    enriched["min_score"] = gate_meta["min_score"]
                    enriched["below_min_score"] = gate_meta["below_min_score"]
                else:
                    enriched["score"] = None
                    enriched["min_score"] = gate
                    enriched["below_min_score"] = False
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

    def status(self, *, lite: bool = False) -> Dict[str, Any]:
        """账户摘要。

        ``lite=True``：只读落盘（持仓码/流水），不盯市、不打分——供数据中心
        快速标「已持」，避免与行情锁互相拖死。
        """
        if not os.path.isfile(self.path):
            return {"ok": True, "initialized": False, "path": self.path, "lite": lite}
        paper = load_paper(self.path)
        if lite:
            raw_holdings = []
            for h in paper.get("holdings") or []:
                raw_holdings.append(
                    {
                        "stock_code": h.get("stock_code"),
                        "stock_name": h.get("stock_name"),
                        "shares": h.get("shares"),
                        "cost": h.get("cost"),
                        "bought_at": h.get("bought_at"),
                        "origin": h.get("origin"),
                    }
                )
            return {
                "ok": True,
                "initialized": True,
                "lite": True,
                "path": self.path,
                "name": paper.get("name"),
                "strategy_id": paper.get("strategy_id") or "short",
                "summary": {
                    "cash": paper.get("cash"),
                    "holdings": raw_holdings,
                },
                "operation_log": self._operation_log_for_ui(paper, limit=50),
            }
        summary = mark_to_market(paper)
        summary = self._compute_holding_scores(paper, summary)
        north_star = paper.get("last_north_star")
        if not isinstance(north_star, dict):
            try:
                from core.north_star import build_north_star_report

                north_star = build_north_star_report(paper)
            except Exception:
                north_star = None
        exposure = None
        try:
            from core.risk.exposure import build_exposure_matrix

            exposure = build_exposure_matrix(paper, summary)
        except Exception:
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
            "strategy_id": paper.get("strategy_id") or "short",
            "strategy_version": paper.get("strategy_version"),
            "cost_model": paper.get("cost_model") or "simple_cn",
            "cost_params": paper.get("cost_params") or {},
            "ops_report": ops,
            "exposure": exposure,
            "north_star": north_star,
            "rules": paper.get("rules") or {},
            "summary": summary,
            "snapshots": paper.get("snapshots") or [],
            "operation_log": self._operation_log_for_ui(paper, limit=50),
            "recent_trades": (paper.get("trades") or [])[-10:],
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
        from core.paper_costs import COST_MODELS, resolve_cost_model

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
        strategy: str = "short",
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
        except Exception:
            pass
        return out

