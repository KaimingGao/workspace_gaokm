"""每日任务编排（纸面 + eval + 量化，Web / cron / CLI 共用）。"""


import logging

logger = logging.getLogger(__name__)
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.paths import DAILY_LAST_RUN_PATH
from quant.ops.daily_presets import resolve_daily_preset
from services.eval_service import EvalService
from services.paper_service import PaperService


class DailyRunService:
    def __init__(
        self,
        paper: Optional[PaperService] = None,
        evals: Optional[EvalService] = None,
        last_run_path: Optional[str] = None,
    ) -> None:
        self.paper = paper or PaperService()
        self.evals = evals or EvalService()
        self.last_run_path = last_run_path or DAILY_LAST_RUN_PATH

    def load_last_run(self) -> Dict[str, Any]:
        if not os.path.isfile(self.last_run_path):
            return {"success": True, "empty": True}
        with open(self.last_run_path, encoding="utf-8") as f:
            return json.load(f)

    def _save_last_run(self, payload: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.last_run_path) or ".", exist_ok=True)
        with open(self.last_run_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def run(
        self,
        *,
        preset: Optional[str] = None,
        paper_run: Optional[bool] = None,
        paper_holding_cycle: Optional[bool] = None,
        paper_buy: Optional[bool] = None,
        eval_mock: Optional[bool] = None,
        eval_agent: Optional[bool] = None,
        quant_report: Optional[bool] = None,
        watching_refresh: Optional[bool] = None,
        cross_section: Optional[bool] = None,
        sync_paper_watchlist: Optional[bool] = None,
        paper_rebalance: Optional[bool] = None,
        paper_cross_section_rebalance: Optional[bool] = None,
        export_quant_report: Optional[bool] = None,
        lookback: Optional[int] = None,
        fusion_w_co: Optional[float] = None,
        rank_enter: Optional[float] = None,
        rank_strong: Optional[float] = None,
    ) -> Dict[str, Any]:
        overrides = {
            k: v
            for k, v in {
                "paper_run": paper_run,
                "paper_holding_cycle": paper_holding_cycle,
                "paper_buy": paper_buy,
                "eval_mock": eval_mock,
                "eval_agent": eval_agent,
                "quant_report": quant_report,
                "watching_refresh": watching_refresh,
                "cross_section": cross_section,
                "sync_paper_watchlist": sync_paper_watchlist,
                "paper_rebalance": paper_rebalance,
                "paper_cross_section_rebalance": paper_cross_section_rebalance,
                "export_quant_report": export_quant_report,
            }.items()
            if v is not None
        }
        resolved = resolve_daily_preset(preset, overrides=overrides)
        flags = resolved["flags"]
        paper_run = bool(flags["paper_run"])
        paper_buy = bool(flags["paper_buy"])
        eval_mock = bool(flags["eval_mock"])
        eval_agent = bool(flags["eval_agent"])
        quant_report = bool(flags["quant_report"])
        watching_refresh = bool(flags["watching_refresh"])
        cross_section = bool(flags["cross_section"])
        sync_paper_watchlist = bool(flags["sync_paper_watchlist"])
        paper_rebalance = bool(flags["paper_rebalance"])
        export_quant_report = bool(flags["export_quant_report"])

        if not any(flags.values()):
            return {"ok": False, "error": "请至少选择一项任务"}

        steps: List[dict] = []
        failures: List[str] = []

        if watching_refresh or sync_paper_watchlist:
            from quant.services.quant_service import QuantService

            try:
                qs = QuantService()
                refreshed = qs.refresh_watching(sync_paper=sync_paper_watchlist)
                steps.append(
                    {
                        "name": "watching_refresh",
                        "ok": True,
                        "watchlist_count": (refreshed.get("refresh") or {}).get("count"),
                    }
                )
                if sync_paper_watchlist and refreshed.get("paper_sync"):
                    steps.append(
                        {
                            "name": "sync_paper_watchlist",
                            "ok": True,
                            "holdings_count": refreshed["paper_sync"].get("holdings_count"),
                        }
                    )
            except FileNotFoundError as e:
                msg = str(e)
                steps.append({"name": "watching_refresh", "ok": False, "error": msg, "code": "watching_not_initialized"})
                failures.append(f"watching: {msg}")
            except Exception as e:
                logger.exception('unexpected error in run')
                msg = str(e)
                steps.append({"name": "watching_refresh", "ok": False, "error": msg})
                failures.append(f"watching: {msg}")

        if cross_section:
            from quant.services.quant_service import QuantService

            try:
                ranked = QuantService().run_cross_section(limit=10)
                steps.append(
                    {
                        "name": "cross_section",
                        "ok": bool(ranked.get("success")),
                        "ranked_count": ranked.get("ranked_count"),
                        "error": ranked.get("error"),
                    }
                )
                if not ranked.get("success"):
                    failures.append(f"cross_section: {ranked.get('error')}")
            except Exception as e:
                logger.exception('unexpected error in run')
                msg = str(e)
                steps.append({"name": "cross_section", "ok": False, "error": msg})
                failures.append(f"cross_section: {msg}")

        if paper_rebalance:
            skip_reason = ""
            try:
                from core.paper.rebalance.auto_worker import (
                    already_ran_today,
                    after_auto_rebalance_window,
                    rebalance_window_label,
                )

                if already_ran_today():
                    skip_reason = "今日已开盘调仓"
                elif after_auto_rebalance_window():
                    skip_reason = (
                        f"已过 {rebalance_window_label()} 开盘窗，日更不补跑、不挂开盘单"
                    )
            except Exception:  # noqa: BLE001
                logger.debug("auto-rebalance daily skip check failed", exc_info=True)
                skip_reason = ""
            if skip_reason:
                steps.append(
                    {
                        "name": "paper_rebalance",
                        "ok": True,
                        "skipped": True,
                        "reason": skip_reason,
                    }
                )
            else:
                try:
                    if not self.paper.exists():
                        raise FileNotFoundError(
                            "纸面账户未初始化，请先 init 或 Web「纸面」初始化"
                        )
                    result = self.paper.rebalance()
                    steps.append(
                        {
                            "name": "paper_rebalance",
                            "ok": bool(result.get("success") or result.get("ok")),
                            "mode": result.get("mode"),
                            "top_k": result.get("top_k"),
                            "sell_trades": len(result.get("sell_trades") or []),
                            "buy_trades": len(result.get("buy_trades") or []),
                            "holdings": len(
                                ((result.get("summary") or {}).get("holdings"))
                                or (result.get("holdings") or [])
                            ),
                            "equity": (result.get("summary") or {}).get("equity"),
                            "error": result.get("error"),
                        }
                    )
                    if not (result.get("success") or result.get("ok")):
                        failures.append(f"paper_rebalance: {result.get('error')}")
                except FileNotFoundError as e:
                    msg = str(e)
                    steps.append({"name": "paper_rebalance", "ok": False, "error": msg})
                    failures.append(f"paper_rebalance: {msg}")
                except Exception as e:
                    logger.exception('unexpected error in run')
                    msg = str(e)
                    steps.append({"name": "paper_rebalance", "ok": False, "error": msg})
                    failures.append(f"paper_rebalance: {msg}")

        if paper_run or paper_buy:
            try:
                if not self.paper.exists():
                    raise FileNotFoundError(
                        "纸面账户未初始化，请先在 Web「纸面」中点击「初始化」，"
                        "或执行: python3 research/paper_run.py --init"
                    )
                result = self.paper.run(simulate_buy=bool(paper_buy))
                steps.append(
                    {
                        "name": "paper_buy" if paper_buy else "paper_run",
                        "ok": True,
                        "observation_pool_count": result.get("observation_pool_count"),
                        "new_trades": len(result.get("new_trades") or []),
                        "sell_trades": len(result.get("sell_trades") or []),
                        "equity": (result.get("summary") or {}).get("equity"),
                    }
                )
            except FileNotFoundError as e:
                msg = str(e)
                steps.append(
                    {
                        "name": "paper",
                        "ok": False,
                        "error": msg,
                        "code": "paper_not_initialized",
                    }
                )
                failures.append(f"paper: {msg}")
            except Exception as e:
                logger.exception('unexpected error in run')
                msg = str(e)
                steps.append({"name": "paper", "ok": False, "error": msg, "code": "paper_error"})
                failures.append(f"paper: {msg}")

        if eval_mock:
            report = self.evals.run(use_mock=True, with_agent=False, save=True)
            step = {
                "name": "eval_mock",
                "ok": bool(report.get("ok")),
                "passed": report.get("passed"),
                "failed": report.get("failed"),
                "total": report.get("total"),
            }
            steps.append(step)
            if not report.get("ok"):
                failures.extend(report.get("failures") or [])

        if eval_agent:
            from agent.llm_client import LLMClient

            llm = LLMClient()
            if not llm.api_key or not llm.is_available():
                msg = llm.get_last_error() or "LLM 不可用"
                steps.append({"name": "eval_agent", "ok": False, "error": msg})
                failures.append(f"eval_agent: {msg}")
            else:
                report = self.evals.run(use_mock=True, with_agent=True, save=True)
                step = {
                    "name": "eval_agent",
                    "ok": bool(report.get("ok")),
                    "passed": report.get("passed"),
                    "failed": report.get("failed"),
                    "total": report.get("total"),
                }
                steps.append(step)
                if not report.get("ok"):
                    failures.extend(report.get("failures") or [])

        quant_report_payload: Optional[Dict[str, Any]] = None
        if quant_report:
            from quant.services.quant_service import QuantService

            try:
                qs = QuantService()
                quant_report_payload = qs.build_daily_report(
                    include_cross_section=cross_section,
                    lookback=lookback,
                    fusion_w_co=fusion_w_co,
                    rank_enter=rank_enter,
                    rank_strong=rank_strong,
                )
                path = qs.save_daily_report(quant_report_payload)
                step: Dict[str, Any] = {
                    "name": "quant_report",
                    "ok": True,
                    "path": path,
                    "factor_sample_count": (quant_report_payload.get("factor_ic") or {}).get("sample_count"),
                    "lookback": lookback,
                    "fusion_w_co": fusion_w_co,
                    "rank_enter": rank_enter,
                    "rank_strong": rank_strong,
                }
                if export_quant_report and quant_report_payload:
                    exported = qs.save_report_exports(quant_report_payload)
                    step["export"] = exported
                    if not exported.get("success"):
                        failures.append(f"quant_export: {exported.get('error')}")
                steps.append(step)
            except Exception as e:
                logger.exception('unexpected error in run')
                msg = str(e)
                steps.append({"name": "quant_report", "ok": False, "error": msg})
                failures.append(f"quant_report: {msg}")

        if any([watching_refresh, cross_section, quant_report, sync_paper_watchlist, paper_rebalance]):
            from core.watching.health import check_watching_health

            health = check_watching_health()
            health_step = {
                "name": "watching_health",
                "ok": bool(health.get("success")),
                "watchlist_count": health.get("watchlist_count"),
                "stale": health.get("stale"),
                "warnings": health.get("warnings") or [],
                "issues": health.get("issues") or [],
            }
            steps.append(health_step)
            for issue in health.get("issues") or []:
                failures.append(f"watching_health: {issue}")

        result = {
            "ok": not failures,
            "preset": resolved.get("preset"),
            "steps": steps,
            "failures": failures[:30],
            "eval_ok": any(s.get("name") == "eval_mock" and s.get("ok") for s in steps),
            "paper_ok": any(
                s.get("name") in ("paper_run", "paper_buy") and s.get("ok") for s in steps
            ),
        }
        self._save_last_run(
            {
                "success": True,
                "ok": result["ok"],
                "preset": result.get("preset"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "steps": [
                    {"name": s.get("name"), "ok": s.get("ok"), "error": s.get("error")}
                    for s in steps
                ],
                "failures": failures[:10],
            }
        )
        return result
