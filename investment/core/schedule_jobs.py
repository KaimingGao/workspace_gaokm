"""调度 Job 骨架：异动扫描 / 盘后复盘（写入报告，不推送实盘）。"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from core.job_progress import job_registry
from core.paths import DATA_DIR, SCHEDULE_LAST_RUN_PATH
from core.io_atomic import atomic_write_json


def _write_last_run(payload: Dict[str, Any]) -> str:
    parent = os.path.dirname(SCHEDULE_LAST_RUN_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    atomic_write_json(SCHEDULE_LAST_RUN_PATH, payload)
    return SCHEDULE_LAST_RUN_PATH


def _resolve_warmup_codes(
    codes: Optional[List[str]] = None,
    *,
    cap: int = 20,
    prefer_validation_universe: bool = True,
) -> List[str]:
    """预热默认标的：显式 codes → 验证宇宙 → watching。"""
    if codes:
        out = [str(c).strip() for c in codes if str(c).strip()]
        return out[: max(1, int(cap))]
    if prefer_validation_universe:
        try:
            from core.validation_universe import resolve_validation_codes

            resolved = resolve_validation_codes()
            uni = [str(c).strip() for c in (resolved.get("codes") or []) if str(c).strip()]
            if uni:
                return uni[: max(1, int(cap))]
        except Exception:
            pass
    uni_path = os.path.join(DATA_DIR, "watching.json")
    if os.path.isfile(uni_path):
        try:
            with open(uni_path, encoding="utf-8") as f:
                uni = json.load(f)
            watch = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
            return watch[: max(1, int(cap))]
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return []


def run_watch_alert(*, codes: Optional[List[str]] = None) -> Dict[str, Any]:
    """轻量异动：对观察池/给定代码拉现货涨跌，标记 |涨跌|≥2%。"""
    from core.ports.market import query_quote

    slot = job_registry.slot("schedule")
    if slot.is_running():
        return {"ok": False, "error": "已有调度任务在运行", "job": slot.get()}

    watch = list(codes or [])
    if not watch:
        uni_path = os.path.join(DATA_DIR, "watching.json")
        if os.path.isfile(uni_path):
            with open(uni_path, encoding="utf-8") as f:
                uni = json.load(f)
            watch = list(uni.get("watchlist") or [])[:12]
    if not watch:
        return {"ok": False, "error": "无标的：请传 codes 或配置 watching.json"}

    job_id = slot.start(kind="watch_alert", total=len(watch), message="扫描异动…")
    alerts: List[Dict[str, Any]] = []
    try:
        for i, code in enumerate(watch, start=1):
            slot.update(current=i, message=f"报价 {code}")
            data = query_quote(str(code))
            if not isinstance(data, dict):
                continue
            if not data.get("success"):
                continue
            chg = data.get("change_raw")
            if chg is None:
                chg = data.get("change_percent")
            try:
                chg_f = float(chg) if chg is not None else None
            except (TypeError, ValueError):
                chg_f = None
            if chg_f is not None and abs(chg_f) >= 2.0:
                alerts.append(
                    {
                        "stock_code": data.get("stock_code") or code,
                        "stock_name": data.get("stock_name"),
                        "change_percent": chg_f,
                        "price": data.get("price") or data.get("price_raw"),
                    }
                )
        result = {
            "ok": True,
            "kind": "watch_alert",
            "scanned": len(watch),
            "alert_count": len(alerts),
            "alerts": alerts,
            "note": "仅研究提醒骨架，非推送通道；不代客下单。",
        }
        path = _write_last_run({"ts": time.time(), "job_id": job_id, **result})
        result["path"] = path
        slot.finish(result=result)
        return result
    except Exception as e:
        slot.finish(error=str(e))
        return {"ok": False, "error": str(e), "job": slot.get()}


def run_daily_review(*, limit: int = 5) -> Dict[str, Any]:
    """盘后复盘骨架：汇总最近 DecisionRecord + 纸面摘要（若有）。"""
    from core.decision_record import list_decisions

    slot = job_registry.slot("schedule")
    if slot.is_running():
        return {"ok": False, "error": "已有调度任务在运行", "job": slot.get()}

    job_id = slot.start(kind="daily_review", total=3, message="汇总决策…")
    try:
        slot.update(current=1, message="读取 DecisionRecord")
        decisions = list_decisions(limit=limit)
        paper_summary = None
        slot.update(current=2, message="读取纸面")
        paper_path = os.path.join(DATA_DIR, "paper.json")
        if os.path.isfile(paper_path):
            try:
                from core.paper import load_paper, mark_to_market

                paper_summary = mark_to_market(load_paper(paper_path))
            except Exception as e:
                paper_summary = {"error": str(e)}
        slot.update(current=3, message="写报告")
        result = {
            "ok": True,
            "kind": "daily_review",
            "decisions": decisions.get("items") or [],
            "paper_summary": paper_summary,
            "note": "盘后复盘骨架；归因深化后续迭代。不代客下单。",
        }
        path = _write_last_run({"ts": time.time(), "job_id": job_id, **result})
        result["path"] = path
        slot.finish(result=result)
        return result
    except Exception as e:
        slot.finish(error=str(e))
        return {"ok": False, "error": str(e), "job": slot.get()}


def run_sentiment_scan(*, codes: Optional[List[str]] = None, limit: int = 5) -> Dict[str, Any]:
    """观察名单舆情扫描：强制刷新标题/规则分，写告警到 schedule_last_run。"""
    from core.sentiment import scan_watchlist_sentiment

    slot = job_registry.slot("schedule")
    if slot.is_running():
        return {"ok": False, "error": "已有调度任务在运行", "job": slot.get()}

    watch = _resolve_warmup_codes(codes, cap=20)
    if not watch:
        return {"ok": False, "error": "无标的：请传 codes 或配置 watching / validation_universe"}

    job_id = slot.start(kind="sentiment_scan", total=len(watch), message="扫描舆情…")
    try:
        slot.update(current=0, message="拉取标题与规则情绪…")
        result = scan_watchlist_sentiment(watch, limit=int(limit or 5))
        path = _write_last_run({"ts": time.time(), "job_id": job_id, **result})
        result["path"] = path
        slot.finish(result=result)
        return result
    except Exception as e:
        slot.finish(error=str(e))
        return {"ok": False, "error": str(e), "job": slot.get()}


def run_bars_warmup(
    *,
    codes: Optional[List[str]] = None,
    limit: int = 60,
) -> Dict[str, Any]:
    """预热观察名单日线缓存（N1/M1 批式收集入口；默认增量合并）。"""
    from core.data_coverage import build_data_coverage, coverage_alerts_for_outbound
    from core.data_service import summarize_data_quality
    from core.alert_outbound import dispatch_monitor_alerts

    slot = job_registry.slot("schedule")
    if slot.is_running():
        return {"ok": False, "error": "已有调度任务在运行", "job": slot.get()}

    watch = _resolve_warmup_codes(codes, cap=40)
    if not watch:
        return {"ok": False, "error": "无标的：请传 codes 或配置 watching / validation_universe"}

    job_id = slot.start(kind="bars_warmup", total=len(watch), message="预热日线…")
    try:
        slot.update(current=0, message="拉取并缓存日线")
        summary = summarize_data_quality(watch, limit=int(limit or 60))
        coverage = build_data_coverage(watch)
        outbound = dispatch_monitor_alerts(
            coverage_alerts_for_outbound(coverage),
            source="bars_warmup",
            extra={"coverage": coverage.get("coverage"), "total": coverage.get("total")},
        )
        result = {
            "ok": True,
            "kind": "bars_warmup",
            **summary,
            "coverage": coverage,
            "alert_outbound": outbound,
            "note": "M1 日线预热（增量）；经 DataService；覆盖率已汇总；不代客下单。",
        }
        path = _write_last_run({"ts": time.time(), "job_id": job_id, **result})
        result["path"] = path
        slot.finish(result=result)
        return result
    except Exception as e:
        slot.finish(error=str(e))
        return {"ok": False, "error": str(e), "job": slot.get()}


def run_spot_refresh(*, force: bool = False) -> Dict[str, Any]:
    """刷新 A 股现货磁盘缓存（PE/PB 等列表估值用）。"""
    slot = job_registry.slot("schedule")
    if slot.is_running():
        return {"ok": False, "error": "已有调度任务在运行", "job": slot.get()}

    job_id = slot.start(kind="spot_refresh", total=1, message="刷新现货…")
    try:
        from core.data_service import get_spot

        slot.update(current=1, message="stock_zh_a_spot_em")
        pack = get_spot(force=bool(force))
        result = {
            "ok": True,
            "kind": "spot_refresh",
            "count": pack.get("count") or 0,
            "source": pack.get("data_source"),
            "fetched_at": pack.get("fetched_at"),
            "note": "M1 现货刷新；经 DataService.get_spot；不代客下单。",
        }
        path = _write_last_run({"ts": time.time(), "job_id": job_id, **result})
        result["path"] = path
        slot.finish(result=result)
        return result
    except Exception as e:
        slot.finish(error=str(e))
        return {"ok": False, "error": str(e), "job": slot.get()}


def run_fundamentals_warmup(
    *,
    codes: Optional[List[str]] = None,
    limit: int = 20,
    ingest_history: bool = True,
    ingest_max_points: int = 8,
) -> Dict[str, Any]:
    """预热观察池基本面快照；默认再对 A 股 6 位码入库真实多期 history（C2）。"""
    from core.data_service import get_fundamentals

    slot = job_registry.slot("schedule")
    if slot.is_running():
        return {"ok": False, "error": "已有调度任务在运行", "job": slot.get()}

    watch = _resolve_warmup_codes(codes, cap=max(1, int(limit or 20)))
    if not watch:
        return {"ok": False, "error": "无标的：请传 codes 或配置 watching / validation_universe"}

    job_id = slot.start(kind="fundamentals_warmup", total=len(watch), message="预热基本面…")
    try:
        ok_n = 0
        cache_hits = 0
        errors: List[str] = []
        for i, code in enumerate(watch):
            slot.update(current=i + 1, message=f"fundamentals {code}")
            try:
                pack = get_fundamentals(code, use_cache=True)
                if pack.get("success"):
                    ok_n += 1
                if pack.get("cache_hit"):
                    cache_hits += 1
            except Exception as e:
                errors.append(f"{code}:{e}")

        ingest: Dict[str, Any] = {"ok": False, "skipped": True}
        if ingest_history:
            slot.update(message="ingest real fundamentals history…")
            try:
                from core.sample_ops import ingest_real_fundamentals_history

                cn_codes = []
                for c in watch:
                    digits = "".join(ch for ch in str(c) if ch.isdigit())
                    if len(digits) == 6:
                        cn_codes.append(str(c).strip())
                if cn_codes:
                    ingest = ingest_real_fundamentals_history(
                        codes=cn_codes,
                        max_points=int(ingest_max_points or 8),
                        drop_synthetic=True,
                        write=True,
                    )
                else:
                    ingest = {
                        "ok": True,
                        "updated_count": 0,
                        "note": "无 A 股 6 位码，跳过 ingest-history",
                    }
            except Exception as e:
                ingest = {"ok": False, "error": str(e)}
                errors.append(f"ingest_history:{e}")

        result = {
            "ok": True,
            "kind": "fundamentals_warmup",
            "count": len(watch),
            "success_count": ok_n,
            "cache_hits": cache_hits,
            "ingest_history": ingest,
            "errors": errors[:10],
            "note": "M1.3 快照预热 + C2 真实多期 history（A 股）；non_pit 快照仍保留；不代客下单。",
        }
        path = _write_last_run({"ts": time.time(), "job_id": job_id, **result})
        result["path"] = path
        slot.finish(result=result)
        return result
    except Exception as e:
        slot.finish(error=str(e))
        return {"ok": False, "error": str(e), "job": slot.get()}


def run_paper_daily(
    *,
    simulate_buy: bool = False,
    strategy: str = "short",
) -> Dict[str, Any]:
    """准实盘日更（N5）：跑纸面日循环 + 决策记录摘要 + 衰减监控。"""
    slot = job_registry.slot("schedule")
    if slot.is_running():
        return {"ok": False, "error": "已有调度任务在运行", "job": slot.get()}

    job_id = slot.start(kind="paper_daily", total=3, message="纸面日更…")
    try:
        from core.paper import load_paper, mark_to_market, run_daily_cycle, save_paper
        from core.decision_record import append_decision
        from core.strategy_monitor import assess_strategy_health
        from core.paths import PAPER_PATH

        slot.update(current=1, message="cluster_prepare + run_daily_cycle")
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
        paper = load_paper(PAPER_PATH)
        cycle = run_daily_cycle(
            paper,
            simulate_buy=bool(simulate_buy),
            strategy=str(strategy or "short"),
        )
        save_paper(paper, PAPER_PATH)
        summary = mark_to_market(paper)

        slot.update(current=2, message="记录决策摘要")
        import uuid

        ops = (cycle or {}).get("ops_report") or {}
        append_decision(
            {
                "id": uuid.uuid4().hex[:12],
                "ts": time.time(),
                "source": "paper_daily",
                "strategy": strategy,
                "strategy_id": ops.get("strategy_id") or (cycle or {}).get("strategy_id") or strategy,
                "strategy_version": ops.get("strategy_version")
                or (cycle or {}).get("strategy_version"),
                "cost_model": ops.get("cost_model") or (cycle or {}).get("cost_model"),
                "data_quality": ops.get("data_quality")
                if ops.get("data_quality") is not None
                else (cycle or {}).get("data_quality"),
                "risk_blocks": ops.get("risk_blocks")
                if ops.get("risk_blocks") is not None
                else (cycle or {}).get("risk_blocks")
                or ((cycle or {}).get("risk_gate") or {}).get("blocks")
                or [],
                "monitor_alerts": ops.get("monitor_alerts")
                if ops.get("monitor_alerts") is not None
                else (cycle or {}).get("monitor_alerts")
                or [],
                "buys_blocked": bool(
                    ops.get("buys_blocked")
                    if ops.get("buys_blocked") is not None
                    else (cycle or {}).get("buys_blocked")
                ),
                "simulate_buy": bool(simulate_buy),
                "summary": {
                    "equity": summary.get("equity"),
                    "max_drawdown_pct": summary.get("max_drawdown_pct"),
                    "holdings": len(summary.get("holdings") or []),
                },
                "north_star": (ops.get("north_star") or paper.get("last_north_star")),
                "risk_gate": (cycle or {}).get("risk_gate"),
                "note": "N5 纸面日更；五问字段可审计；告警不自动改权。",
            }
        )

        slot.update(current=3, message="衰减监控 + score ledger")
        health = (cycle or {}).get("health")
        if not health:
            health = assess_strategy_health(
                paper,
                summary=summary,
                compute_rolling_ic=True,
            )

        # 复盘账本：冻结今日 ŷ + 回填到期决策日 realized
        score_ledger = None
        try:
            from core.score_ledger import run_score_ledger_daily

            score_ledger = run_score_ledger_daily()
        except Exception as exc:
            score_ledger = {"success": False, "error": str(exc)}

        # R0：确保日更结果含权威北极星包（cycle 内已算则复用）
        north_star = paper.get("last_north_star") or (ops or {}).get("north_star")
        if not north_star:
            try:
                from core.north_star import build_north_star_report

                north_star = build_north_star_report(paper)
                paper["last_north_star"] = north_star
                save_paper(paper, PAPER_PATH)
            except Exception:
                north_star = None

        monitor_alerts = (
            (cycle or {}).get("monitor_alerts")
            or (health or {}).get("alerts")
            or []
        )
        from core.alert_outbound import dispatch_monitor_alerts
        from core.data_coverage import build_data_coverage, coverage_alerts_for_outbound

        coverage = build_data_coverage()
        cov_alerts = coverage_alerts_for_outbound(coverage)
        merged_alerts = list(monitor_alerts) + list(cov_alerts)

        alert_outbound = dispatch_monitor_alerts(
            merged_alerts,
            source="paper_daily",
            extra={
                "strategy": strategy,
                "equity": (summary or {}).get("equity"),
                "bars_coverage": coverage.get("coverage"),
            },
        )
        result = {
            "ok": True,
            "kind": "paper_daily",
            "simulate_buy": bool(simulate_buy),
            "strategy": strategy,
            "cluster_prepare": cluster_prep,
            "cycle": cycle,
            "summary": summary,
            "health": health,
            "score_ledger": score_ledger,
            "ops_report": ops or (cycle or {}).get("ops_report"),
            "north_star": north_star,
            "strategy_id": (cycle or {}).get("strategy_id") or strategy,
            "strategy_version": (cycle or {}).get("strategy_version"),
            "cost_model": (cycle or {}).get("cost_model"),
            "data_quality": (cycle or {}).get("data_quality") or {},
            "data_coverage": coverage,
            "risk_blocks": (cycle or {}).get("risk_blocks") or [],
            "monitor_alerts": merged_alerts,
            "alert_outbound": alert_outbound,
            "buys_blocked": bool((cycle or {}).get("buys_blocked")),
            "note": "N5 准实盘日更；含日线覆盖与北极星 KPI；账本冻/回填；告警不自动改权；不代客下单。",
        }
        path = _write_last_run({"ts": time.time(), "job_id": job_id, **result})
        result["path"] = path
        slot.finish(result=result)
        return result
    except Exception as e:
        slot.finish(error=str(e))
        return {"ok": False, "error": str(e), "job": slot.get()}


def run_validation_prepare(
    *,
    codes: Optional[List[str]] = None,
    write_excludes: bool = False,
    warmup_bars: bool = True,
    warmup_sentiment: bool = True,
    limit: int = 60,
) -> Dict[str, Any]:
    """验证宇宙一键准备：卫生报告 + 可选 exclude + 日线/舆情预热。"""
    slot = job_registry.slot("schedule")
    if slot.is_running():
        return {"ok": False, "error": "已有调度任务在运行", "job": slot.get()}

    job_id = slot.start(kind="validation_prepare", total=1, message="验证宇宙准备…")
    try:
        from core.validation_universe import prepare_validation_universe

        slot.update(current=1, message="hygiene + warmup")
        result = prepare_validation_universe(
            write_excludes=bool(write_excludes),
            warmup_bars=bool(warmup_bars),
            warmup_sentiment=bool(warmup_sentiment),
            bars_limit=int(limit or 60),
            codes=codes,
        )
        result = {**result, "kind": "validation_prepare"}
        path = _write_last_run({"ts": time.time(), "job_id": job_id, **result})
        result["path"] = path
        slot.finish(result=result)
        return result
    except Exception as e:
        slot.finish(error=str(e))
        return {"ok": False, "error": str(e), "job": slot.get()}


def run_schedule(kind: str, **kwargs: Any) -> Dict[str, Any]:
    k = (kind or "").strip()
    if k == "watch_alert":
        return run_watch_alert(codes=kwargs.get("codes"))
    if k == "daily_review":
        return run_daily_review(limit=int(kwargs.get("limit") or 5))
    if k == "sentiment_scan":
        return run_sentiment_scan(codes=kwargs.get("codes"), limit=int(kwargs.get("limit") or 5))
    if k == "bars_warmup":
        return run_bars_warmup(codes=kwargs.get("codes"), limit=int(kwargs.get("limit") or 60))
    if k == "spot_refresh":
        return run_spot_refresh(force=bool(kwargs.get("force")))
    if k == "fundamentals_warmup":
        ingest = kwargs.get("ingest_history")
        if ingest is None:
            ingest = True
        return run_fundamentals_warmup(
            codes=kwargs.get("codes"),
            limit=int(kwargs.get("limit") or 20),
            ingest_history=bool(ingest),
            ingest_max_points=int(kwargs.get("ingest_max_points") or 8),
        )
    if k == "paper_daily":
        return run_paper_daily(
            simulate_buy=bool(kwargs.get("simulate_buy")),
            strategy=str(kwargs.get("strategy") or "short"),
        )
    if k == "validation_prepare":
        return run_validation_prepare(
            codes=kwargs.get("codes"),
            write_excludes=bool(kwargs.get("write_excludes")),
            warmup_bars=kwargs.get("warmup_bars", True),
            warmup_sentiment=kwargs.get("warmup_sentiment", True),
            limit=int(kwargs.get("limit") or 60),
        )
    return {"ok": False, "error": f"unknown schedule kind: {kind}"}
