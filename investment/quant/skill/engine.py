"""quant Skill：量化研究台（组合回测、横截面、对照、建议）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

AVAILABLE_TASKS = (
    "daily_summary",
    "cross_section",
    "portfolio_backtest",
    "portfolio_neutral_compare",
    "weight_suggest",
    "threshold_suggest",
    "interpret",
    "health",
    "config_diff",
    "daily_presets",
    "portfolio_bridge",
    "package_info",
    "factor_ols",
    "factor_corr",
    "t0_backtest",
)


class QuantEngine:
    def __init__(self) -> None:
        from quant.services.quant_service import QuantService

        self._svc = QuantService()

    def run(self, params: dict) -> Dict[str, Any]:
        task = str(params.get("task") or "").strip().lower()
        if not task:
            return {
                "success": False,
                "error": "请指定 task",
                "available_tasks": list(AVAILABLE_TASKS),
            }
        if task not in AVAILABLE_TASKS:
            return {
                "success": False,
                "error": f"未知 task: {task}",
                "available_tasks": list(AVAILABLE_TASKS),
            }

        code = str(params.get("stock_code") or params.get("code") or "茅台").strip()
        default_lb = 30 if task == "t0_backtest" else 120
        min_lb = 20 if task == "t0_backtest" else 40
        lookback = max(
            min_lb,
            min(int(params.get("lookback") or params.get("lookback_days") or default_lb), 500),
        )

        if task == "daily_summary":
            use_saved = bool(params.get("use_saved", True))
            if use_saved:
                saved = self._svc.load_last_daily()
                if not saved.get("empty"):
                    return self._compact_daily(saved)
            report = self._svc.build_daily_report(
                code,
                include_cross_section=bool(params.get("include_cross_section")),
                include_portfolio_backtest=bool(params.get("include_portfolio_backtest", True)),
                include_portfolio_neutral_compare=bool(
                    params.get("include_portfolio_neutral_compare", True)
                ),
            )
            return self._compact_daily(report)

        if task == "cross_section":
            codes = self._as_codes(params.get("codes") or params.get("stock_codes"))
            return self._svc.run_cross_section(
                codes=codes or None,
                limit=int(params.get("limit") or 10),
                min_score=params.get("min_score"),
                horizon_days=int(params.get("horizon_days") or 3),
            )

        if task == "portfolio_backtest":
            codes = self._as_codes(params.get("codes") or params.get("stock_codes"))
            return self._svc.run_portfolio_backtest(
                codes=codes or None,
                lookback=lookback,
                top_k=int(params.get("top_k") or 3),
                horizon_days=int(params.get("horizon_days") or 3),
                min_score=float(params.get("min_score") or 55),
                apply_costs=bool(params.get("apply_costs")),
            )

        if task == "portfolio_neutral_compare":
            codes = self._as_codes(params.get("codes") or params.get("stock_codes"))
            out = self._svc.run_portfolio_neutral_compare(
                codes=codes or None,
                lookback=lookback,
                top_k=int(params.get("top_k") or 3),
                horizon_days=int(params.get("horizon_days") or 3),
                min_score=float(params.get("min_score") or 55),
                apply_costs=bool(params.get("apply_costs")),
            )
            out["task"] = "portfolio_neutral_compare"
            return out

        if task == "factor_ols":
            return self._svc.run_factor_ols_experiment(
                code,
                lookback=lookback,
                horizon_days=int(params.get("horizon_days") or 3),
            )

        if task == "factor_corr":
            codes = self._as_codes(params.get("codes") or params.get("stock_codes"))
            return self._svc.run_factor_corr(
                codes=codes or None,
                limit=int(params.get("limit") or 30),
                horizon_days=int(params.get("horizon_days") or 3),
            )

        if task == "t0_backtest":
            rules = {
                "t0_ratio": params.get("t0_ratio"),
                "sell_trigger_pct": params.get("sell_trigger_pct"),
                "buy_trigger_pct": params.get("buy_trigger_pct"),
                "must_cover_same_day": bool(params.get("must_cover_same_day")),
            }
            for key in ("fill_mode", "direction", "min_range_pct", "path_mode"):
                if params.get(key) is not None:
                    rules[key] = params.get(key)
            if "use_atr" in params:
                rules["use_atr"] = bool(params.get("use_atr"))
            from_paper = bool(params.get("from_paper"))
            if not str(code or "").strip():
                from_paper = True
            return self._svc.run_t0_backtest(
                code or "",
                lookback=lookback,
                initial_shares=float(params.get("initial_shares") or params.get("shares") or 1000),
                rules=rules,
                from_paper=from_paper,
                codes=params.get("codes"),
                compare_optimistic=bool(params.get("compare_optimistic", True)),
                use_minute=bool(params.get("use_minute", True)),
                compare_daily=bool(params.get("compare_daily", True)),
            )

        if task == "weight_suggest":
            return self._svc.suggest_weights(
                code,
                lookback=lookback,
                horizon_days=int(params.get("horizon_days") or 3),
            )

        if task == "threshold_suggest":
            use_watching = bool(params.get("use_watching"))
            return self._svc.suggest_thresholds(
                code,
                lookback=lookback,
                use_watching=use_watching,
                watching_limit=int(params.get("watching_limit") or 5),
            )

        if task == "interpret":
            use_saved = bool(params.get("use_saved", True))
            offline = bool(params.get("offline"))
            report = None
            if use_saved:
                saved = self._svc.load_last_daily()
                if not saved.get("empty"):
                    report = saved
            if report is None:
                report = self._svc.build_daily_report(
                    code,
                    include_portfolio_backtest=True,
                    include_portfolio_neutral_compare=True,
                )
            if offline:
                from quant.services.quant_interpret import build_rule_based_interpret

                out = build_rule_based_interpret(report)
                out["task"] = "interpret"
                return out
            out = self._svc.interpret_report(report, use_saved=False)
            out["task"] = "interpret"
            return out

        if task == "health":
            return self._svc.build_health_summary()

        if task == "config_diff":
            preview = self._svc.build_config_diff_preview(
                code,
                use_saved=bool(params.get("use_saved", True)),
            )
            weights = preview.get("weights") or {}
            thresholds = preview.get("thresholds") or {}
            return {
                "success": True,
                "task": "config_diff",
                "ok": bool(preview.get("ok")),
                "readonly": True,
                "weights": weights if weights.get("success") else None,
                "thresholds": thresholds if thresholds.get("success") else None,
                "change_counts": {
                    "weights": len(weights.get("changes") or {}),
                    "stance_thresholds": len(thresholds.get("changes") or {}),
                },
                "note": preview.get("note"),
            }

        if task == "daily_presets":
            from quant.ops.daily_presets import list_daily_presets

            return {
                "success": True,
                "task": "daily_presets",
                "presets": list_daily_presets(),
                "note": "CLI: python3 research/daily_run.py --preset <name>；Web 运维区可选 preset 运行 daily。",
            }

        if task == "portfolio_bridge":
            return self._svc.build_portfolio_bridge(
                include_stance=bool(params.get("include_stance", True)),
            )

        if task == "package_info":
            out = self._svc.build_package_info()
            out["task"] = "package_info"
            out["note"] = "quant 包 canonical 路径；P41 起 shim 已移除，见 removed_shim_paths。"
            return out

        return {"success": False, "error": f"未实现 task: {task}"}

    @staticmethod
    def _as_codes(raw) -> Optional[List[str]]:
        if not raw:
            return None
        if isinstance(raw, str):
            return [c.strip() for c in raw.replace("，", ",").split(",") if c.strip()]
        if isinstance(raw, list):
            return [str(c).strip() for c in raw if str(c).strip()]
        return None

    @staticmethod
    def _compact_daily(report: Dict[str, Any]) -> Dict[str, Any]:
        from quant.services.quant_interpret import compact_quant_report

        out = compact_quant_report(report)
        out["success"] = True
        out["task"] = "daily_summary"
        out["note"] = "量化研究摘要；详细字段见 quant_daily.json。"
        if report.get("threshold_suggest"):
            out["threshold_suggest"] = report.get("threshold_suggest")
        return out
