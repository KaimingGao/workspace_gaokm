"""QuantService · watching(②) / 日报 / 导出 / 解读 / 运维（P94 拆分）。

持仓联动 ``build_portfolio_bridge`` 已迁至 QuantCompareMixin（③）。
"""


import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.paths import QUANT_DAILY_PATH, QUANT_REPORTS_DIR, WATCHING_PATH

logger = logging.getLogger(__name__)


class QuantOpsMixin:
    def read_watching(self) -> Dict[str, Any]:
        """观察名单骨架：只读本地文件，不打 akshare / 不跑信号扫描。

        评分由 ``/api/watching/insights`` 异步补全；持仓仅挂接纸面落盘字段
        （成本/股数），避免 GET /api/watching 被行情锁拖过前端超时。
        """
        from core.watching.store import (
            read_watching,
            watchlist_names_for,
            watchlist_origins_for,
        )

        try:
            data = read_watching()
        except FileNotFoundError:
            return {"success": True, "exists": False, "path": WATCHING_PATH}
        uni = dict(data)
        uni["watchlist_origins"] = watchlist_origins_for(data)
        uni["watchlist_names"] = watchlist_names_for(data)

        codes = {str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()}
        holdings_by_code = {}
        if codes:
            try:
                from core.paper import load_paper
                from core.paths import PAPER_PATH

                if os.path.isfile(PAPER_PATH):
                    paper = load_paper(PAPER_PATH)
                    for h in paper.get("holdings") or []:
                        hc = str(h.get("stock_code") or "").strip()
                        if hc and hc in codes:
                            holdings_by_code[hc] = dict(h)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in quant_service_ops.py", exc_info=True)
                logger.warning("运维操作异常", exc_info=True)
        # 评分留给 insights 填充；保持字段存在以免前端判空出错
        uni["watchlist_scores"] = {}
        uni["watchlist_holdings"] = holdings_by_code

        return {"success": True, "exists": True, "watching": uni}

    def read_watching_file(self) -> Dict[str, Any]:
        from core.watching.store import read_watching

        try:
            data = read_watching()
        except FileNotFoundError:
            return {"ok": True, "exists": False, "path": WATCHING_PATH}
        return {"ok": True, "exists": True, "path": WATCHING_PATH, "watching": data}

    def check_watching_health(self) -> Dict[str, Any]:
        from core.watching.health import check_watching_health

        return check_watching_health()

    def list_report_archive(self, *, limit: int = 20) -> Dict[str, Any]:
        from quant.services.quant_report_index import list_quant_reports

        return list_quant_reports(limit=limit)

    def read_report_archive(self, filename: str) -> Dict[str, Any]:
        from quant.services.quant_report_index import read_quant_report_file

        return read_quant_report_file(filename)

    def delete_report_archive(
        self,
        *,
        stamp: Optional[str] = None,
        date: Optional[str] = None,
        dates: Optional[List[str]] = None,
        stamps: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from quant.services.quant_report_index import delete_quant_reports

        return delete_quant_reports(
            stamp=stamp, date=date, dates=dates, stamps=stamps
        )

    def build_health_summary(self) -> Dict[str, Any]:
        from quant.ops.daily_health import build_daily_health

        out = build_daily_health()
        out["task"] = "health"
        return out

    def build_package_info(self) -> Dict[str, Any]:
        from quant.ops.package_info import build_quant_package_info

        return build_quant_package_info()

    def refresh_watching(self, *, sync_paper: bool = False) -> Dict[str, Any]:
        from core.watching.store import refresh_watchlist, sync_paper_watchlist

        result = refresh_watchlist()
        out = {"success": True, "refresh": result}
        if sync_paper:
            out["paper_sync"] = sync_paper_watchlist()
        return out

    def plan_watching_to_paper(
        self,
        codes: Optional[list] = None,
        *,
        shares: Optional[int] = None,
        shares_by_code: Optional[dict] = None,
        amount_per_code: Optional[float] = None,
        amount_by_code: Optional[dict] = None,
        position_pct: Optional[float] = None,
    ) -> Dict[str, Any]:
        """建仓预览：确认前先看每只买多少、合计多少、剩余现金。"""
        from core.watching.store import plan_sync_to_paper

        return plan_sync_to_paper(
            codes=codes,
            lot_shares=shares,
            shares_by_code=shares_by_code,
            amount_per_code=amount_per_code,
            amount_by_code=amount_by_code,
            position_pct=position_pct,
        )

    def sync_watching_to_paper(
        self,
        codes: Optional[list] = None,
        *,
        shares: Optional[int] = None,
        shares_by_code: Optional[dict] = None,
        amount_per_code: Optional[float] = None,
        amount_by_code: Optional[dict] = None,
        position_pct: Optional[float] = None,
    ) -> Dict[str, Any]:
        """把观察名单（或所选 codes）写入模拟账户并按现价假买进持仓。"""
        from core.watching.store import sync_paper_watchlist

        return {
            "success": True,
            "paper_sync": sync_paper_watchlist(
                codes=codes,
                buy=True,
                lot_shares=shares,
                shares_by_code=shares_by_code,
                amount_per_code=amount_per_code,
                amount_by_code=amount_by_code,
                position_pct=position_pct,
            ),
        }

    def export_report_markdown(
        self,
        report: Optional[Dict[str, Any]] = None,
        *,
        use_saved: bool = True,
    ) -> Dict[str, Any]:
        from quant.services.quant_report_export import export_quant_report

        payload = report
        if payload is None and use_saved:
            payload = self.load_last_daily()
        return export_quant_report(payload, fmt="markdown")

    def export_report(
        self,
        report: Optional[Dict[str, Any]] = None,
        *,
        fmt: str = "markdown",
        use_saved: bool = True,
    ) -> Dict[str, Any]:
        from quant.services.quant_report_export import export_quant_report

        payload = report
        if payload is None and use_saved:
            payload = self.load_last_daily()
        return export_quant_report(payload, fmt=fmt)

    def export_portfolio_backtest_report(
        self,
        result: Optional[Dict[str, Any]] = None,
        *,
        fmt: str = "markdown",
    ) -> Dict[str, Any]:
        from quant.services.quant_report_export import export_portfolio_backtest_report

        return export_portfolio_backtest_report(result, fmt=fmt)

    def export_executive_summary(
        self,
        report: Optional[Dict[str, Any]] = None,
        *,
        use_saved: bool = True,
    ) -> Dict[str, Any]:
        from quant.services.quant_report_export import build_report_executive_summary

        payload = report
        if payload is None and use_saved:
            payload = self.load_last_daily()
        if not payload or payload.get("empty"):
            return {"success": False, "error": "quant_daily.json 为空"}
        summary = build_report_executive_summary(payload)
        summary["success"] = True
        return summary

    def interpret_report(
        self,
        report: Optional[Dict[str, Any]] = None,
        *,
        use_saved: bool = True,
        offline: bool = False,
    ) -> Dict[str, Any]:
        payload = report
        if payload is None and use_saved:
            saved = self.load_last_daily()
            if not saved.get("empty"):
                payload = saved
        if payload is None:
            payload = self.build_daily_report(
                include_portfolio_backtest=True,
                include_portfolio_neutral_compare=True,
            )
        from quant.services.quant_interpret import build_rule_based_interpret, interpret_quant_report

        if offline:
            out = build_rule_based_interpret(payload)
            out["task"] = "interpret"
            return out
        out = interpret_quant_report(payload)
        if out.get("success"):
            out["task"] = "interpret"
        return out

    def save_daily_report(self, report: Dict[str, Any]) -> str:
        os.makedirs(os.path.dirname(QUANT_DAILY_PATH) or ".", exist_ok=True)
        payload = dict(report or {})
        if not payload.get("generated_at"):
            payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
        with open(QUANT_DAILY_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        try:
            from core.score_ledger import freeze_from_daily_report

            freeze_from_daily_report(payload)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_service_ops.py", exc_info=True)
            logger.warning("运维后处理异常", exc_info=True)
        return QUANT_DAILY_PATH

    def freeze_score_ledger(self, as_of: Optional[str] = None) -> Dict[str, Any]:
        """手动冻结今日/指定日打分账本（书 + 可选横截面）。"""
        from core.score_ledger import freeze_from_cluster_book

        return freeze_from_cluster_book(as_of=as_of)

    def build_tau_shadow_review(
        self,
        as_of: Optional[str] = None,
        *,
        horizon_days: int = 1,
        autofill: bool = True,
    ) -> Dict[str, Any]:
        """A2：ŷ_τ 影子簿验收摘要（IC / 命中 / vs EOD 重叠）。"""
        from core.score_ledger import build_tau_shadow_review, default_as_of

        return build_tau_shadow_review(
            as_of or default_as_of(),
            horizon_days=horizon_days,
            autofill=autofill,
        )

    def build_nowcast_shadow_review(
        self,
        as_of: Optional[str] = None,
        *,
        horizon_days: int = 1,
        autofill: bool = True,
    ) -> Dict[str, Any]:
        """N3：ŷ_nowcast 影子簿验收摘要（IC / 命中 / Nordhaus / vs EOD）。"""
        from core.score_ledger import build_nowcast_shadow_review, default_as_of

        return build_nowcast_shadow_review(
            as_of or default_as_of(),
            horizon_days=horizon_days,
            autofill=autofill,
        )

    def fill_score_outcomes(
        self,
        as_of: Optional[str] = None,
        *,
        horizon_days: int = 3,
    ) -> Dict[str, Any]:
        from core.score_ledger import default_as_of, fill_outcomes

        return fill_outcomes(as_of or default_as_of(), horizon_days=horizon_days)

    def build_score_review(
        self,
        as_of: Optional[str] = None,
        *,
        horizon_days: int = 3,
        autofill: bool = True,
    ) -> Dict[str, Any]:
        from core.score_ledger import build_score_review, default_as_of

        return build_score_review(
            as_of or default_as_of(),
            horizon_days=horizon_days,
            autofill=autofill,
        )

    def fit_score_calibration(
        self,
        *,
        lookback_dates: int = 90,
        train_frac: float = 0.75,
        sample_source: str = "panel",
        lookback_bars: Optional[int] = None,
        horizon_days: Optional[int] = None,
        watching_limit: int = 100,
    ) -> Dict[str, Any]:
        from core.signal.score_calibration import fit_score_calibration_report

        return fit_score_calibration_report(
            lookback_dates=lookback_dates,
            train_frac=train_frac,
            sample_source=sample_source,
            lookback_bars=lookback_bars,
            horizon_days=horizon_days,
            watching_limit=watching_limit,
        )

    def persist_score_calibration(
        self,
        *,
        note: str = "",
        enable: bool = True,
    ) -> Dict[str, Any]:
        from core.signal.score_calibration import persist_score_calibration

        return persist_score_calibration(note=note, enable=enable)

    def get_score_calibration_model(self) -> Dict[str, Any]:
        from core.signal.score_calibration import (
            calibration_enabled,
            calibration_model_path,
            calibration_promote_safe,
            load_calibration_last_report,
            load_calibration_model,
            reconcile_calibration_switch,
        )

        try:
            reconcile_calibration_switch()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_service_ops.py", exc_info=True)
            pass
        live = load_calibration_model()
        last = load_calibration_last_report()
        promote_ok, promote_block = True, None
        heads = None
        if isinstance(live, dict):
            heads = live.get("heads")
        elif isinstance(last, dict):
            heads = last.get("heads")
        if isinstance(heads, dict):
            promote_ok, promote_block = calibration_promote_safe(heads)
        return {
            "success": True,
            "enabled": calibration_enabled(model_doc=live),
            "path": calibration_model_path(),
            "live": live,
            "last_report": last,
            "live_present": bool(live),
            "promote_ok": bool(promote_ok),
            "promote_block_reason": None if promote_ok else promote_block,
        }

    def list_score_ledger_dates(self, *, limit: int = 30) -> Dict[str, Any]:
        from core.market.calendar import prev_trading_day, resolve_session_date
        from core.score_ledger import default_as_of, list_ledger_dates, list_ledger_entries

        entries = list_ledger_entries(limit=limit)
        dates = [str(e.get("as_of") or "") for e in entries if e.get("as_of")]
        if not dates:
            dates = list_ledger_dates(limit=limit)
        cal = default_as_of()  # 上一交易日
        sess = resolve_session_date()
        immature_set = {
            str(e.get("as_of") or "")
            for e in entries
            if e.get("immature") and e.get("as_of")
        }
        filled = [
            str(e.get("as_of") or "")
            for e in entries
            if e.get("as_of") and int(e.get("outcomes_filled") or 0) > 0
        ]
        # 复盘默认：已回填日 > as_of+h 已到期日。昨收未进缓存时 as_of=昨仍薄样本
        safe = prev_trading_day(sess, n=2) or cal or sess
        pick = next((d for d in filled if d), None) or next(
            (d for d in dates if d and d <= safe and d not in immature_set),
            None,
        ) or next(
            (d for d in dates if d and d <= cal and d not in immature_set),
            None,
        ) or next(
            (d for d in dates if d and d not in immature_set),
            None,
        ) or cal or (dates[0] if dates else "")
        return {
            "success": True,
            "dates": dates,
            "entries": entries,
            "default_as_of": pick,
            "calendar_as_of": cal,
            "safe_as_of": safe,
            "session_date": sess,
        }

    def delete_score_ledger(
        self,
        *,
        as_of: Optional[str] = None,
        dates: Optional[List[str]] = None,
        include_outcomes: bool = True,
    ) -> Dict[str, Any]:
        from core.score_ledger import delete_ledger, delete_ledgers

        batch = [str(d).strip() for d in (dates or []) if str(d).strip()]
        if as_of and str(as_of).strip():
            batch.append(str(as_of).strip())
        # 去重保序
        seen = set()
        uniq: List[str] = []
        for d in batch:
            if d in seen:
                continue
            seen.add(d)
            uniq.append(d)
        if not uniq:
            return {"success": False, "error": "未指定 as_of / dates"}
        if len(uniq) == 1:
            return delete_ledger(uniq[0], include_outcomes=include_outcomes)
        return delete_ledgers(uniq, include_outcomes=include_outcomes)

    def score_ledger_code_series(self, code: str, *, limit: int = 40) -> Dict[str, Any]:
        from core.score_ledger import code_yhat_series

        return code_yhat_series(code, limit=limit)

    def score_ledger_stock_panel(
        self, code: str, *, lookback: int = 10
    ) -> Dict[str, Any]:
        from core.score_ledger import stock_panel_series

        return stock_panel_series(code, lookback=lookback)

    def score_review_hit_series(
        self, *, horizon_days: int = 3, limit: int = 20, autofill: bool = False
    ) -> Dict[str, Any]:
        from core.score_ledger import hit_rate_series

        return hit_rate_series(
            horizon_days=horizon_days, limit=limit, autofill=autofill
        )

    def save_report_exports(
        self,
        report: Dict[str, Any],
        *,
        formats: tuple = ("markdown", "html"),
    ) -> Dict[str, Any]:
        """将日报写入 data/reports/quant_daily_YYYYMMDD.{md,html}。"""
        if not report or report.get("empty"):
            return {"success": False, "error": "无报告数据"}

        os.makedirs(QUANT_REPORTS_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        paths: Dict[str, str] = {}
        for fmt in formats:
            exported = self.export_report(report, fmt=fmt, use_saved=False)
            if not exported.get("success"):
                continue
            ext = "md" if fmt == "markdown" else "html"
            path = os.path.join(QUANT_REPORTS_DIR, f"quant_daily_{stamp}.{ext}")
            with open(path, "w", encoding="utf-8") as f:
                f.write(exported.get("content") or "")
            paths[fmt] = path

        if not paths:
            return {"success": False, "error": "导出失败"}
        return {"success": True, "paths": paths, "stamp": stamp}

    def load_last_daily(self) -> Dict[str, Any]:
        if not os.path.isfile(QUANT_DAILY_PATH):
            return {"success": True, "empty": True}
        with open(QUANT_DAILY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"success": True, "empty": True}
        generated = data.get("generated_at") or data.get("saved_at")
        if not generated:
            try:
                generated = datetime.fromtimestamp(
                    os.path.getmtime(QUANT_DAILY_PATH)
                ).isoformat(timespec="seconds")
            except OSError:
                generated = None
        data["snapshot_meta"] = {
            "source": "quant_daily",
            "path": QUANT_DAILY_PATH,
            "generated_at": generated,
            "frozen": True,
            "note": "日报冻结摘要；与当页「Top-K 回测 / 中性化对照」结果可能不一致。",
        }
        return data

    def build_daily_report(
        self,
        code: str = "茅台",
        *,
        include_cross_section: bool = True,
        include_portfolio_backtest: bool = True,
        include_portfolio_neutral_compare: bool = True,
        include_legacy_probe: bool = False,
    ) -> Dict[str, Any]:
        """量化日报：主叙事=组ŷ / 簿 / OOS / 横截面ŷ / Top-K(ŷ)。

        单票 IC·OLS·权建议·阈值 默认不跑，仅 ``include_legacy_probe=True`` 进附录。
        """
        cfg = self.config_summary()
        ic = None
        factor_exp = None
        factor_ols = None
        weight_suggest = None
        threshold_suggest = None
        if include_legacy_probe:
            ic = self.run_factor_report(code)
            factor_exp = self.run_factor_experiment(code)
            factor_ols = self.run_factor_ols_experiment(code)
            cluster_yhat_active = False
            try:
                from core.signal.cluster_live import cluster_status_public

                cs = (cluster_status_public(include_audit=False) or {}).get("cluster_scoring") or {}
                cluster_yhat_active = bool(
                    cs.get("enabled") and cs.get("mode") in ("shadow", "active")
                )
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in quant_service_ops.py", exc_info=True)
                cluster_yhat_active = False
            if not cluster_yhat_active:
                from core.signal.weight_suggest import suggest_weights_from_ic

                weight_suggest = (
                    suggest_weights_from_ic(factor_exp) if factor_exp.get("success") else None
                )
                if weight_suggest and weight_suggest.get("success"):
                    from core.signal.weight_suggest import format_weight_config_diff

                    weight_suggest["config_diff"] = format_weight_config_diff(weight_suggest)
                    weight_suggest["deprecated_for_scoring"] = True
                    weight_suggest["note"] = (
                        "附录·遗留 IC 小步权诊断；选股真源为 return_model → predicted_score（ŷ），"
                        "不自动写 signal_config"
                    )
            threshold_suggest = self.suggest_thresholds(code)
            if not threshold_suggest.get("success"):
                threshold_suggest = None
            elif isinstance(threshold_suggest, dict):
                threshold_suggest = dict(threshold_suggest)
                threshold_suggest["appendix"] = True
                note0 = threshold_suggest.get("note") or ""
                threshold_suggest["note"] = (
                    "附录·单票阈值探针。 " + str(note0)
                ).strip()

        portfolio_summary = self.portfolio_daily_summary() if include_portfolio_backtest else None
        neutral_compare_summary = None
        if include_portfolio_backtest and include_portfolio_neutral_compare:
            neutral_compare_summary = self.portfolio_neutral_compare_summary()
        cluster_live = None
        try:
            from core.signal.cluster_live import (
                _summarize_cluster_oos,
                cluster_status_public,
                load_active_cluster_book,
                load_active_cluster_weights,
            )

            st = cluster_status_public(include_audit=False)
            cs = (st or {}).get("cluster_scoring") or {}
            if cs.get("mode") in ("shadow", "active"):
                active = load_active_cluster_weights() or {}
                clusters = active.get("clusters") or []
                oos_summary = _summarize_cluster_oos(clusters)
                book_doc = load_active_cluster_book() or {}
                book_rows = list(book_doc.get("book") or [])
                book_top = []
                for r in book_rows[:8]:
                    if not isinstance(r, dict):
                        continue
                    try:
                        from core.signal.rebalance_tracks import table_yhat_score_value

                        yhat = table_yhat_score_value(r)
                    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                        logger.debug("catch except Exception: in quant_service_ops.py", exc_info=True)
                        yhat = r.get("predicted_score")
                        if yhat is None:
                            yhat = r.get("score")
                        try:
                            if yhat is not None and abs(float(yhat)) >= 10.0:
                                yhat = None
                        except (TypeError, ValueError):
                            yhat = None
                    book_top.append(
                        {
                            "stock_code": r.get("stock_code"),
                            "stock_name": r.get("stock_name"),
                            "score": yhat,
                            "cluster_label": r.get("cluster_label"),
                            "rank": r.get("rank"),
                        }
                    )
                group_models = []
                for cl in clusters:
                    if not isinstance(cl, dict):
                        continue
                    rm = cl.get("return_model") if isinstance(cl.get("return_model"), dict) else {}
                    coef = rm.get("coefficients") if isinstance(rm.get("coefficients"), dict) else {}
                    gate = cl.get("oos_gate") if isinstance(cl.get("oos_gate"), dict) else {}
                    group_models.append(
                        {
                            "label": cl.get("label"),
                            "n_members": len(cl.get("members") or []),
                            "n_coef": len(coef),
                            "sample_count": rm.get("sample_count"),
                            "ridge_lambda": rm.get("ridge_lambda"),
                            "oos_passed": cl.get("oos_passed"),
                            "oos_skipped": bool(gate.get("skipped")),
                        }
                    )
                cluster_live = {
                    "mode": cs.get("mode"),
                    "enabled": cs.get("enabled"),
                    "version": ((st or {}).get("active") or {}).get("version"),
                    "coverage": ((st or {}).get("health") or {}).get("coverage"),
                    "age_days": ((st or {}).get("health") or {}).get("age_days"),
                    "stale": ((st or {}).get("health") or {}).get("stale"),
                    "alerts": ((st or {}).get("health") or {}).get("alerts") or [],
                    "book_names": ((st or {}).get("book") or {}).get("name_count"),
                    "book_top": book_top,
                    "group_models": group_models,
                    "n_groups_with_model": sum(
                        1 for g in group_models if int(g.get("n_coef") or 0) > 0
                    ),
                    "oos_summary": oos_summary,
                    "note": (
                        "组ŷ live；未写 signal_config.weights；"
                        "OOS=heuristic 基线 vs ŷ 研究臂；过门≠自动 promote；"
                        "OOS 计数来自 active 落盘快照（生成日报时不重跑）"
                    ),
                }
        except Exception as exc:
            logger.exception('unexpected error in build_daily_report')
            cluster_live = {"success": False, "error": str(exc)}

        scoring = (cfg.get("scoring") if isinstance(cfg, dict) else None) or {}
        y_check_summary = None
        try:
            from core.signal.y_state import ledger_y_check_daily_summary

            y_check_summary = ledger_y_check_daily_summary(include_hit=True)
        except Exception as exc:
            logger.exception('unexpected error in build_daily_report')
            y_check_summary = {"success": False, "error": str(exc)}

        report: Dict[str, Any] = {
            "success": True,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "narrative": "cluster_yhat",
            "config": cfg,
            "scoring": {
                "rank_mode": scoring.get("rank_mode") or "predicted_score",
                "min_predicted_score": scoring.get("min_predicted_score"),
                "note": (
                    (cfg.get("product_note") if isinstance(cfg, dict) else None)
                    or "选股真源=predicted_score（ŷ）· 主叙事=组ŷ/簿/OOS/横截面/Top-K"
                ),
            },
            "strategies": self.list_strategies(),
            "portfolio_backtest_summary": portfolio_summary,
            "portfolio_neutral_compare_summary": neutral_compare_summary,
            "cluster_live": cluster_live,
            "y_check_summary": y_check_summary,
        }
        if include_cross_section:
            report["cross_section"] = self.run_cross_section(limit=10)
        if include_legacy_probe:
            report["factor_ic"] = ic
            report["factor_experiment"] = factor_exp
            report["factor_ols"] = factor_ols
            report["weight_suggest"] = weight_suggest
            report["threshold_suggest"] = threshold_suggest
            report["appendix"] = {
                "legacy_probe": True,
                "probe_code": code,
                "note": "单票 IC/OLS/权建议/阈值为附录探针，不驱动选股",
            }
        return report
