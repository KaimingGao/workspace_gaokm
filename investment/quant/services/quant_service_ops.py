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
                include_portfolio_neutral_compare=False,
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
        return QUANT_DAILY_PATH

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
            "note": "日报冻结摘要；与当页「历史回测」结果可能不一致。",
        }
        return data

    def build_daily_report(
        self,
        code: str = "茅台",
        *,
        include_cross_section: bool = True,
        include_portfolio_backtest: bool = True,
        include_portfolio_neutral_compare: bool = False,
        include_legacy_probe: bool = False,
        top_k: Optional[int] = None,
        horizon_days: Optional[int] = None,
        lookback: Optional[int] = None,
        fusion_w_co: Optional[float] = None,
        rank_enter: Optional[float] = None,
        rank_strong: Optional[float] = None,
    ) -> Dict[str, Any]:
        """量化日报：主叙事=组ŷ / 簿 / OOS / 横截面ŷ / 历史回测（rank_lots）。

        单票 IC·OLS·权建议·阈值 默认不跑，仅 ``include_legacy_probe=True`` 进附录。
        历史回测缺省对齐 /replay：paper_replay · α / rank入场 1.2% / rank强 1.2% / lookback 30（下限 10）。
        中性化对照默认不下发（ŷ 路径开关空转）；仅显式 ``include_portfolio_neutral_compare=True``。
        ``top_k`` / ``horizon_days`` 仅留给该研究腿。
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

        portfolio_bt_kwargs: Dict[str, Any] = {}
        if lookback is not None:
            portfolio_bt_kwargs["lookback"] = int(lookback)
        if fusion_w_co is not None:
            portfolio_bt_kwargs["fusion_w_co"] = float(fusion_w_co)
        if rank_enter is not None:
            portfolio_bt_kwargs["rank_enter"] = float(rank_enter)
        if rank_strong is not None:
            portfolio_bt_kwargs["rank_strong"] = float(rank_strong)
        portfolio_summary = (
            self.portfolio_daily_summary(**portfolio_bt_kwargs)
            if include_portfolio_backtest
            else None
        )
        neutral_compare_summary = None
        if include_portfolio_backtest and include_portfolio_neutral_compare:
            neutral_kwargs: Dict[str, Any] = {}
            if top_k is not None:
                neutral_kwargs["top_k"] = int(top_k)
            if horizon_days is not None:
                neutral_kwargs["horizon_days"] = int(horizon_days)
            if lookback is not None:
                neutral_kwargs["lookback"] = int(lookback)
            neutral_compare_summary = self.portfolio_neutral_compare_summary(
                **neutral_kwargs
            )
        cluster_live = {
            "success": False,
            "cluster_retired": True,
            "error": "cluster_retired",
            "mode": "off",
            "enabled": False,
            "note": "分组 live 已退役；选股真源=全局 return_model ŷ",
        }

        scoring = (cfg.get("scoring") if isinstance(cfg, dict) else None) or {}

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
                    or "选股真源=predicted_score（ŷ）· 主叙事=组ŷ/簿/OOS/横截面/历史回测"
                ),
            },
            "strategies": self.list_strategies(),
            "portfolio_backtest_summary": portfolio_summary,
            "cluster_live": cluster_live,
        }
        if include_portfolio_backtest and include_portfolio_neutral_compare:
            report["portfolio_neutral_compare_summary"] = neutral_compare_summary
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
