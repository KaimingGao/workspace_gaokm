"""平台能力门面：Job / Memory / Decision / Feedback / Schedule / Prefill（D1–D6）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.decision_record import list_decisions, record_from_advice
from core.feedback_suggest import suggest_config_feedback
from core.job_progress import job_registry
from core.memory_store import effective_preferences, read_memory, write_memory
from core.observation import make_observation, wrap_skill_result
from core.order_prefill import prefill_from_recent_decisions
from core.schedule_jobs import run_schedule


class PlatformService:
    def get_job(self, name: str) -> Dict[str, Any]:
        slot = job_registry.slot(name)
        # 轮询路径自动回收卡住的任务（尤其 quant-ols-clusters 拉日线挂死）
        if hasattr(slot, "reclaim_if_stale"):
            slot.reclaim_if_stale()
        return {"ok": True, "job": slot.get()}

    def list_jobs(self) -> Dict[str, Any]:
        jobs = []
        for snap in job_registry.list_jobs():
            name = (snap or {}).get("slot") or (snap or {}).get("name")
            if name:
                slot = job_registry.slot(str(name))
                if hasattr(slot, "reclaim_if_stale"):
                    slot.reclaim_if_stale()
                jobs.append(slot.get())
            else:
                jobs.append(snap)
        return {"ok": True, "jobs": jobs}

    def get_memory(self) -> Dict[str, Any]:
        mem = read_memory()
        return {"ok": True, **mem, "effective": effective_preferences()}

    def get_effective_prefs(self) -> Dict[str, Any]:
        return {"ok": True, "preferences": effective_preferences()}

    def save_memory(self, preferences: Dict[str, Any]) -> Dict[str, Any]:
        return write_memory(preferences)

    def list_decisions(self, *, limit: int = 50) -> Dict[str, Any]:
        return list_decisions(limit=limit)

    def record_advice(
        self,
        advice: Dict[str, Any],
        *,
        source: str = "advise",
        session_id: str = "",
        persist: bool = True,
    ) -> Dict[str, Any]:
        return record_from_advice(
            advice,
            source=source,
            session_id=session_id,
            persist=persist,
        )

    def suggest_feedback(
        self,
        *,
        paper_metrics: Optional[Dict[str, Any]] = None,
        backtest_metrics: Optional[Dict[str, Any]] = None,
        monitor_alerts: Optional[list] = None,
    ) -> Dict[str, Any]:
        return suggest_config_feedback(
            paper_metrics=paper_metrics,
            backtest_metrics=backtest_metrics,
            monitor_alerts=monitor_alerts,
        )

    def run_schedule(self, kind: str, **kwargs: Any) -> Dict[str, Any]:
        return run_schedule(kind, **kwargs)

    def get_schedule_last(self) -> Dict[str, Any]:
        import json
        import os

        from core.paths import SCHEDULE_LAST_RUN_PATH

        if not os.path.isfile(SCHEDULE_LAST_RUN_PATH):
            return {"ok": True, "empty": True, "path": SCHEDULE_LAST_RUN_PATH}
        try:
            with open(SCHEDULE_LAST_RUN_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return {"ok": False, "error": str(e), "path": SCHEDULE_LAST_RUN_PATH}
        return {"ok": True, "empty": False, "path": SCHEDULE_LAST_RUN_PATH, "last": data}

    def order_prefill(self, *, limit: int = 10, fmt: str = "json") -> Dict[str, Any]:
        return prefill_from_recent_decisions(limit=limit, fmt=fmt)

    def observation(self, **kwargs: Any) -> Dict[str, Any]:
        return make_observation(**kwargs)

    def wrap_skill(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return wrap_skill_result(tool, payload)

    def get_north_star(self, *, refresh: bool = True) -> Dict[str, Any]:
        """R0 · 北极星二级指标权威包。"""
        import os

        from core.north_star import build_north_star_report
        from core.paper import load_paper, save_paper
        from core.paths import PAPER_PATH

        paper = None
        if os.path.isfile(PAPER_PATH):
            try:
                paper = load_paper(PAPER_PATH)
            except Exception as e:
                return {"ok": False, "error": str(e)}
        if not refresh and isinstance(paper, dict) and paper.get("last_north_star"):
            return {"ok": True, "cached": True, "north_star": paper["last_north_star"]}
        report = build_north_star_report(paper or {})
        if isinstance(paper, dict):
            paper["last_north_star"] = report
            try:
                save_paper(paper, PAPER_PATH)
            except Exception:
                pass
        return {"ok": True, "cached": False, "north_star": report}

    def get_sample_status(self) -> Dict[str, Any]:
        """样本覆盖：TTM / PIT history / 纸面快照 / 拦截标注。"""
        import os

        from core.paper import load_paper
        from core.paths import PAPER_PATH
        from core.sample_ops import sample_status

        paper = None
        if os.path.isfile(PAPER_PATH):
            try:
                paper = load_paper(PAPER_PATH)
            except Exception:
                paper = None
        return sample_status(paper=paper)

    def get_empty_fundamentals(self) -> Dict[str, Any]:
        from core.validation_universe import empty_fundamentals_report

        return empty_fundamentals_report()

    def get_data_quality(self, *, codes: Optional[list] = None) -> Dict[str, Any]:
        """D4 · 数据质量中心聚合。"""
        from core.data_quality_center import build_data_quality_report

        return build_data_quality_report(codes=codes)

    def run_fundamentals_ingest_nudge(
        self,
        *,
        codes: Optional[list] = None,
        write: bool = True,
        max_points: int = 8,
    ) -> Dict[str, Any]:
        """DC3 · 对 ann_missing TopN（或指定 codes）催办真实财务多期 ingest。"""
        from core.pro_core import ingest_nudge_payload
        from core.sample_ops import ingest_real_fundamentals_history, sample_status

        ss = sample_status(paper=None)
        nudge = ingest_nudge_payload(codes=codes, sample_status=ss)
        target = list(nudge.get("codes") or [])
        if not target:
            return {
                "ok": True,
                "wrote": False,
                "nudge": nudge,
                "message": "无 ann_missing TopN 可催办",
            }
        out = ingest_real_fundamentals_history(
            codes=target,
            max_points=max_points,
            write=bool(write),
        )
        return {
            "ok": True,
            "wrote": bool(write),
            "nudge": nudge,
            "ingest": out,
            "track": "DC3",
        }

    def get_source_audit(self, *, codes: Optional[list] = None, lookback: int = 40) -> Dict[str, Any]:
        """D1 · 独立源审计。"""
        from core.data_consistency import audit_code_sources
        from core.data_coverage import universe_codes

        code_list = [str(c).strip() for c in (codes or []) if str(c).strip()]
        if not code_list:
            code_list = list(universe_codes() or [])[:20]
        return audit_code_sources(code_list, lookback=lookback)

    def get_maturity_gate(self) -> Dict[str, Any]:
        from core.maturity_gate import evaluate_maturity_gate
        from core.sample_ops import sample_status

        ns = self.get_north_star(refresh=False)
        ss = sample_status(paper=None)
        try:
            import os

            from core.paper import load_paper
            from core.paths import PAPER_PATH

            if os.path.isfile(PAPER_PATH):
                ss = sample_status(paper=load_paper(PAPER_PATH))
        except Exception:
            pass
        core = None
        try:
            from evals.core_golden_paths import run_all_core_paths

            core = run_all_core_paths()
        except Exception as e:
            core = {"ok": False, "failures": [str(e)]}
        return evaluate_maturity_gate(
            sample_status=ss,
            north_star=(ns or {}).get("north_star"),
            core_paths=core,
        )

    def export_validation_pack(
        self, *, backtest_result: Optional[dict] = None, note: str = ""
    ) -> Dict[str, Any]:
        from core.signal.config import load_signal_config
        from core.validation_pack import build_validation_pack, render_validation_pack_markdown

        ns = self.get_north_star(refresh=False)
        ss = self.get_sample_status()
        out = build_validation_pack(
            backtest_result=backtest_result,
            signal_config=load_signal_config(),
            north_star=(ns or {}).get("north_star"),
            sample_status=ss,
            note=note,
        )
        out["markdown"] = render_validation_pack_markdown(out)
        return out

    def get_fit_gap(self, *, backtest_result: Optional[dict] = None) -> Dict[str, Any]:
        from core.fit_gap import build_curve_day_diff, fit_gap_hints
        from core.north_star import load_last_backtest_curve

        ns = self.get_north_star(refresh=False)
        bt = backtest_result or {}
        paper_ops: Dict[str, Any] = {}
        paper = None
        try:
            import os

            from core.paper import load_paper
            from core.paths import PAPER_PATH

            if os.path.isfile(PAPER_PATH):
                paper = load_paper(PAPER_PATH)
                paper_ops["cost_model"] = paper.get("cost_model") or "simple_cn"
                opt = paper.get("last_optimize") or {}
                if isinstance(opt, dict) and opt.get("weight_mode"):
                    paper_ops["weight_mode"] = opt.get("weight_mode")
                ops_rep = paper.get("ops_report") or {}
                if isinstance(ops_rep, dict):
                    if ops_rep.get("cost_model"):
                        paper_ops["cost_model"] = ops_rep.get("cost_model")
                    last_opt = ops_rep.get("last_optimize") or {}
                    if isinstance(last_opt, dict) and last_opt.get("weight_mode"):
                        paper_ops["weight_mode"] = last_opt.get("weight_mode")
        except Exception:
            pass

        snaps = list((paper or {}).get("snapshots") or []) if paper else []
        bt_curve: list = []
        bt_meta: Dict[str, Any] = {}
        # 优先用本次回测结果曲线，否则落盘 last curve
        for key in ("equity_curve", "nav_curve", "curve", "portfolio_curve"):
            raw = bt.get(key)
            if isinstance(raw, list) and raw:
                bt_curve = list(raw)
                bt_meta["source"] = f"request.{key}"
                break
        if not bt_curve:
            try:
                pack = load_last_backtest_curve()
                if isinstance(pack, dict):
                    bt_curve = list(pack.get("curve") or pack.get("points") or [])
                    bt_meta = {
                        "source": "last_backtest_curve",
                        "saved_at": pack.get("saved_at") or pack.get("updated_at"),
                        "params": (pack.get("meta") or {}).get("params")
                        if isinstance(pack.get("meta"), dict)
                        else None,
                    }
            except Exception:
                pass

        day_diff = build_curve_day_diff(snaps, bt_curve)
        realization = ((ns or {}).get("north_star") or {}).get("realization")
        out = fit_gap_hints(
            realization=realization,
            cost_compare=bt.get("cost_compare"),
            source_audit=bt.get("source_audit") or bt.get("data_quality"),
            paper_ops=paper_ops or None,
            backtest_params=bt.get("params") or bt.get("request") or bt_meta.get("params") or {},
            universe=bt.get("universe"),
            day_diff=day_diff,
        )
        out["curve_meta"] = bt_meta
        out["day_diff"] = day_diff
        # 前端落差卡要直接展示 Corr/TE（不另打北极星）
        if isinstance(realization, dict):
            out["realization"] = realization
        return out
