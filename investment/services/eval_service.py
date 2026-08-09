"""Golden eval 服务（Web / CLI 共用）。"""

from __future__ import annotations
from core.numbers import now_iso_local as _now_iso

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.paths import EVALS_JOB_PATH, EVALS_LAST_RUN_PATH
from evals.run_checklist import (
    CASES_PATH,
    check_routing_expect,
    evaluate_case_skills,
    filter_quant_cases,
    load_cases,
    run_agent,
    run_skills,
)


class EvalService:
    def __init__(
        self,
        last_run_path: Optional[str] = None,
        job_path: Optional[str] = None,
    ) -> None:
        self.last_run_path = last_run_path or EVALS_LAST_RUN_PATH
        self.job_path = job_path or EVALS_JOB_PATH

    def list_cases(self) -> List[dict]:
        rows = []
        for case in load_cases():
            rows.append(
                {
                    "id": case["id"],
                    "question": case.get("question"),
                    "intent": case.get("intent"),
                    "has_mock": bool(case.get("mock")),
                    "has_routing_expect": bool(case.get("routing_expect")),
                    "agent_must_contain": case.get("agent_must_contain") or [],
                }
            )
        return rows

    def check_presets(self) -> Dict[str, Any]:
        from evals.preset_check import check_daily_presets

        return check_daily_presets()

    def check_readme(self) -> Dict[str, Any]:
        from evals.readme_check import check_readme_coverage

        return check_readme_coverage()

    def list_routing(self) -> Dict[str, Any]:
        from quant.ops.eval_routing_map import build_eval_routing_map

        return build_eval_routing_map()

    def summary(self) -> Dict[str, Any]:
        cases = self.list_cases()
        presets = self.check_presets()
        readme = self.check_readme()
        last = self.load_last_report()
        job = self.get_job()
        last_summary = None
        if last:
            last_summary = {
                "ok": last.get("ok"),
                "passed": last.get("passed"),
                "failed": last.get("failed"),
                "total": last.get("total"),
                "use_mock": last.get("use_mock"),
                "with_agent": last.get("with_agent"),
                "with_presets": last.get("with_presets"),
                "saved_at": last.get("saved_at"),
                "presets_ok": (last.get("presets") or {}).get("ok"),
                "readme_ok": (last.get("readme") or {}).get("ok"),
            }
        return {
            "success": True,
            "case_count": len(cases),
            "quant_case_ids": [c["id"] for c in cases if str(c["id"]).startswith("quant_")],
            "presets": presets,
            "readme": readme,
            "last_run": last_summary,
            "job": job,
            "ci_commands": {
                "local": "bash scripts/ci_quant.sh",
                "github": ".github/workflows/investment-ci.yml (unit · import audit · repro · checklist · preset · daily)",
                "checklist": "python3 evals/run_checklist.py --mock --presets",
                "checklist_quant": "python3 evals/run_checklist.py --mock --quant-only --presets",
                "check_quant_imports": "bash scripts/check_quant_imports.sh",
                "readme_index": "GET /api/readme-index",
                "readme_content": "GET /api/readme?dir=<path>",
                "readme_check": "GET /api/evals/readme · python3 evals/run_readme_check.py",
                "web_quant_ci": "POST /api/evals/run { use_mock, with_presets, quant_only }",
                "agent_weekend": "bash scripts/agent_regression.sh",
                "agent_weekend_quant": "bash scripts/agent_regression_quant.sh",
            },
        }

    def load_last_report(self) -> Optional[Dict[str, Any]]:
        if not os.path.isfile(self.last_run_path):
            return None
        try:
            with open(self.last_run_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def save_last_report(self, report: Dict[str, Any]) -> str:
        payload = dict(report)
        payload["saved_at"] = _now_iso()
        os.makedirs(os.path.dirname(self.last_run_path) or ".", exist_ok=True)
        with open(self.last_run_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return self.last_run_path

    def get_job(self) -> Dict[str, Any]:
        if not os.path.isfile(self.job_path):
            return {"status": "idle"}
        try:
            with open(self.job_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"status": "idle"}
        except (OSError, json.JSONDecodeError):
            return {"status": "idle"}

    def _write_job(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.job_path) or ".", exist_ok=True)
        with open(self.job_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def start_background_run(
        self,
        *,
        case_id: Optional[str] = None,
        use_mock: bool = True,
        with_agent: bool = False,
        with_presets: bool = False,
        quant_only: bool = False,
    ) -> Dict[str, Any]:
        job = self.get_job()
        if job.get("status") == "running":
            return {"ok": False, "error": "已有校验任务在运行", "job": job}
        self._write_job(
            {
                "status": "running",
                "started_at": _now_iso(),
                "case_id": case_id,
                "use_mock": use_mock,
                "with_agent": with_agent,
                "with_presets": with_presets,
                "quant_only": quant_only,
            }
        )
        return {"ok": True, "status": "running"}

    def finish_background_run(self, report: Dict[str, Any]) -> None:
        summary = {
            "status": "done" if report.get("ok") else "failed",
            "finished_at": _now_iso(),
            "ok": report.get("ok"),
            "passed": report.get("passed"),
            "failed": report.get("failed"),
            "total": report.get("total"),
            "failures": (report.get("failures") or [])[:20],
        }
        self._write_job(summary)

    def fail_background_run(self, error: str) -> None:
        self._write_job(
            {
                "status": "failed",
                "finished_at": _now_iso(),
                "error": error,
            }
        )

    def run(
        self,
        *,
        case_id: Optional[str] = None,
        use_mock: bool = True,
        with_agent: bool = False,
        with_presets: bool = False,
        quant_only: bool = False,
        save: bool = True,
    ) -> Dict[str, Any]:
        cases = load_cases()
        if case_id:
            cases = [c for c in cases if c["id"] == case_id]
            if not cases:
                return {
                    "ok": False,
                    "error": f"未找到 case: {case_id}",
                    "cases_path": CASES_PATH,
                }
        if quant_only:
            cases = filter_quant_cases(cases)
            if not cases:
                return {
                    "ok": False,
                    "error": "未找到 quant_* case",
                    "cases_path": CASES_PATH,
                }

        report: Dict[str, Any] = {
            "ok": True,
            "cases_path": CASES_PATH,
            "use_mock": bool(use_mock),
            "with_agent": bool(with_agent),
            "with_presets": bool(with_presets),
            "quant_only": bool(quant_only),
            "cases": [],
            "failures": [],
            "passed": 0,
            "failed": 0,
        }
        all_failures: List[str] = []

        for case in cases:
            cid = case["id"]
            routing_failures = check_routing_expect(case)
            run = run_skills(case, use_mock=use_mock)
            skill_failures, tokens = evaluate_case_skills(case, run)
            case_failures = list(routing_failures) + list(skill_failures)

            entry: Dict[str, Any] = {
                "id": cid,
                "question": case.get("question"),
                "intent": case.get("intent"),
                "ok": not case_failures,
                "routing_failures": routing_failures,
                "skill_failures": skill_failures,
                "used_mock": run.get("used_mock"),
                "skills": [
                    {
                        "name": step["name"],
                        "ok": bool(step["result"].get("success")),
                        "elapsed_sec": step["elapsed_sec"],
                        "error": step["result"].get("error"),
                    }
                    for step in run["skill_runs"]
                ],
            }

            if with_agent:
                agent_res = run_agent(case, tokens)
                entry["agent"] = {
                    k: v
                    for k, v in agent_res.items()
                    if k not in ("reply", "body")
                }
                if agent_res.get("skipped"):
                    entry["agent"]["skipped"] = True
                    if case.get("agent_required"):
                        case_failures.append(
                            f"{cid}/agent: required but skipped ({agent_res.get('reason')})"
                        )
                else:
                    mp = agent_res.get("missing_phrases") or []
                    if mp:
                        case_failures.append(f"{cid}/agent phrases: {mp}")
                    miss = agent_res.get("token_miss") or []
                    if miss:
                        entry["agent"]["number_miss_warning"] = miss

            if case_failures:
                report["failed"] += 1
                all_failures.extend(case_failures)
            else:
                report["passed"] += 1

            entry["failures"] = case_failures
            report["cases"].append(entry)

        if with_presets:
            preset_out = self.check_presets()
            report["presets"] = preset_out
            if not preset_out.get("ok"):
                all_failures.extend(preset_out.get("failures") or [])

            readme_out = self.check_readme()
            report["readme"] = readme_out
            if not readme_out.get("ok"):
                all_failures.extend(readme_out.get("failures") or [])

        report["failures"] = all_failures
        report["ok"] = not all_failures
        report["total"] = len(cases)
        if save:
            self.save_last_report(report)
        return report

    def run_and_finalize_job(
        self,
        *,
        case_id: Optional[str] = None,
        use_mock: bool = True,
        with_agent: bool = False,
        with_presets: bool = False,
        quant_only: bool = False,
    ) -> Dict[str, Any]:
        try:
            report = self.run(
                case_id=case_id,
                use_mock=use_mock,
                with_agent=with_agent,
                with_presets=with_presets,
                quant_only=quant_only,
                save=True,
            )
            self.finish_background_run(report)
            return report
        except Exception as e:
            self.fail_background_run(str(e))
            raise
