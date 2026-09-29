"""QuantService · 因子面板 / IC / OLS / 权重与阈值（进阶）；``run_cross_section`` 属 ② 回溯。"""


import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.watching.store import WATCHING_MAX_SIZE

logger = logging.getLogger(__name__)


def _tau_ridge_desk_key(doc: Optional[Dict[str, Any]]) -> tuple:
    """研究台身份：τ / 标签 / 面板 n + Holdout。Holdout 变了必须展示最近拟合。"""
    return _ridge_desk_key(doc)


def _ridge_desk_key(doc: Optional[Dict[str, Any]]) -> tuple:
    """研究台身份：标签 / 面板 n + Holdout。Holdout 变了必须展示最近拟合。"""
    if not isinstance(doc, dict):
        return ("", "", None, None, None)
    oos = doc.get("oos") if isinstance(doc.get("oos"), dict) else {}
    hold = doc.get("holdout_trading_days")
    if hold is None:
        hold = oos.get("holdout_trading_days")
    try:
        hold_n = int(hold) if hold is not None else None
    except (TypeError, ValueError):
        hold_n = hold
    return (
        str(doc.get("tau") or doc.get("target") or ""),
        str(doc.get("target") or ""),
        doc.get("sample_count"),
        hold_n,
        oos.get("n_test"),
    )


def _select_ridge_desk_doc(
    live: Optional[Dict[str, Any]],
    last: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """last 与 live 的 Holdout/n 不一致时，研究台展示最近拟合。"""
    last_ok = isinstance(last, dict) and isinstance(last.get("return_model"), dict)
    live_ok = isinstance(live, dict) and isinstance(live.get("return_model"), dict)
    if last_ok:
        if not live_ok:
            return last, True
        if _ridge_desk_key(last) != _ridge_desk_key(live):
            return last, True
    if live_ok:
        return live, False
    return None, False


def _last_report_path_from_live(live_path: str) -> str:
    """``tc_ridge_model.json`` → ``tc_ridge_last_report.json``。"""
    p = str(live_path or "")
    if p.endswith("_model.json"):
        return p[: -len("_model.json")] + "_last_report.json"
    return ""


def _file_mtime_iso(path: str) -> Optional[str]:
    try:
        if not path or not os.path.isfile(path):
            return None
        from datetime import datetime, timezone

        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        return None


def _attach_ridge_role_flags(
    out: Dict[str, Any],
    live_path: str,
    *,
    live_present: Optional[bool] = None,
) -> Dict[str, Any]:
    """给 Ridge GET/POST 打上执行套 / 研究套是否已落盘。"""
    from core.research.holdout import research_sidecar_flags

    flags = research_sidecar_flags(live_path)
    out["research_exists"] = bool(flags.get("research_exists"))
    out["research_path"] = flags.get("research_path")
    out["research_promoted_at"] = flags.get("research_promoted_at")
    if live_present is None:
        live_present = bool(live_path and os.path.isfile(live_path))
    out["live_model_present"] = bool(live_present)
    fitted = out.get("fitted_at")
    if not fitted:
        fitted = _file_mtime_iso(_last_report_path_from_live(live_path))
    if not fitted:
        fitted = out.get("promoted_at") or flags.get("research_promoted_at")
    if fitted:
        out["fitted_at"] = fitted
    return out


def _start_ridge_fit_job(
    *,
    slot: Any,
    kind: str,
    message: str,
    worker_fn: Any,
    watching_limit: int = WATCHING_MAX_SIZE,
) -> Dict[str, Any]:
    """Ridge 拟合入队：立刻返回 ``background=true``，心跳防 5min 误杀。"""
    import threading

    slot.reclaim_if_stale()
    if slot.is_running():
        return {
            "ok": True,
            "success": True,
            "background": True,
            "reused": True,
            "job": slot.get(),
        }

    try:
        from core.watching.store import read_watching

        n_watch_all = len(list((read_watching() or {}).get("watchlist") or []))
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        n_watch_all = watching_limit
    cap = max(1, int(watching_limit or WATCHING_MAX_SIZE))
    n_watch = min(n_watch_all, cap) if n_watch_all else cap
    job_total = max(1, n_watch)
    job_id = slot.start(kind=kind, total=job_total, message=message)

    def _worker() -> None:
        stop_hb = threading.Event()

        def _heartbeat() -> None:
            while not stop_hb.wait(8.0):
                if not slot.touch(job_id=job_id):
                    return

        hb = threading.Thread(target=_heartbeat, name=f"{kind}-hb-{job_id}", daemon=True)
        hb.start()
        try:
            if slot.is_cancel_requested():
                slot.finish(error="已取消", job_id=job_id)
                return
            result = worker_fn()
            if slot.is_cancel_requested():
                slot.finish(error="已取消", job_id=job_id)
                return
            if not isinstance(result, dict):
                slot.finish(error="拟合无返回", job_id=job_id)
                return
            if not result.get("success"):
                slot.finish(
                    error=str(result.get("error") or "拟合失败"),
                    result=result,
                    job_id=job_id,
                )
                return
            slot.update(
                current=job_total,
                total=job_total,
                message="完成",
                job_id=job_id,
            )
            slot.finish(result=result, job_id=job_id)
        except Exception as e:
            logger.exception("unexpected error in %s worker", kind)
            slot.finish(error=str(e), job_id=job_id)
        finally:
            stop_hb.set()

    threading.Thread(target=_worker, name=f"{kind}-{job_id}", daemon=True).start()
    return {
        "ok": True,
        "success": True,
        "background": True,
        "job": slot.get(),
    }


# ============================================================
# 跑分组阶段编号（共 13 主阶段）
# 前端状态示例：「分组中… 358s · [2/13] 拉日线 0/100（强制刷新过期票） · 1%」
# 对应规则：regex 关键词 → (stage_idx, total_stages)
# ============================================================
_CLUSTER_STAGE_RULES: "List[Tuple[str, int, int]]" = [
    # 0：排队中 / 启动 / 缓存命中
    ("排队中", 0, 13),
    ("启动中", 0, 13),
    (r"命中.*缓存", 0, 13),
    # 1：合并宇宙
    ("合并宇宙", 1, 13),
    # 2：拉日线 / 拉指数（并行子步归同一阶段号）
    ("拉日线", 2, 13),
    ("拉指数", 2, 13),
    # 3：单票拟合
    ("拟合", 3, 13),
    # 4：聚类定组 / auto-k 选区
    ("选区", 4, 13),
    ("聚类", 4, 13),
    # 5：组池 OLS
    ("组池 OLS", 5, 13),
    # 6：贪心换组
    ("贪心换组", 6, 13),
    # 7：扩展窗审计
    ("扩展窗审计", 7, 13),
    # 8：OOS / 分池（含 attach_cluster_oos_gates & 逐组 OOS 循环）
    (r"^OOS", 8, 13),
    ("OOS / 分池", 8, 13),
    # 9：组内打分
    ("组内打分", 9, 13),
    # 10：分池合成
    ("分池合成", 10, 13),
    # 11：分池产物
    ("分池产物", 11, 13),
    # 12：多权打分
    ("多权打分", 12, 13),
    # 13：收尾
    ("收尾", 13, 13),
]


def _with_stage_prefix(msg: str) -> str:
    """为跑分组 progress 消息加 [N/13] 前缀；已编号则跳过。"""
    if not msg:
        return msg
    if re.match(r"\s*\[\s*\d+\s*/\s*\d+\s*\]", str(msg)):
        return msg
    text = str(msg).lstrip()
    for pattern, n, tot in _CLUSTER_STAGE_RULES:
        try:
            if re.search(pattern, text):
                return f"[{n}/{tot}] {msg.lstrip()}"
        except re.error:
            continue
    return f"[-/13] {msg.lstrip()}"


class QuantFactorMixin:
    def list_factors(self) -> Dict[str, Any]:
        from core.signal.factors.meta.panel import build_factor_panel

        return build_factor_panel()

    def build_factor_panel(
        self,
        code: str = "茅台",
        *,
        lookback: int = 120,
        horizon_days: int = 3,
        with_experiment: bool = False,
    ) -> Dict[str, Any]:
        from core.signal.factors.meta.panel import build_factor_panel

        if not with_experiment:
            return build_factor_panel()

        exp = self.run_factor_experiment(code, lookback=lookback, horizon_days=horizon_days)
        if not exp.get("success"):
            return exp
        panel = build_factor_panel(
            experiment=exp,
            stock_code=exp.get("stock_code") or code,
            horizon_days=exp.get("horizon_days") or horizon_days,
            data_source=exp.get("data_source"),
        )
        panel["experiment"] = {
            "horizon_days": exp.get("horizon_days"),
            "factor_count": exp.get("factor_count"),
        }
        return panel

    def run_cross_section(
        self,
        *,
        codes: Optional[List[str]] = None,
        limit: int = 10,
        min_score: Optional[float] = None,
        horizon_days: int = 3,
    ) -> Dict[str, Any]:
        from core.signal.service import get_default_signal_service

        return get_default_signal_service().rank_cross_section(
            codes,
            horizon_days=horizon_days,
            limit=limit,
            min_score=min_score,
        ).as_dict()

    def run_factor_report(
        self,
        code: str = "茅台",
        *,
        lookback: int = 120,
    ) -> Dict[str, Any]:
        from core.data.facade import bars_and_source_research as bars_and_source
        from core.data.facade import get_quote, index_bars_and_source
        from core.ports.market import default_benchmark, resolve_market_code
        from quant.research.factor_report import compute_factor_ic_report

        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        bars, src = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = bars_and_source(sym, limit=lookback + 35)
        if not bars:
            return {"success": False, "error": f"无法获取 {code} 日线"}

        market, _ = resolve_market_code(code)
        index_bars, _ = index_bars_and_source(default_benchmark(market), limit=lookback + 35)
        fundamentals = self._experiment_fundamentals(code, sym)
        report = compute_factor_ic_report(
            bars,
            index_bars=index_bars or None,
            fundamentals=fundamentals,
            stock_code=str(sym or code),
            pit_fundamentals=True,
        )
        report["stock_code"] = sym
        report["data_source"] = src
        return report

    def _experiment_fundamentals(self, code: str, sym: Optional[str] = None) -> Optional[dict]:
        from core.signal.config import load_signal_config
        from core.signal.fundamentals_bridge import fetch_score_fundamentals

        cfg = load_signal_config()
        fund_cfg = cfg.get("fundamentals") or {}
        if not fund_cfg.get("enabled", True):
            return None
        if not fund_cfg.get("use_in_ic_experiment", True):
            return None
        return fetch_score_fundamentals(code) or (
            fetch_score_fundamentals(sym) if sym else None
        )

    def run_factor_experiment(
        self,
        code: str = "茅台",
        *,
        lookback: int = 120,
        horizon_days: int = 3,
    ) -> Dict[str, Any]:
        from core.data.facade import bars_and_source_research as bars_and_source
        from core.data.facade import get_quote, index_bars_and_source
        from core.ports.market import default_benchmark, resolve_market_code
        from core.signal.factors.meta.registry import run_factor_experiment

        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        bars, src = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = bars_and_source(sym, limit=lookback + 35)
        if not bars:
            return {"success": False, "error": f"无法获取 {code} 日线"}

        market, _ = resolve_market_code(code)
        index_bars, _ = index_bars_and_source(default_benchmark(market), limit=lookback + 35)
        fundamentals = self._experiment_fundamentals(code, sym)
        report = run_factor_experiment(
            bars,
            horizon_days=horizon_days,
            index_bars=index_bars or None,
            fundamentals=fundamentals,
            stock_code=str(sym or code),
            pit_fundamentals=True,
        )
        report["stock_code"] = sym
        report["data_source"] = src
        from core.signal.factors.meta.panel import build_factor_panel

        report["panel"] = build_factor_panel(
            experiment=report,
            stock_code=sym,
            horizon_days=report.get("horizon_days") or horizon_days,
            data_source=src,
        )
        return report

    def run_factor_ols_experiment(
        self,
        code: str = "茅台",
        *,
        lookback: int = 120,
        horizon_days: int = 3,
        ridge_lambda: float = 0.0,
    ) -> Dict[str, Any]:
        from core.data.facade import bars_and_source_research as bars_and_source
        from core.data.facade import get_quote, index_bars_and_source
        from core.ports.market import default_benchmark, resolve_market_code
        from quant.research.factor_ols import compute_factor_ols_report

        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        bars, src = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = bars_and_source(sym, limit=lookback + 35)
        if not bars:
            return {"success": False, "error": f"无法获取 {code} 日线", "task": "factor_ols"}

        market, _ = resolve_market_code(code)
        index_bars, _ = index_bars_and_source(default_benchmark(market), limit=lookback + 35)
        fundamentals = self._experiment_fundamentals(code, sym)
        report = compute_factor_ols_report(
            bars,
            horizon_days=horizon_days,
            index_bars=index_bars or None,
            fundamentals=fundamentals,
            stock_code=str(sym or code),
            pit_fundamentals=True,
            ridge_lambda=ridge_lambda,
        )
        report["stock_code"] = sym
        report["data_source"] = src
        report["task"] = "factor_ols"
        return report

    def run_yhat_residual_shadow(
        self,
        *,
        watching_limit: int = 36,
        top_k: int = 10,
        prefer_cluster_book: bool = False,
    ) -> Dict[str, Any]:
        """ŷ 行业残差 on/off 影子对照（不写盘）。分池簿已停用。"""
        from core.research.yhat_residual_shadow import compare_yhat_residual_shadow
        from core.watching.store import read_watching

        _ = prefer_cluster_book
        items: List[Dict[str, Any]] = []
        source = "none"
        uni = read_watching()
        codes = list(uni.get("watchlist") or [])[
            : max(3, min(int(watching_limit or 36), 40))
        ]
        try:
            from core.signal.service import get_default_signal_service

            svc = get_default_signal_service()
            for code in codes:
                try:
                    packed = svc.score_one(str(code)).as_dict()
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
                    continue
                if not isinstance(packed, dict):
                    continue
                if packed.get("predicted_score") is None and packed.get("score") is None:
                    continue
                items.append(packed)
            source = "live_score"
        except Exception as exc:
            logger.exception('unexpected error in run_yhat_residual_shadow')
            return {
                "success": False,
                "ok": False,
                "error": f"无法打分：{exc}",
                "task": "yhat_residual_shadow",
            }

        if len(items) < 3:
            return {
                "success": False,
                "ok": False,
                "error": "至少 3 只有 ŷ/score 的条目才能对照",
                "task": "yhat_residual_shadow",
                "sample_count": len(items),
                "source": source,
            }

        out = compare_yhat_residual_shadow(items, top_k=top_k)
        out["source"] = source
        out["watching_limit"] = watching_limit
        return out

    def run_excess_mode_shadow(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = 36,
        horizon_days: int = 1,
        ridge_lambda: float = 1.0,
    ) -> Dict[str, Any]:
        """绝对 y vs 指数超额 y：同池 holdout IC 影子对照（不写盘）。"""
        from core.data.facade import bars_and_source
        from core.research.excess_mode_shadow import compare_excess_mode_shadow
        from core.watching.store import read_watching

        uni = read_watching()
        codes = list(uni.get("watchlist") or [])
        limit = max(2, min(int(watching_limit or 36), 40))
        codes = codes[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "ok": False,
                "error": "研究池至少 2 只",
                "task": "excess_mode_shadow",
            }
        stock_bars: List[Dict[str, Any]] = []
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            stock_bars.append({"code": str(code), "bars": bars})
        out = compare_excess_mode_shadow(
            stock_bars,
            horizon_days=horizon_days,
            ridge_lambda=ridge_lambda,
        )
        out["watching_limit"] = limit
        out["lookback"] = lookback
        out["stock_count"] = len(stock_bars)
        return out

    def run_tau_ridge_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        persist: bool = False,
        note: str = "",
        tau_hm: Optional[str] = None,
        force_promote: bool = False,
        persist_role: str = "live",
        holdout_trading_days: int = 20,
        include_alpha158: bool = True,
    ) -> Dict[str, Any]:
        """R0：观察池 ŷ_τ 头 Ridge；可选 persist live 模型。

        默认用满观察池。``tau_hm`` 缺省跟随 ``dual_score.enable_minute_tau``：
        开则训 09:30…做 T 11:00 网格，否则 open。
        ``include_alpha158``：默认 True，Ridge 吃 ``raw_alpha158_*``（≤T−1）。
        """
        from core.data.facade import bars_and_source
        from core.signal.dual_score import get_dual_score_cfg
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.tau_ridge import (
            fit_tau_ridge_report,
            load_tau_last_report,
            load_tau_model,
            persist_tau_model,
            save_tau_last_report,
            tau_model_path,
            tau_promote_gate,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ Ridge",
                "task": "tau_ridge",
            }

        if persist:
            last = load_tau_last_report()
            if last:
                saved = persist_tau_model(
                    last,
                    note=note or "persist last tau report",
                    force=bool(force_promote),
                    role=persist_role,
                )
                out = dict(last)
                out["persisted"] = saved
                out["from_last_report"] = True
                out["promote_gate"] = saved.get("promote_gate") or tau_promote_gate(last)
                if saved.get("promoted_at"):
                    out["promoted_at"] = saved["promoted_at"]
                return _attach_ridge_role_flags(
                    out, tau_model_path(), live_present=bool(load_tau_model())
                )

        ds = get_dual_score_cfg()
        from core.signal.minute_tau_grid import DEFAULT_MINUTE_TAU_GRID, TRAIN_TAU_END_HM

        if tau_hm is None:
            use_minute = bool(ds.get("enable_minute_tau"))
        else:
            tau_key_raw = str(tau_hm or "open").strip() or "open"
            use_minute = tau_key_raw.lower() not in ("", "open")
        tau_key = TRAIN_TAU_END_HM if use_minute else "open"
        tau_grid = list(DEFAULT_MINUTE_TAU_GRID) if use_minute else None

        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        # Alpha158 需 ≥62 日 hist；lookback 默认 120，再多留缓冲
        bar_limit = int(lookback) + 40
        if include_alpha158:
            from core.signal.factors.alpha158 import ALPHA158_PANEL_WINDOW

            bar_limit = max(bar_limit, int(ALPHA158_PANEL_WINDOW) + int(holdout_trading_days or 20) + 20)
        for code in codes:
            bars, _src = bars_and_source(code, limit=bar_limit)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            if use_minute:
                try:
                    from core.ports.market import resolve_market_code
                    from core.store import load_minute_cache

                    mkt, pure = resolve_market_code(str(code))
                    packed = load_minute_cache(
                        mkt or "CN",
                        pure or str(code),
                        "5",
                        min_bars=1,
                        ignore_age=True,
                    )
                    if packed:
                        mb, _meta = packed
                        if mb:
                            row["minute_bars"] = mb
                            minute_hit += 1
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
                    pass
            stock_bars.append(row)
        report = fit_tau_ridge_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            tau_hm=tau_key,
            tau_grid=tau_grid,
            holdout_trading_days=holdout_trading_days,
            include_alpha158=bool(include_alpha158),
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_cache_hit"] = minute_hit if use_minute else None
        report["minute_cache_universe"] = len(codes) if use_minute else None
        if report.get("success"):
            save_tau_last_report(report)
            if not report.get("promote_gate"):
                report["promote_gate"] = tau_promote_gate(report)
        if persist and report.get("success"):
            saved = persist_tau_model(
                report,
                note=note or "api tau-ridge persist",
                force=bool(force_promote),
                role=persist_role,
            )
            report["persisted"] = saved
            report["promote_gate"] = saved.get("promote_gate") or tau_promote_gate(report)
            if saved.get("promoted_at"):
                report["promoted_at"] = saved["promoted_at"]
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_tau_model()
            report["live_model_present"] = bool(live)
            if report.get("success") and not report.get("promote_gate"):
                report["promote_gate"] = tau_promote_gate(report)
        _attach_ridge_role_flags(
            report, tau_model_path(), live_present=bool(load_tau_model())
        )
        return report

    def get_tau_ridge_model(self) -> Dict[str, Any]:
        from core.research.tau_ridge import (
            load_tau_last_report,
            load_tau_model,
            tau_model_path,
            tau_promote_gate,
        )

        live = load_tau_model()
        last = load_tau_last_report()
        if not live and not last:
            return _attach_ridge_role_flags(
                {
                    "success": False,
                    "exists": False,
                    "path": tau_model_path(),
                    "last_report_exists": False,
                    "note": "尚无 ŷ_τ 模型；POST /api/quant/tau-ridge persist=true",
                },
                tau_model_path(),
                live_present=False,
            )

        _doc, use_last = _select_ridge_desk_doc(live, last)
        if use_last and last:
            out = dict(last)
            out.update(
                {
                    "success": True,
                    "exists": True,
                    "path": tau_model_path(),
                    "promoted": False,
                    "shadow": True,
                    "last_report_exists": True,
                    "live_model_present": bool(live),
                    "live_tau": (live or {}).get("tau") if live else None,
                    "promote_gate": tau_promote_gate(last),
                }
            )
            return _attach_ridge_role_flags(
                out, tau_model_path(), live_present=bool(live)
            )

        if not live:
            return _attach_ridge_role_flags(
                {
                    "success": False,
                    "exists": False,
                    "path": tau_model_path(),
                    "last_report_exists": bool(last),
                    "note": "尚无 ŷ_τ 模型；POST /api/quant/tau-ridge persist=true",
                },
                tau_model_path(),
                live_present=False,
            )

        return _attach_ridge_role_flags(
            {
                "success": True,
                "exists": True,
                "path": tau_model_path(),
                "promoted": True,
                "shadow": False,
                "last_report_exists": bool(last),
                "live_model_present": True,
                "promote_gate": tau_promote_gate(
                    live if live.get("oos") else (last or live)
                ),
                **live,
            },
            tau_model_path(),
            live_present=True,
        )

    def run_oo_tree_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        horizon_days: int = 1,
        ridge_lambda: float = 1.0,
        holdout_trading_days: int = 20,
        backend: Optional[str] = None,
        include_alpha158: bool = True,
    ) -> Dict[str, Any]:
        """ŷ_oo_tree 影子头：日线面板 Holdout vs Ridge。只写 last_report。"""
        import time

        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.oo_tree import (
            fit_oo_tree_report,
            save_oo_tree_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_oo_tree",
                "task": "oo_tree",
                "head": "y_oo_tree",
                "live_hook": False,
                "backtest_hook": False,
            }

        stock_bars: List[Dict[str, Any]] = []
        t_bars0 = time.perf_counter()
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            stock_bars.append({"code": str(code), "bars": bars})
        bars_s = round(time.perf_counter() - t_bars0, 2)
        report = fit_oo_tree_report(
            stock_bars,
            horizon_days=horizon_days,
            ridge_lambda=ridge_lambda,
            holdout_trading_days=holdout_trading_days,
            backend=backend,
            include_alpha158=include_alpha158,
        )
        if isinstance(report, dict):
            timing = dict(report.get("timing") or {})
            timing["bars_s"] = bars_s
            report["timing"] = timing
            report["watching_limit"] = limit
            report["codes"] = [r.get("code") for r in stock_bars]
            if report.get("success"):
                save_oo_tree_last_report(report)
        return report

    def get_oo_tree_last_report(self) -> Dict[str, Any]:
        from core.research.oo_tree import (
            load_oo_tree_last_report,
            oo_tree_last_report_path,
        )

        last = load_oo_tree_last_report()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": oo_tree_last_report_path(),
                "note": "尚无 ŷ_oo_tree 影子报告",
                "head": "y_oo_tree",
                "live_hook": False,
                "backtest_hook": False,
            }
        out = dict(last)
        out["exists"] = True
        out["path"] = oo_tree_last_report_path()
        out.setdefault("success", True)
        out.setdefault("head", "y_oo_tree")
        return out

    def run_co_tree_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        holdout_trading_days: int = 20,
        backend: Optional[str] = None,
        include_alpha158: bool = True,
    ) -> Dict[str, Any]:
        """ŷ_co_tree 影子头：隔夜缺口面板 Holdout vs Ridge。只写 last_report。"""
        import time

        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.co_tree import (
            fit_co_tree_report,
            save_co_tree_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_co_tree",
                "task": "co_tree",
                "head": "y_co_tree",
                "live_hook": False,
                "backtest_hook": False,
            }

        bar_limit = int(lookback) + 40
        if include_alpha158:
            from core.signal.factors.alpha158 import ALPHA158_PANEL_WINDOW

            bar_limit = max(
                bar_limit,
                int(ALPHA158_PANEL_WINDOW) + int(holdout_trading_days or 20) + 20,
            )
        stock_bars: List[Dict[str, Any]] = []
        t_bars0 = time.perf_counter()
        for code in codes:
            bars, _src = bars_and_source(code, limit=bar_limit)
            if not bars:
                continue
            stock_bars.append({"code": str(code), "bars": bars})
        bars_s = round(time.perf_counter() - t_bars0, 2)
        report = fit_co_tree_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            holdout_trading_days=holdout_trading_days,
            backend=backend,
            include_alpha158=include_alpha158,
        )
        if isinstance(report, dict):
            timing = dict(report.get("timing") or {})
            timing["bars_s"] = bars_s
            try:
                total_s = bars_s + float(timing.get("fit_s") or 0.0)
            except (TypeError, ValueError):
                total_s = bars_s
            timing["total_s"] = round(float(total_s), 2)
            report["timing"] = timing
            report["watching_limit"] = limit
            report["watching_pool_size"] = len(pool)
            report["lookback"] = lookback
            report["live_hook"] = False
            report["backtest_hook"] = False
            if report.get("success"):
                save_co_tree_last_report(report)
        return report

    def get_co_tree_last_report(self) -> Dict[str, Any]:
        from core.research.co_tree import (
            load_co_tree_last_report,
            co_tree_last_report_path,
        )

        last = load_co_tree_last_report()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": co_tree_last_report_path(),
                "note": "尚无 ŷ_co_tree 影子报告；POST /api/quant/co-tree",
                "head": "y_co_tree",
                "live_hook": False,
                "backtest_hook": False,
            }
        out = dict(last)
        out["exists"] = True
        out["path"] = co_tree_last_report_path()
        out.setdefault("success", True)
        out.setdefault("head", "y_co_tree")
        out["live_hook"] = False
        out["backtest_hook"] = False
        return out

    def run_tau_tree_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        tau_hm: Optional[str] = None,
        holdout_trading_days: int = 20,
        backend: Optional[str] = None,
        include_alpha158: bool = True,
    ) -> Dict[str, Any]:
        """ŷ_τ_tree 影子头：同面板 Holdout vs Ridge。只写 last_report，不进 live / 回测。"""
        import time

        from core.data.facade import bars_and_source
        from core.signal.dual_score import get_dual_score_cfg
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.tau_tree import (
            fit_tau_tree_report,
            save_tau_tree_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ_tree",
                "task": "tau_tree",
                "head": "y_tau_tree",
                "live_hook": False,
                "backtest_hook": False,
            }

        ds = get_dual_score_cfg()
        from core.signal.minute_tau_grid import DEFAULT_MINUTE_TAU_GRID, TRAIN_TAU_END_HM

        if tau_hm is None:
            use_minute = bool(ds.get("enable_minute_tau"))
        else:
            tau_key_raw = str(tau_hm or "open").strip() or "open"
            use_minute = tau_key_raw.lower() not in ("", "open")
        tau_key = TRAIN_TAU_END_HM if use_minute else "open"
        tau_grid = list(DEFAULT_MINUTE_TAU_GRID) if use_minute else None

        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        t_bars0 = time.perf_counter()
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            if use_minute:
                try:
                    from core.ports.market import resolve_market_code
                    from core.store import load_minute_cache

                    mkt, pure = resolve_market_code(str(code))
                    packed = load_minute_cache(
                        mkt or "CN",
                        pure or str(code),
                        "5",
                        min_bars=1,
                        ignore_age=True,
                    )
                    if packed:
                        mb, _meta = packed
                        if mb:
                            row["minute_bars"] = mb
                            minute_hit += 1
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "tau tree minute cache failed", exc_info=True
                    )
            stock_bars.append(row)
        bars_s = round(time.perf_counter() - t_bars0, 2)
        report = fit_tau_tree_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            tau_hm=tau_key,
            tau_grid=tau_grid,
            holdout_trading_days=holdout_trading_days,
            backend=backend,
            include_alpha158=bool(include_alpha158),
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_cache_hit"] = minute_hit if use_minute else None
        report["minute_cache_universe"] = len(codes) if use_minute else None
        timing = dict(report.get("timing") or {}) if isinstance(report.get("timing"), dict) else {}
        timing["bars_s"] = bars_s
        fit_s = timing.get("fit_s")
        try:
            total_s = bars_s + (float(fit_s) if fit_s is not None else 0.0)
        except (TypeError, ValueError):
            total_s = bars_s
        timing["total_s"] = round(float(total_s), 2)
        report["timing"] = timing
        report["live_hook"] = False
        report["backtest_hook"] = False
        report["persisted"] = {
            "success": False,
            "skipped": True,
            "reason": "shadow_only",
        }
        if report.get("success"):
            save_tau_tree_last_report(report)
        return report

    def get_tau_tree_last_report(self) -> Dict[str, Any]:
        from core.research.tau_tree import (
            load_tau_tree_last_report,
            tau_tree_last_report_path,
        )

        last = load_tau_tree_last_report()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": tau_tree_last_report_path(),
                "live_hook": False,
                "backtest_hook": False,
                "head": "y_tau_tree",
                "note": "尚无 ŷ_τ_tree；POST /api/quant/tau-tree",
            }
        out = dict(last)
        out["exists"] = True
        out["path"] = tau_tree_last_report_path()
        out["live_hook"] = False
        out["backtest_hook"] = False
        out.setdefault("head", "y_tau_tree")
        return out

    run_tau_boost_experiment = run_tau_tree_experiment
    get_tau_boost_last_report = get_tau_tree_last_report

    run_rem_ridge_experiment = run_tau_ridge_experiment
    get_rem_ridge_model = get_tau_ridge_model

    def run_co_ridge_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        persist: bool = False,
        note: str = "",
        persist_role: str = "live",
        holdout_trading_days: int = 20,
    ) -> Dict[str, Any]:
        """R0+：观察池 ŷ_co Ridge；可选 persist live 模型。默认用满观察池。"""
        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.co_ridge import (
            fit_co_ridge_report,
            load_co_last_report,
            load_co_model,
            co_model_path,
            persist_co_model,
            save_co_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 co Ridge",
                "task": "co_ridge",
            }

        if persist:
            last = load_co_last_report()
            if last:
                saved = persist_co_model(
                    last,
                    note=note or "persist last co report",
                    role=persist_role,
                )
                out = dict(last)
                out["persisted"] = saved
                out["from_last_report"] = True
                if saved.get("promoted_at"):
                    out["promoted_at"] = saved["promoted_at"]
                return _attach_ridge_role_flags(out, co_model_path())

        stock_bars: List[Dict[str, Any]] = []
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            stock_bars.append({"code": str(code), "bars": bars})
        report = fit_co_ridge_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            holdout_trading_days=holdout_trading_days,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        if report.get("success"):
            save_co_last_report(report)
        if persist and report.get("success"):
            saved = persist_co_model(
                report, note=note or "api co-ridge persist", role=persist_role
            )
            report["persisted"] = saved
            if saved.get("promoted_at"):
                report["promoted_at"] = saved["promoted_at"]
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_co_model()
            report["live_model_present"] = bool(live)
        return _attach_ridge_role_flags(report, co_model_path())

    def get_co_ridge_model(self) -> Dict[str, Any]:
        from core.research.co_ridge import (
            load_co_last_report,
            load_co_model,
            co_model_path,
            co_model_path_legacy,
        )

        doc = load_co_model()
        last = load_co_last_report()
        live_file = bool(
            os.path.isfile(co_model_path()) or os.path.isfile(co_model_path_legacy())
        )
        chosen, use_last = _select_ridge_desk_doc(doc, last)
        if not chosen:
            return _attach_ridge_role_flags(
                {
                    "success": False,
                    "exists": False,
                    "path": co_model_path(),
                    "last_report_exists": bool(last),
                    "note": "尚无 ŷ_co 模型；POST /api/quant/co-ridge persist=true",
                },
                co_model_path(),
                live_present=False,
            )
        out = dict(chosen)
        out.update(
            {
                "success": True,
                "exists": True,
                "path": co_model_path(),
                "shadow": bool(use_last) or bool(chosen.get("_shadow")) or (not live_file),
                "promoted": (not use_last) and live_file and not chosen.get("_shadow"),
                "last_report_exists": bool(last),
            }
        )
        return _attach_ridge_role_flags(out, co_model_path(), live_present=live_file)

    run_on_ridge_experiment = run_co_ridge_experiment
    get_on_ridge_model = get_co_ridge_model

    def run_oo_rank_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        holdout_trading_days: int = 20,
        feature_mode: str = "raw",
        pair_preset: str = "wide",
        top_k: Optional[int] = None,
        bottom_k: Optional[int] = None,
        topk_track: int = 10,
        l2: float = 1.0,
        backend: str = "lambdarank",
        persist: bool = False,
        note: str = "",
    ) -> Dict[str, Any]:
        """影子 ŷ_oo_rank：LambdaRank；不进 live ranking。

        日线研究：优先 ``research_universe``（可宽于观察池）；空则回退观察池。
        不触发分钟暖仓。
        """
        from core.data.facade import bars_and_source
        from core.research_universe import (
            RESEARCH_UNIVERSE_MAX_SIZE,
            resolve_research_codes,
        )
        from core.research.oo_rank_pairwise import (
            fit_oo_rank_report,
            load_oo_rank_model,
            oo_rank_model_path,
            persist_oo_rank_model,
            save_oo_rank_last_report,
        )

        # watching_limit：研究宇宙非空时可放宽到 RESEARCH_UNIVERSE_MAX_SIZE
        resolved = resolve_research_codes(limit=watching_limit or None)
        codes = list(resolved.get("codes") or [])
        pool_n = int(resolved.get("count") or 0)
        limit = int(resolved.get("cap") or pool_n)
        if len(codes) < 8:
            return {
                "success": False,
                "error": "研究池至少 8 只才可跑 oo_rank pairwise（配置 research_universe 或观察池）",
                "task": "oo_rank_pairwise",
                "universe_source": resolved.get("source"),
            }

        stock_bars: List[Dict[str, Any]] = []
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            stock_bars.append({"code": str(code), "bars": bars})
        report = fit_oo_rank_report(
            stock_bars,
            holdout_trading_days=holdout_trading_days,
            feature_mode=feature_mode,
            pair_preset=pair_preset,
            top_k=top_k,
            bottom_k=bottom_k,
            topk_track=topk_track,
            l2=l2,
            backend=backend or "lambdarank",
            persist=False,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = pool_n
        report["universe_source"] = resolved.get("source")
        report["universe_note"] = resolved.get("note")
        report["research_universe_max"] = RESEARCH_UNIVERSE_MAX_SIZE
        report["lookback"] = lookback
        if note:
            report["api_note"] = str(note)[:200]
        if report.get("success"):
            save_oo_rank_last_report(report)
            oos = report.get("oos") or {}
            rank_m = oos.get("oo_rank") or {}
            ridge_m = oos.get("ridge_oo_baseline") or {}
            def _delta(a, b):
                if a is None or b is None:
                    return None
                try:
                    return round(float(a) - float(b), 4)
                except (TypeError, ValueError):
                    return None

            report["shadow_track"] = {
                "delta_topk_mean_y_oo": _delta(
                    rank_m.get("topk_mean_y_oo"), ridge_m.get("topk_mean_y_oo")
                ),
                "delta_spearman": _delta(rank_m.get("spearman"), ridge_m.get("spearman")),
                "delta_topk_overlap": _delta(
                    rank_m.get("topk_overlap"), ridge_m.get("topk_overlap")
                ),
            }
        if persist and report.get("success"):
            saved = persist_oo_rank_model(report, also_research=True)
            report["persisted"] = {"success": True, **saved}
        else:
            report["persisted"] = {"success": False, "skipped": True}
            report["live_model_present"] = bool(load_oo_rank_model(prefer_research=False))
        report["path"] = oo_rank_model_path()
        report["shadow_only"] = True
        return report

    def get_oo_rank_model(self) -> Dict[str, Any]:
        import json

        from core.research.oo_rank_pairwise import (
            load_oo_rank_model,
            oo_rank_last_report_path,
            oo_rank_model_path,
        )

        path = oo_rank_model_path()
        doc = load_oo_rank_model(prefer_research=True)
        last_path = oo_rank_last_report_path()
        last = None
        if os.path.isfile(last_path):
            try:
                with open(last_path, encoding="utf-8") as f:
                    last = json.load(f)
            except Exception:  # noqa: BLE001
                last = None
        if not doc:
            return {
                "success": False,
                "exists": False,
                "path": path,
                "last_report_exists": bool(last),
                "shadow_only": True,
                "note": "尚无 ŷ_oo_rank；POST /api/quant/oo-rank persist=true",
            }
        out = dict(doc)
        out.update(
            {
                "success": True,
                "exists": True,
                "path": path,
                "shadow_only": True,
                "last_report_exists": bool(last),
            }
        )
        return out

    def run_path_ridge_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        sell_trig_pct: Optional[float] = None,
        buy_trig_pct: Optional[float] = None,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        persist: bool = False,
        note: str = "",
        force_promote: bool = False,
        persist_role: str = "live",
        holdout_trading_days: int = 20,
    ) -> Dict[str, Any]:
        """观察池 ŷ_hl Ridge：开盘 Z + 多 τ 前缀分钟小包 → 全日极值序。

        默认用满观察池；分钟线**只读本地缓存**，不打远端（缺缓存的票跳过）。
        """
        from core.data.facade import bars_and_source
        from core.ports.market import group_minute_bars_by_date
        from core.store import load_minute_cache
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.path_ridge import (
            fit_path_ridge_report,
            load_path_last_report,
            load_path_model,
            path_model_path,
            path_promote_gate,
            persist_path_model,
            save_path_last_report,
        )

        # HL 对照标签缺省（与旧 T0 触发% 解耦；仅用于 ŷ_hl 训练/回放对照）
        _PATH_RIDGE_SELL_TRIG = 2.0
        _PATH_RIDGE_BUY_TRIG = 1.5
        sell_trig = (
            float(sell_trig_pct) if sell_trig_pct is not None else _PATH_RIDGE_SELL_TRIG
        )
        buy_trig = (
            float(buy_trig_pct) if buy_trig_pct is not None else _PATH_RIDGE_BUY_TRIG
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        # 默认吃满观察池；watching_limit 仅作上限（测试可传小值）
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_hl Ridge",
                "task": "path_ridge",
            }

        if persist:
            last = load_path_last_report()
            if last:
                saved = persist_path_model(
                    last,
                    note=note or "persist last path report",
                    force=bool(force_promote),
                    role=persist_role,
                )
                out = dict(last)
                out["persisted"] = saved
                out["from_last_report"] = True
                out["promote_gate"] = saved.get("promote_gate") or path_promote_gate(last)
                if saved.get("promoted_at"):
                    out["promoted_at"] = saved["promoted_at"]
                return _attach_ridge_role_flags(out, path_model_path())

        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        minute_by_code_date: Dict[str, Dict[str, Any]] = {}
        minute_codes_hit = 0
        minute_codes_miss = 0
        minute_days_total = 0
        minute_span_days: List[int] = []

        stock_bars: List[Dict[str, Any]] = []
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            stock_bars.append({"code": str(code), "bars": bars})
            raw = str(code).strip()
            mbars: Optional[List[dict]] = None
            try:
                packed = load_minute_cache(
                    "CN",
                    raw,
                    period,
                    min_bars=10,
                    max_age_hours=720.0,
                    ignore_age=True,
                )
                if packed:
                    mbars, _meta = packed
            except Exception:  # noqa: BLE001
                logger.debug("path ridge minute cache miss for %s", raw, exc_info=True)
            # 只用缓存：不 fetch_minute_bars；无缓存则跳过该票分钟样本
            if not mbars:
                minute_codes_miss += 1
                continue
            by_day = group_minute_bars_by_date(mbars)
            if not by_day:
                minute_codes_miss += 1
                continue
            minute_by_code_date[raw] = by_day
            minute_codes_hit += 1
            minute_days_total += len(by_day)
            minute_span_days.append(len(by_day))

        if minute_codes_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "path_ridge",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
            }

        from core.signal.minute_tau_grid import DEFAULT_MINUTE_TAU_GRID

        path_tau_grid = list(DEFAULT_MINUTE_TAU_GRID)
        report = fit_path_ridge_report(
            stock_bars,
            minute_by_code_date=minute_by_code_date,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            sell_trig_pct=sell_trig,
            buy_trig_pct=buy_trig,
            tau_grid=path_tau_grid,
            holdout_trading_days=holdout_trading_days,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_codes_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_days_total"] = minute_days_total
        report["minute_cache_only"] = True
        report["minute_cache_short_refetch"] = 0
        if minute_span_days:
            ss = sorted(minute_span_days)
            report["minute_span_days_med"] = ss[len(ss) // 2]
            report["minute_span_days_min"] = ss[0]
            report["minute_span_days_max"] = ss[-1]
        report["sell_trig_pct"] = sell_trig
        report["buy_trig_pct"] = buy_trig
        if report.get("success"):
            save_path_last_report(report)
        if persist and report.get("success"):
            saved = persist_path_model(
                report,
                note=note or "api path-ridge persist",
                force=bool(force_promote),
                role=persist_role,
            )
            report["persisted"] = saved
            report["promote_gate"] = saved.get("promote_gate") or path_promote_gate(report)
            if saved.get("promoted_at"):
                report["promoted_at"] = saved["promoted_at"]
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_path_model()
            report["live_model_present"] = bool(live)
            if report.get("success") and not report.get("promote_gate"):
                report["promote_gate"] = path_promote_gate(report)
        return _attach_ridge_role_flags(report, path_model_path())

    def get_path_ridge_model(self) -> Dict[str, Any]:
        from core.research.path_ridge import (
            load_path_last_report,
            load_path_model,
            path_model_path,
            path_promote_gate,
        )

        doc = load_path_model()
        last = load_path_last_report()
        chosen, use_last = _select_ridge_desk_doc(doc, last)
        if not chosen:
            out = {
                "success": False,
                "exists": False,
                "path": path_model_path(),
                "last_report_exists": bool(last),
                "note": "尚无 ŷ_hl 模型；POST /api/quant/path-ridge persist=true",
            }
            if last:
                out["promote_gate"] = path_promote_gate(last)
                out["oos"] = last.get("oos")
            return _attach_ridge_role_flags(out, path_model_path(), live_present=False)
        gate_src = last if use_last else (doc if not doc.get("_shadow") else (last or doc))
        return _attach_ridge_role_flags(
            {
                **chosen,
                "success": True,
                "exists": True,
                "path": path_model_path(),
                "promoted": (not use_last) and not bool(chosen.get("_shadow")),
                "shadow": bool(use_last) or bool(chosen.get("_shadow")),
                "last_report_exists": bool(last),
                "promote_gate": path_promote_gate(gate_src),
            },
            path_model_path(),
        )

    def run_tc_ridge_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        persist: bool = False,
        note: str = "",
        force_promote: bool = False,
        persist_role: str = "live",
        holdout_trading_days: int = 20,
    ) -> Dict[str, Any]:
        """观察池 ŷ_τc Ridge：与 ŷ_oc 同 X → close[T]/price(τ)−1。只读本地 5m 缓存。"""
        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.tc_ridge import (
            fit_tc_ridge_report,
            load_tc_last_report,
            load_tc_model,
            persist_tc_model,
            tc_model_path,
            tc_promote_gate,
            save_tc_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τc Ridge",
                "task": "tc_ridge",
            }

        if persist:
            last = load_tc_last_report()
            if last:
                saved = persist_tc_model(
                    last,
                    note=note or "persist last r report",
                    force=bool(force_promote),
                    role=persist_role,
                )
                out = dict(last)
                out["persisted"] = saved
                out["from_last_report"] = True
                out["promote_gate"] = saved.get("promote_gate") or tc_promote_gate(last)
                if saved.get("promoted_at"):
                    out["promoted_at"] = saved["promoted_at"]
                return _attach_ridge_role_flags(out, tc_model_path())

        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("r ridge minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "tc_ridge",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
            }

        report = fit_tc_ridge_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            holdout_trading_days=holdout_trading_days,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_only"] = True
        if report.get("success"):
            save_tc_last_report(report)
        if persist and report.get("success"):
            saved = persist_tc_model(
                report,
                note=note or "api r-ridge persist",
                force=bool(force_promote),
                role=persist_role,
            )
            report["persisted"] = saved
            report["promote_gate"] = saved.get("promote_gate") or tc_promote_gate(report)
            if saved.get("promoted_at"):
                report["promoted_at"] = saved["promoted_at"]
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_tc_model()
            report["live_model_present"] = bool(live)
            if report.get("success") and not report.get("promote_gate"):
                report["promote_gate"] = tc_promote_gate(report)
        return _attach_ridge_role_flags(report, tc_model_path())

    def get_tc_ridge_model(self) -> Dict[str, Any]:
        from core.research.tc_ridge import (
            load_tc_last_report,
            load_tc_model,
            tc_model_path,
            tc_promote_gate,
        )

        doc = load_tc_model()
        last = load_tc_last_report()
        chosen, use_last = _select_ridge_desk_doc(doc, last)
        if not chosen:
            out = {
                "success": False,
                "exists": False,
                "path": tc_model_path(),
                "last_report_exists": bool(last),
                "note": "尚无 ŷ_τc 模型；POST /api/quant/tc-ridge persist=true",
            }
            if last:
                out["promote_gate"] = tc_promote_gate(last)
                out["oos"] = last.get("oos")
            return _attach_ridge_role_flags(out, tc_model_path(), live_present=False)
        gate_src = last if use_last else (doc if not doc.get("_shadow") else (last or doc))
        packed = {
            **chosen,
            "success": True,
            "exists": True,
            "path": tc_model_path(),
            "promoted": (not use_last) and not bool(chosen.get("_shadow")),
            "shadow": bool(use_last) or bool(chosen.get("_shadow")),
            "last_report_exists": bool(last),
            "promote_gate": tc_promote_gate(gate_src),
        }
        research_rm = packed.get("return_model_research")
        if not isinstance(research_rm, dict):
            if isinstance(last, dict) and isinstance(last.get("return_model_research"), dict):
                research_rm = last["return_model_research"]
            else:
                research_doc = load_tc_model(role="research")
                research_rm = (
                    research_doc.get("return_model")
                    if isinstance(research_doc, dict)
                    else None
                )
        if isinstance(research_rm, dict):
            packed["return_model_research"] = research_rm
        return _attach_ridge_role_flags(packed, tc_model_path())

    def run_t30_ridge_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        persist: bool = False,
        note: str = "",
        force_promote: bool = False,
        persist_role: str = "live",
        holdout_trading_days: int = 20,
    ) -> Dict[str, Any]:
        """观察池 ŷ_τ30 Ridge：与 ŷ_oc 同 X → mean(price(τ⊕25/30/35))/price(τ)−1。只读本地 5m 缓存。"""
        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.t30_ridge import (
            fit_t30_ridge_report,
            load_t30_last_report,
            load_t30_model,
            persist_t30_model,
            t30_model_path,
            t30_promote_gate,
            save_t30_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ30 Ridge",
                "task": "t30_ridge",
            }

        if persist:
            last = load_t30_last_report()
            if last:
                saved = persist_t30_model(
                    last,
                    note=note or "persist last t30 report",
                    force=bool(force_promote),
                    role=persist_role,
                )
                out = dict(last)
                out["persisted"] = saved
                out["from_last_report"] = True
                out["promote_gate"] = saved.get("promote_gate") or t30_promote_gate(last)
                if saved.get("promoted_at"):
                    out["promoted_at"] = saved["promoted_at"]
                return _attach_ridge_role_flags(out, t30_model_path())

        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("t30 ridge minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "t30_ridge",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
            }

        report = fit_t30_ridge_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            holdout_trading_days=holdout_trading_days,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_only"] = True
        if report.get("success"):
            save_t30_last_report(report)
        if persist and report.get("success"):
            saved = persist_t30_model(
                report,
                note=note or "api t30-ridge persist",
                force=bool(force_promote),
                role=persist_role,
            )
            report["persisted"] = saved
            report["promote_gate"] = saved.get("promote_gate") or t30_promote_gate(report)
            if saved.get("promoted_at"):
                report["promoted_at"] = saved["promoted_at"]
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_t30_model()
            report["live_model_present"] = bool(live)
            if report.get("success") and not report.get("promote_gate"):
                report["promote_gate"] = t30_promote_gate(report)
        return _attach_ridge_role_flags(report, t30_model_path())

    def get_t30_ridge_model(self) -> Dict[str, Any]:
        from core.research.t30_ridge import (
            load_t30_last_report,
            load_t30_model,
            t30_model_path,
            t30_promote_gate,
        )

        doc = load_t30_model()
        last = load_t30_last_report()
        chosen, use_last = _select_ridge_desk_doc(doc, last)
        if not chosen:
            out = {
                "success": False,
                "exists": False,
                "path": t30_model_path(),
                "last_report_exists": bool(last),
                "note": "尚无 ŷ_τ30 模型；POST /api/quant/t30-ridge persist=true",
            }
            if last:
                out["promote_gate"] = t30_promote_gate(last)
                out["oos"] = last.get("oos")
            return _attach_ridge_role_flags(out, t30_model_path(), live_present=False)
        gate_src = last if use_last else (doc if not doc.get("_shadow") else (last or doc))
        packed = {
            **chosen,
            "success": True,
            "exists": True,
            "path": t30_model_path(),
            "promoted": (not use_last) and not bool(chosen.get("_shadow")),
            "shadow": bool(use_last) or bool(chosen.get("_shadow")),
            "last_report_exists": bool(last),
            "promote_gate": t30_promote_gate(gate_src),
        }
        research_rm = packed.get("return_model_research")
        if not isinstance(research_rm, dict):
            if isinstance(last, dict) and isinstance(last.get("return_model_research"), dict):
                research_rm = last["return_model_research"]
            else:
                research_doc = load_t30_model(role="research")
                research_rm = (
                    research_doc.get("return_model")
                    if isinstance(research_doc, dict)
                    else None
                )
        if isinstance(research_rm, dict):
            packed["return_model_research"] = research_rm
        return _attach_ridge_role_flags(packed, t30_model_path())

    def start_t30_ridge_job(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        note: str = "",
        holdout_trading_days: int = 20,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """后台 ŷ_τ30 拟合；轮询 ``GET /api/jobs/t30-ridge``。不写盘。"""
        from core.job_progress import t30_ridge_job

        kwargs = dict(
            lookback=lookback,
            watching_limit=watching_limit,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            minute_period=minute_period,
            minute_lookback_days=minute_lookback_days,
            persist=False,
            note=note or "",
            holdout_trading_days=holdout_trading_days,
        )
        return _start_ridge_fit_job(
            slot=t30_ridge_job,
            kind="t30_ridge",
            message="ŷ_τ30 拟合中…",
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            worker_fn=lambda: self.run_t30_ridge_experiment(**kwargs),
        )

    def run_t45_ridge_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        persist: bool = False,
        note: str = "",
        force_promote: bool = False,
        persist_role: str = "live",
        holdout_trading_days: int = 20,
    ) -> Dict[str, Any]:
        """观察池 ŷ_τ45 Ridge：与 ŷ_oc 同 X → mean(price(τ⊕40/45/50))/price(τ)−1。只读本地 5m 缓存。"""
        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.t45_ridge import (
            fit_t45_ridge_report,
            load_t45_last_report,
            load_t45_model,
            persist_t45_model,
            t45_model_path,
            t45_promote_gate,
            save_t45_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ45 Ridge",
                "task": "t45_ridge",
            }

        if persist:
            last = load_t45_last_report()
            if last:
                saved = persist_t45_model(
                    last,
                    note=note or "persist last t45 report",
                    force=bool(force_promote),
                    role=persist_role,
                )
                out = dict(last)
                out["persisted"] = saved
                out["from_last_report"] = True
                out["promote_gate"] = saved.get("promote_gate") or t45_promote_gate(last)
                if saved.get("promoted_at"):
                    out["promoted_at"] = saved["promoted_at"]
                return _attach_ridge_role_flags(out, t45_model_path())

        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("t45 ridge minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "t45_ridge",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
            }

        report = fit_t45_ridge_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            holdout_trading_days=holdout_trading_days,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_only"] = True
        if report.get("success"):
            save_t45_last_report(report)
        if persist and report.get("success"):
            saved = persist_t45_model(
                report,
                note=note or "api t45-ridge persist",
                force=bool(force_promote),
                role=persist_role,
            )
            report["persisted"] = saved
            report["promote_gate"] = saved.get("promote_gate") or t45_promote_gate(report)
            if saved.get("promoted_at"):
                report["promoted_at"] = saved["promoted_at"]
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_t45_model()
            report["live_model_present"] = bool(live)
            if report.get("success") and not report.get("promote_gate"):
                report["promote_gate"] = t45_promote_gate(report)
        return _attach_ridge_role_flags(report, t45_model_path())

    def get_t45_ridge_model(self) -> Dict[str, Any]:
        from core.research.t45_ridge import (
            load_t45_last_report,
            load_t45_model,
            t45_model_path,
            t45_promote_gate,
        )

        doc = load_t45_model()
        last = load_t45_last_report()
        chosen, use_last = _select_ridge_desk_doc(doc, last)
        if not chosen:
            out = {
                "success": False,
                "exists": False,
                "path": t45_model_path(),
                "last_report_exists": bool(last),
                "note": "尚无 ŷ_τ45 模型；POST /api/quant/t45-ridge persist=true",
            }
            if last:
                out["promote_gate"] = t45_promote_gate(last)
                out["oos"] = last.get("oos")
            return _attach_ridge_role_flags(out, t45_model_path(), live_present=False)
        gate_src = last if use_last else (doc if not doc.get("_shadow") else (last or doc))
        packed = {
            **chosen,
            "success": True,
            "exists": True,
            "path": t45_model_path(),
            "promoted": (not use_last) and not bool(chosen.get("_shadow")),
            "shadow": bool(use_last) or bool(chosen.get("_shadow")),
            "last_report_exists": bool(last),
            "promote_gate": t45_promote_gate(gate_src),
        }
        research_rm = packed.get("return_model_research")
        if not isinstance(research_rm, dict):
            if isinstance(last, dict) and isinstance(last.get("return_model_research"), dict):
                research_rm = last["return_model_research"]
            else:
                research_doc = load_t45_model(role="research")
                research_rm = (
                    research_doc.get("return_model")
                    if isinstance(research_doc, dict)
                    else None
                )
        if isinstance(research_rm, dict):
            packed["return_model_research"] = research_rm
        return _attach_ridge_role_flags(packed, t45_model_path())

    def start_t45_ridge_job(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        note: str = "",
        holdout_trading_days: int = 20,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """后台 ŷ_τ45 拟合；轮询 ``GET /api/jobs/t45-ridge``。不写盘。"""
        from core.job_progress import t45_ridge_job

        kwargs = dict(
            lookback=lookback,
            watching_limit=watching_limit,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            minute_period=minute_period,
            minute_lookback_days=minute_lookback_days,
            persist=False,
            note=note or "",
            holdout_trading_days=holdout_trading_days,
        )
        return _start_ridge_fit_job(
            slot=t45_ridge_job,
            kind="t45_ridge",
            message="ŷ_τ45 拟合中…",
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            worker_fn=lambda: self.run_t45_ridge_experiment(**kwargs),
        )

    def run_t60_ridge_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        persist: bool = False,
        note: str = "",
        force_promote: bool = False,
        persist_role: str = "live",
        holdout_trading_days: int = 20,
    ) -> Dict[str, Any]:
        """观察池 ŷ_τ60 Ridge：与 ŷ_oc 同 X → mean(price(τ⊕55/60/65))/price(τ)−1。只读本地 5m 缓存。"""
        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.t60_ridge import (
            fit_t60_ridge_report,
            load_t60_last_report,
            load_t60_model,
            persist_t60_model,
            t60_model_path,
            t60_promote_gate,
            save_t60_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ60 Ridge",
                "task": "t60_ridge",
            }

        if persist:
            last = load_t60_last_report()
            if last:
                saved = persist_t60_model(
                    last,
                    note=note or "persist last t60 report",
                    force=bool(force_promote),
                    role=persist_role,
                )
                out = dict(last)
                out["persisted"] = saved
                out["from_last_report"] = True
                out["promote_gate"] = saved.get("promote_gate") or t60_promote_gate(last)
                if saved.get("promoted_at"):
                    out["promoted_at"] = saved["promoted_at"]
                return _attach_ridge_role_flags(out, t60_model_path())

        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("t60 ridge minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "t60_ridge",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
            }

        report = fit_t60_ridge_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            holdout_trading_days=holdout_trading_days,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_only"] = True
        if report.get("success"):
            save_t60_last_report(report)
        if persist and report.get("success"):
            saved = persist_t60_model(
                report,
                note=note or "api t60-ridge persist",
                force=bool(force_promote),
                role=persist_role,
            )
            report["persisted"] = saved
            report["promote_gate"] = saved.get("promote_gate") or t60_promote_gate(report)
            if saved.get("promoted_at"):
                report["promoted_at"] = saved["promoted_at"]
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_t60_model()
            report["live_model_present"] = bool(live)
            if report.get("success") and not report.get("promote_gate"):
                report["promote_gate"] = t60_promote_gate(report)
        return _attach_ridge_role_flags(report, t60_model_path())

    def get_t60_ridge_model(self) -> Dict[str, Any]:
        from core.research.t60_ridge import (
            load_t60_last_report,
            load_t60_model,
            t60_model_path,
            t60_promote_gate,
        )

        doc = load_t60_model()
        last = load_t60_last_report()
        chosen, use_last = _select_ridge_desk_doc(doc, last)
        if not chosen:
            out = {
                "success": False,
                "exists": False,
                "path": t60_model_path(),
                "last_report_exists": bool(last),
                "note": "尚无 ŷ_τ60 模型；POST /api/quant/t60-ridge persist=true",
            }
            if last:
                out["promote_gate"] = t60_promote_gate(last)
                out["oos"] = last.get("oos")
            return _attach_ridge_role_flags(out, t60_model_path(), live_present=False)
        gate_src = last if use_last else (doc if not doc.get("_shadow") else (last or doc))
        packed = {
            **chosen,
            "success": True,
            "exists": True,
            "path": t60_model_path(),
            "promoted": (not use_last) and not bool(chosen.get("_shadow")),
            "shadow": bool(use_last) or bool(chosen.get("_shadow")),
            "last_report_exists": bool(last),
            "promote_gate": t60_promote_gate(gate_src),
        }
        research_rm = packed.get("return_model_research")
        if not isinstance(research_rm, dict):
            if isinstance(last, dict) and isinstance(last.get("return_model_research"), dict):
                research_rm = last["return_model_research"]
            else:
                research_doc = load_t60_model(role="research")
                research_rm = (
                    research_doc.get("return_model")
                    if isinstance(research_doc, dict)
                    else None
                )
        if isinstance(research_rm, dict):
            packed["return_model_research"] = research_rm
        return _attach_ridge_role_flags(packed, t60_model_path())

    def start_t60_ridge_job(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        note: str = "",
        holdout_trading_days: int = 20,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """后台 ŷ_τ60 拟合；轮询 ``GET /api/jobs/t60-ridge``。不写盘。"""
        from core.job_progress import t60_ridge_job

        kwargs = dict(
            lookback=lookback,
            watching_limit=watching_limit,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            minute_period=minute_period,
            minute_lookback_days=minute_lookback_days,
            persist=False,
            note=note or "",
            holdout_trading_days=holdout_trading_days,
        )
        return _start_ridge_fit_job(
            slot=t60_ridge_job,
            kind="t60_ridge",
            message="ŷ_τ60 拟合中…",
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            worker_fn=lambda: self.run_t60_ridge_experiment(**kwargs),
        )

    def run_t75_ridge_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        persist: bool = False,
        note: str = "",
        force_promote: bool = False,
        persist_role: str = "live",
        holdout_trading_days: int = 20,
    ) -> Dict[str, Any]:
        """观察池 ŷ_τ75 Ridge：与 ŷ_oc 同 X → mean(price(τ⊕70/75/80))/price(τ)−1。只读本地 5m 缓存。"""
        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.t75_ridge import (
            fit_t75_ridge_report,
            load_t75_last_report,
            load_t75_model,
            persist_t75_model,
            t75_model_path,
            t75_promote_gate,
            save_t75_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ75 Ridge",
                "task": "t75_ridge",
            }

        if persist:
            last = load_t75_last_report()
            if last:
                saved = persist_t75_model(
                    last,
                    note=note or "persist last t75 report",
                    force=bool(force_promote),
                    role=persist_role,
                )
                out = dict(last)
                out["persisted"] = saved
                out["from_last_report"] = True
                out["promote_gate"] = saved.get("promote_gate") or t75_promote_gate(last)
                if saved.get("promoted_at"):
                    out["promoted_at"] = saved["promoted_at"]
                return _attach_ridge_role_flags(out, t75_model_path())

        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("t75 ridge minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "t75_ridge",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
            }

        report = fit_t75_ridge_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            holdout_trading_days=holdout_trading_days,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_only"] = True
        if report.get("success"):
            save_t75_last_report(report)
        if persist and report.get("success"):
            saved = persist_t75_model(
                report,
                note=note or "api t75-ridge persist",
                force=bool(force_promote),
                role=persist_role,
            )
            report["persisted"] = saved
            report["promote_gate"] = saved.get("promote_gate") or t75_promote_gate(report)
            if saved.get("promoted_at"):
                report["promoted_at"] = saved["promoted_at"]
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_t75_model()
            report["live_model_present"] = bool(live)
            if report.get("success") and not report.get("promote_gate"):
                report["promote_gate"] = t75_promote_gate(report)
        return _attach_ridge_role_flags(report, t75_model_path())

    def get_t75_ridge_model(self) -> Dict[str, Any]:
        from core.research.t75_ridge import (
            load_t75_last_report,
            load_t75_model,
            t75_model_path,
            t75_promote_gate,
        )

        doc = load_t75_model()
        last = load_t75_last_report()
        chosen, use_last = _select_ridge_desk_doc(doc, last)
        if not chosen:
            out = {
                "success": False,
                "exists": False,
                "path": t75_model_path(),
                "last_report_exists": bool(last),
                "note": "尚无 ŷ_τ75 模型；POST /api/quant/t75-ridge persist=true",
            }
            if last:
                out["promote_gate"] = t75_promote_gate(last)
                out["oos"] = last.get("oos")
            return _attach_ridge_role_flags(out, t75_model_path(), live_present=False)
        gate_src = last if use_last else (doc if not doc.get("_shadow") else (last or doc))
        packed = {
            **chosen,
            "success": True,
            "exists": True,
            "path": t75_model_path(),
            "promoted": (not use_last) and not bool(chosen.get("_shadow")),
            "shadow": bool(use_last) or bool(chosen.get("_shadow")),
            "last_report_exists": bool(last),
            "promote_gate": t75_promote_gate(gate_src),
        }
        research_rm = packed.get("return_model_research")
        if not isinstance(research_rm, dict):
            if isinstance(last, dict) and isinstance(last.get("return_model_research"), dict):
                research_rm = last["return_model_research"]
            else:
                research_doc = load_t75_model(role="research")
                research_rm = (
                    research_doc.get("return_model")
                    if isinstance(research_doc, dict)
                    else None
                )
        if isinstance(research_rm, dict):
            packed["return_model_research"] = research_rm
        return _attach_ridge_role_flags(packed, t75_model_path())

    def start_t75_ridge_job(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        note: str = "",
        holdout_trading_days: int = 20,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """后台 ŷ_τ75 拟合；轮询 ``GET /api/jobs/t75-ridge``。不写盘。"""
        from core.job_progress import t75_ridge_job

        kwargs = dict(
            lookback=lookback,
            watching_limit=watching_limit,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            minute_period=minute_period,
            minute_lookback_days=minute_lookback_days,
            persist=False,
            note=note or "",
            holdout_trading_days=holdout_trading_days,
        )
        return _start_ridge_fit_job(
            slot=t75_ridge_job,
            kind="t75_ridge",
            message="ŷ_τ75 拟合中…",
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            worker_fn=lambda: self.run_t75_ridge_experiment(**kwargs),
        )

    def run_t90_ridge_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        persist: bool = False,
        note: str = "",
        force_promote: bool = False,
        persist_role: str = "live",
        holdout_trading_days: int = 20,
    ) -> Dict[str, Any]:
        """观察池 ŷ_τ90 Ridge：与 ŷ_oc 同 X → mean(price(τ⊕85/90/95))/price(τ)−1。只读本地 5m 缓存。"""
        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.t90_ridge import (
            fit_t90_ridge_report,
            load_t90_last_report,
            load_t90_model,
            persist_t90_model,
            t90_model_path,
            t90_promote_gate,
            save_t90_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ90 Ridge",
                "task": "t90_ridge",
            }

        if persist:
            last = load_t90_last_report()
            if last:
                saved = persist_t90_model(
                    last,
                    note=note or "persist last t90 report",
                    force=bool(force_promote),
                    role=persist_role,
                )
                out = dict(last)
                out["persisted"] = saved
                out["from_last_report"] = True
                out["promote_gate"] = saved.get("promote_gate") or t90_promote_gate(last)
                if saved.get("promoted_at"):
                    out["promoted_at"] = saved["promoted_at"]
                return _attach_ridge_role_flags(out, t90_model_path())

        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("t90 ridge minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "t90_ridge",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
            }

        report = fit_t90_ridge_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            holdout_trading_days=holdout_trading_days,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_only"] = True
        if report.get("success"):
            save_t90_last_report(report)
        if persist and report.get("success"):
            saved = persist_t90_model(
                report,
                note=note or "api t90-ridge persist",
                force=bool(force_promote),
                role=persist_role,
            )
            report["persisted"] = saved
            report["promote_gate"] = saved.get("promote_gate") or t90_promote_gate(report)
            if saved.get("promoted_at"):
                report["promoted_at"] = saved["promoted_at"]
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_t90_model()
            report["live_model_present"] = bool(live)
            if report.get("success") and not report.get("promote_gate"):
                report["promote_gate"] = t90_promote_gate(report)
        return _attach_ridge_role_flags(report, t90_model_path())

    def get_t90_ridge_model(self) -> Dict[str, Any]:
        from core.research.t90_ridge import (
            load_t90_last_report,
            load_t90_model,
            t90_model_path,
            t90_promote_gate,
        )

        doc = load_t90_model()
        last = load_t90_last_report()
        chosen, use_last = _select_ridge_desk_doc(doc, last)
        if not chosen:
            out = {
                "success": False,
                "exists": False,
                "path": t90_model_path(),
                "last_report_exists": bool(last),
                "note": "尚无 ŷ_τ90 模型；POST /api/quant/t90-ridge persist=true",
            }
            if last:
                out["promote_gate"] = t90_promote_gate(last)
                out["oos"] = last.get("oos")
            return _attach_ridge_role_flags(out, t90_model_path(), live_present=False)
        gate_src = last if use_last else (doc if not doc.get("_shadow") else (last or doc))
        packed = {
            **chosen,
            "success": True,
            "exists": True,
            "path": t90_model_path(),
            "promoted": (not use_last) and not bool(chosen.get("_shadow")),
            "shadow": bool(use_last) or bool(chosen.get("_shadow")),
            "last_report_exists": bool(last),
            "promote_gate": t90_promote_gate(gate_src),
        }
        research_rm = packed.get("return_model_research")
        if not isinstance(research_rm, dict):
            if isinstance(last, dict) and isinstance(last.get("return_model_research"), dict):
                research_rm = last["return_model_research"]
            else:
                research_doc = load_t90_model(role="research")
                research_rm = (
                    research_doc.get("return_model")
                    if isinstance(research_doc, dict)
                    else None
                )
        if isinstance(research_rm, dict):
            packed["return_model_research"] = research_rm
        return _attach_ridge_role_flags(packed, t90_model_path())

    def start_t90_ridge_job(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        note: str = "",
        holdout_trading_days: int = 20,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """后台 ŷ_τ90 拟合；轮询 ``GET /api/jobs/t90-ridge``。不写盘。"""
        from core.job_progress import t90_ridge_job

        kwargs = dict(
            lookback=lookback,
            watching_limit=watching_limit,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            minute_period=minute_period,
            minute_lookback_days=minute_lookback_days,
            persist=False,
            note=note or "",
            holdout_trading_days=holdout_trading_days,
        )
        return _start_ridge_fit_job(
            slot=t90_ridge_job,
            kind="t90_ridge",
            message="ŷ_τ90 拟合中…",
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            worker_fn=lambda: self.run_t90_ridge_experiment(**kwargs),
        )

    def start_tc_ridge_job(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
        note: str = "",
        holdout_trading_days: int = 20,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """后台 ŷ_τc 拟合；轮询 ``GET /api/jobs/tc-ridge``。不写盘。"""
        from core.job_progress import tc_ridge_job

        kwargs = dict(
            lookback=lookback,
            watching_limit=watching_limit,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            minute_period=minute_period,
            minute_lookback_days=minute_lookback_days,
            persist=False,
            note=note or "",
            holdout_trading_days=holdout_trading_days,
        )
        return _start_ridge_fit_job(
            slot=tc_ridge_job,
            kind="tc_ridge",
            message="ŷ_τc 拟合中…",
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            worker_fn=lambda: self.run_tc_ridge_experiment(**kwargs),
        )

    def run_tc_tree_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        tau_hm: Optional[str] = None,
        holdout_trading_days: int = 20,
        backend: Optional[str] = None,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
    ) -> Dict[str, Any]:
        """ŷ_τc_tree 影子头：同面板 Holdout vs Ridge。只写 last_report，不进 live / 回测。"""
        import time

        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.tc_tree import (
            fit_tc_tree_report,
            save_tc_tree_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τc_tree",
                "task": "tc_tree",
                "head": "y_tc_tree",
                "live_hook": False,
                "backtest_hook": False,
            }

        live_hm = str(tau_hm or "10:30").strip() or "10:30"
        if live_hm.lower() in ("", "open"):
            live_hm = "10:30"
        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        t_bars0 = time.perf_counter()
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("r tree minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)
        bars_s = round(time.perf_counter() - t_bars0, 2)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "tc_tree",
                "head": "y_tc_tree",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
                "live_hook": False,
                "backtest_hook": False,
            }

        report = fit_tc_tree_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            tau_hm=live_hm,
            holdout_trading_days=holdout_trading_days,
            backend=backend,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_hit"] = minute_hit
        report["minute_cache_universe"] = len(codes)
        report["minute_cache_only"] = True
        timing = dict(report.get("timing") or {}) if isinstance(report.get("timing"), dict) else {}
        timing["bars_s"] = bars_s
        fit_s = timing.get("fit_s")
        try:
            total_s = bars_s + (float(fit_s) if fit_s is not None else 0.0)
        except (TypeError, ValueError):
            total_s = bars_s
        timing["total_s"] = round(float(total_s), 2)
        report["timing"] = timing
        report["live_hook"] = False
        report["backtest_hook"] = False
        report["persisted"] = {
            "success": False,
            "skipped": True,
            "reason": "shadow_only",
        }
        if report.get("success"):
            save_tc_tree_last_report(report)
        return report

    def get_tc_tree_last_report(self) -> Dict[str, Any]:
        from core.research.tc_tree import (
            load_tc_tree_last_report,
            tc_tree_last_report_path,
        )

        last = load_tc_tree_last_report()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": tc_tree_last_report_path(),
                "live_hook": False,
                "backtest_hook": False,
                "head": "y_tc_tree",
                "note": "尚无 ŷ_τc_tree；POST /api/quant/tc-tree",
            }
        out = dict(last)
        out["exists"] = True
        out["path"] = tc_tree_last_report_path()
        out["live_hook"] = False
        out["backtest_hook"] = False
        out.setdefault("head", "y_tc_tree")
        return out

    # 旧名兼容（ŷ_r / r_ridge / r_tree）
    run_r_ridge_experiment = run_tc_ridge_experiment
    get_r_ridge_model = get_tc_ridge_model
    start_r_ridge_job = start_tc_ridge_job
    run_r_tree_experiment = run_tc_tree_experiment
    get_r_tree_last_report = get_tc_tree_last_report

    def run_t30_tree_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        tau_hm: Optional[str] = None,
        holdout_trading_days: int = 20,
        backend: Optional[str] = None,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
    ) -> Dict[str, Any]:
        """ŷ_τ30_tree 影子头：同面板 Holdout vs Ridge。只写 last_report，不进 live / 回测。"""
        import time

        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.t30_tree import (
            fit_t30_tree_report,
            persist_t30_tree_model,
            save_t30_tree_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ30_tree",
                "task": "t30_tree",
                "head": "y_t30_tree",
                "live_hook": False,
                "backtest_hook": False,
            }

        live_hm = str(tau_hm or "10:30").strip() or "10:30"
        if live_hm.lower() in ("", "open"):
            live_hm = "10:30"
        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        t_bars0 = time.perf_counter()
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("t30 tree minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)
        bars_s = round(time.perf_counter() - t_bars0, 2)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "t30_tree",
                "head": "y_t30_tree",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
                "live_hook": False,
                "backtest_hook": False,
            }

        report = fit_t30_tree_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            tau_hm=live_hm,
            holdout_trading_days=holdout_trading_days,
            backend=backend,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_hit"] = minute_hit
        report["minute_cache_universe"] = len(codes)
        report["minute_cache_only"] = True
        timing = dict(report.get("timing") or {}) if isinstance(report.get("timing"), dict) else {}
        timing["bars_s"] = bars_s
        fit_s = timing.get("fit_s")
        try:
            total_s = bars_s + (float(fit_s) if fit_s is not None else 0.0)
        except (TypeError, ValueError):
            total_s = bars_s
        timing["total_s"] = round(float(total_s), 2)
        report["timing"] = timing
        report["live_hook"] = False
        report["backtest_hook"] = True
        if report.get("success"):
            save_t30_tree_last_report(report)
            saved = persist_t30_tree_model(
                report,
                note=f"t30_tree fit auto-persist for backtest",
                force=True,
            )
            report["persisted"] = saved
            if saved.get("path"):
                report["model_path"] = saved["path"]
        else:
            report["persisted"] = {
                "success": False,
                "skipped": True,
                "reason": "fit_failed",
            }
        return report

    def get_t30_tree_last_report(self) -> Dict[str, Any]:
        from core.research.t30_tree import (
            load_t30_tree_last_report,
            t30_tree_last_report_path,
        )

        last = load_t30_tree_last_report()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": t30_tree_last_report_path(),
                "live_hook": False,
                "backtest_hook": False,
                "head": "y_t30_tree",
                "note": "尚无 ŷ_τ30_tree；POST /api/quant/t30-tree",
            }
        out = dict(last)
        out["exists"] = True
        out["path"] = t30_tree_last_report_path()
        out["live_hook"] = False
        out["backtest_hook"] = False
        out.setdefault("head", "y_t30_tree")
        return out

    def run_t45_tree_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        tau_hm: Optional[str] = None,
        holdout_trading_days: int = 20,
        backend: Optional[str] = None,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
    ) -> Dict[str, Any]:
        """ŷ_τ45_tree 影子头：同面板 Holdout vs Ridge。只写 last_report，不进 live / 回测。"""
        import time

        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.t45_tree import (
            fit_t45_tree_report,
            persist_t45_tree_model,
            save_t45_tree_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ45_tree",
                "task": "t45_tree",
                "head": "y_t45_tree",
                "live_hook": False,
                "backtest_hook": False,
            }

        live_hm = str(tau_hm or "10:30").strip() or "10:30"
        if live_hm.lower() in ("", "open"):
            live_hm = "10:30"
        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        t_bars0 = time.perf_counter()
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("t45 tree minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)
        bars_s = round(time.perf_counter() - t_bars0, 2)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "t45_tree",
                "head": "y_t45_tree",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
                "live_hook": False,
                "backtest_hook": False,
            }

        report = fit_t45_tree_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            tau_hm=live_hm,
            holdout_trading_days=holdout_trading_days,
            backend=backend,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_hit"] = minute_hit
        report["minute_cache_universe"] = len(codes)
        report["minute_cache_only"] = True
        timing = dict(report.get("timing") or {}) if isinstance(report.get("timing"), dict) else {}
        timing["bars_s"] = bars_s
        fit_s = timing.get("fit_s")
        try:
            total_s = bars_s + (float(fit_s) if fit_s is not None else 0.0)
        except (TypeError, ValueError):
            total_s = bars_s
        timing["total_s"] = round(float(total_s), 2)
        report["timing"] = timing
        report["live_hook"] = False
        report["backtest_hook"] = True
        if report.get("success"):
            save_t45_tree_last_report(report)
            saved = persist_t45_tree_model(
                report,
                note=f"t45_tree fit auto-persist for backtest",
                force=True,
            )
            report["persisted"] = saved
            if saved.get("path"):
                report["model_path"] = saved["path"]
        else:
            report["persisted"] = {
                "success": False,
                "skipped": True,
                "reason": "fit_failed",
            }
        return report

    def get_t45_tree_last_report(self) -> Dict[str, Any]:
        from core.research.t45_tree import (
            load_t45_tree_last_report,
            t45_tree_last_report_path,
        )

        last = load_t45_tree_last_report()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": t45_tree_last_report_path(),
                "live_hook": False,
                "backtest_hook": False,
                "head": "y_t45_tree",
                "note": "尚无 ŷ_τ45_tree；POST /api/quant/t45-tree",
            }
        out = dict(last)
        out["exists"] = True
        out["path"] = t45_tree_last_report_path()
        out["live_hook"] = False
        out["backtest_hook"] = False
        out.setdefault("head", "y_t45_tree")
        return out

    def run_t60_tree_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        tau_hm: Optional[str] = None,
        holdout_trading_days: int = 20,
        backend: Optional[str] = None,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
    ) -> Dict[str, Any]:
        """ŷ_τ60_tree 影子头：同面板 Holdout vs Ridge。只写 last_report，不进 live / 回测。"""
        import time

        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.t60_tree import (
            fit_t60_tree_report,
            persist_t60_tree_model,
            save_t60_tree_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ60_tree",
                "task": "t60_tree",
                "head": "y_t60_tree",
                "live_hook": False,
                "backtest_hook": False,
            }

        live_hm = str(tau_hm or "10:30").strip() or "10:30"
        if live_hm.lower() in ("", "open"):
            live_hm = "10:30"
        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        t_bars0 = time.perf_counter()
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("t60 tree minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)
        bars_s = round(time.perf_counter() - t_bars0, 2)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "t60_tree",
                "head": "y_t60_tree",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
                "live_hook": False,
                "backtest_hook": False,
            }

        report = fit_t60_tree_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            tau_hm=live_hm,
            holdout_trading_days=holdout_trading_days,
            backend=backend,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_hit"] = minute_hit
        report["minute_cache_universe"] = len(codes)
        report["minute_cache_only"] = True
        timing = dict(report.get("timing") or {}) if isinstance(report.get("timing"), dict) else {}
        timing["bars_s"] = bars_s
        fit_s = timing.get("fit_s")
        try:
            total_s = bars_s + (float(fit_s) if fit_s is not None else 0.0)
        except (TypeError, ValueError):
            total_s = bars_s
        timing["total_s"] = round(float(total_s), 2)
        report["timing"] = timing
        report["live_hook"] = False
        report["backtest_hook"] = True
        if report.get("success"):
            save_t60_tree_last_report(report)
            saved = persist_t60_tree_model(
                report,
                note=f"t60_tree fit auto-persist for backtest",
                force=True,
            )
            report["persisted"] = saved
            if saved.get("path"):
                report["model_path"] = saved["path"]
        else:
            report["persisted"] = {
                "success": False,
                "skipped": True,
                "reason": "fit_failed",
            }
        return report

    def get_t60_tree_last_report(self) -> Dict[str, Any]:
        from core.research.t60_tree import (
            load_t60_tree_last_report,
            t60_tree_last_report_path,
        )

        last = load_t60_tree_last_report()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": t60_tree_last_report_path(),
                "live_hook": False,
                "backtest_hook": False,
                "head": "y_t60_tree",
                "note": "尚无 ŷ_τ60_tree；POST /api/quant/t60-tree",
            }
        out = dict(last)
        out["exists"] = True
        out["path"] = t60_tree_last_report_path()
        out["live_hook"] = False
        out["backtest_hook"] = False
        out.setdefault("head", "y_t60_tree")
        return out

    def run_t75_tree_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        tau_hm: Optional[str] = None,
        holdout_trading_days: int = 20,
        backend: Optional[str] = None,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
    ) -> Dict[str, Any]:
        """ŷ_τ75_tree 影子头：同面板 Holdout vs Ridge。只写 last_report，不进 live / 回测。"""
        import time

        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.t75_tree import (
            fit_t75_tree_report,
            persist_t75_tree_model,
            save_t75_tree_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ75_tree",
                "task": "t75_tree",
                "head": "y_t75_tree",
                "live_hook": False,
                "backtest_hook": False,
            }

        live_hm = str(tau_hm or "10:30").strip() or "10:30"
        if live_hm.lower() in ("", "open"):
            live_hm = "10:30"
        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        t_bars0 = time.perf_counter()
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("t75 tree minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)
        bars_s = round(time.perf_counter() - t_bars0, 2)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "t75_tree",
                "head": "y_t75_tree",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
                "live_hook": False,
                "backtest_hook": False,
            }

        report = fit_t75_tree_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            tau_hm=live_hm,
            holdout_trading_days=holdout_trading_days,
            backend=backend,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_hit"] = minute_hit
        report["minute_cache_universe"] = len(codes)
        report["minute_cache_only"] = True
        timing = dict(report.get("timing") or {}) if isinstance(report.get("timing"), dict) else {}
        timing["bars_s"] = bars_s
        fit_s = timing.get("fit_s")
        try:
            total_s = bars_s + (float(fit_s) if fit_s is not None else 0.0)
        except (TypeError, ValueError):
            total_s = bars_s
        timing["total_s"] = round(float(total_s), 2)
        report["timing"] = timing
        report["live_hook"] = False
        report["backtest_hook"] = True
        if report.get("success"):
            save_t75_tree_last_report(report)
            saved = persist_t75_tree_model(
                report,
                note=f"t75_tree fit auto-persist for backtest",
                force=True,
            )
            report["persisted"] = saved
            if saved.get("path"):
                report["model_path"] = saved["path"]
        else:
            report["persisted"] = {
                "success": False,
                "skipped": True,
                "reason": "fit_failed",
            }
        return report

    def get_t75_tree_last_report(self) -> Dict[str, Any]:
        from core.research.t75_tree import (
            load_t75_tree_last_report,
            t75_tree_last_report_path,
        )

        last = load_t75_tree_last_report()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": t75_tree_last_report_path(),
                "live_hook": False,
                "backtest_hook": False,
                "head": "y_t75_tree",
                "note": "尚无 ŷ_τ75_tree；POST /api/quant/t75-tree",
            }
        out = dict(last)
        out["exists"] = True
        out["path"] = t75_tree_last_report_path()
        out["live_hook"] = False
        out["backtest_hook"] = False
        out.setdefault("head", "y_t75_tree")
        return out

    def run_t90_tree_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = WATCHING_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        tau_hm: Optional[str] = None,
        holdout_trading_days: int = 20,
        backend: Optional[str] = None,
        minute_period: str = "5",
        minute_lookback_days: int = 150,
    ) -> Dict[str, Any]:
        """ŷ_τ90_tree 影子头：同面板 Holdout vs Ridge。只写 last_report，不进 live / 回测。"""
        import time

        from core.data.facade import bars_and_source
        from core.watching.store import WATCHING_MAX_SIZE, read_watching
        from core.research.t90_tree import (
            fit_t90_tree_report,
            persist_t90_tree_model,
            save_t90_tree_last_report,
        )

        uni = read_watching()
        pool = [
            str(c).strip()
            for c in (uni.get("watchlist") or [])
            if str(c).strip()
        ]
        cap = max(2, min(int(WATCHING_MAX_SIZE), 500))
        limit = max(2, min(int(watching_limit or cap), cap))
        codes = pool[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τ90_tree",
                "task": "t90_tree",
                "head": "y_t90_tree",
                "live_hook": False,
                "backtest_hook": False,
            }

        live_hm = str(tau_hm or "10:30").strip() or "10:30"
        if live_hm.lower() in ("", "open"):
            live_hm = "10:30"
        period = str(minute_period or "5").strip() or "5"
        mlook = max(20, min(int(minute_lookback_days or 90), 240))
        stock_bars: List[Dict[str, Any]] = []
        minute_hit = 0
        minute_codes_miss = 0
        t_bars0 = time.perf_counter()
        for code in codes:
            bars, _src = bars_and_source(code, limit=lookback + 40)
            if not bars:
                continue
            row: Dict[str, Any] = {"code": str(code), "bars": bars}
            try:
                from core.ports.market import resolve_market_code
                from core.store import load_minute_cache

                mkt, pure = resolve_market_code(str(code))
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or str(code),
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        row["minute_bars"] = mb
                        minute_hit += 1
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except Exception:  # noqa: BLE001
                logger.debug("t90 tree minute cache miss for %s", code, exc_info=True)
                minute_codes_miss += 1
            stock_bars.append(row)
        bars_s = round(time.perf_counter() - t_bars0, 2)

        if minute_hit < 1:
            return {
                "success": False,
                "error": (
                    f"无本地分钟缓存（period={period} · 池 {len(codes)} 只）；"
                    "请先「强更 5m」，拟合不再拉远端"
                ),
                "task": "t90_tree",
                "head": "y_t90_tree",
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(pool),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
                "live_hook": False,
                "backtest_hook": False,
            }

        report = fit_t90_tree_report(
            stock_bars,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            tau_hm=live_hm,
            holdout_trading_days=holdout_trading_days,
            backend=backend,
        )
        report["watching_limit"] = limit
        report["watching_pool_size"] = len(pool)
        report["lookback"] = lookback
        report["minute_period"] = period
        report["minute_lookback_days"] = mlook
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_hit"] = minute_hit
        report["minute_cache_universe"] = len(codes)
        report["minute_cache_only"] = True
        timing = dict(report.get("timing") or {}) if isinstance(report.get("timing"), dict) else {}
        timing["bars_s"] = bars_s
        fit_s = timing.get("fit_s")
        try:
            total_s = bars_s + (float(fit_s) if fit_s is not None else 0.0)
        except (TypeError, ValueError):
            total_s = bars_s
        timing["total_s"] = round(float(total_s), 2)
        report["timing"] = timing
        report["live_hook"] = False
        report["backtest_hook"] = True
        if report.get("success"):
            save_t90_tree_last_report(report)
            saved = persist_t90_tree_model(
                report,
                note=f"t90_tree fit auto-persist for backtest",
                force=True,
            )
            report["persisted"] = saved
            if saved.get("path"):
                report["model_path"] = saved["path"]
        else:
            report["persisted"] = {
                "success": False,
                "skipped": True,
                "reason": "fit_failed",
            }
        return report

    def get_t90_tree_last_report(self) -> Dict[str, Any]:
        from core.research.t90_tree import (
            load_t90_tree_last_report,
            t90_tree_last_report_path,
        )

        last = load_t90_tree_last_report()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": t90_tree_last_report_path(),
                "live_hook": False,
                "backtest_hook": False,
                "head": "y_t90_tree",
                "note": "尚无 ŷ_τ90_tree；POST /api/quant/t90-tree",
            }
        out = dict(last)
        out["exists"] = True
        out["path"] = t90_tree_last_report_path()
        out["live_hook"] = False
        out["backtest_hook"] = False
        out.setdefault("head", "y_t90_tree")
        return out

    def run_factor_ols_pool_experiment(
        self,
        *,
        lookback: int = 120,
        horizon_days: int = 3,
        watching_limit: int = 8,
        ridge_lambda: float = 0.0,
    ) -> Dict[str, Any]:
        """研究池多票堆叠时序 OLS（显式触发；不写 config）。"""
        from core.data.facade import bars_and_source_research as bars_and_source
        from core.data.facade import get_quote, index_bars_and_source
        from core.ports.market import default_benchmark, resolve_market_code
        from core.watching.store import read_watching
        from quant.research.factor_ols import compute_factor_ols_pooled_report

        uni = read_watching()
        codes = list(uni.get("watchlist") or [])
        limit = max(2, min(int(watching_limit or 8), 20))
        codes = codes[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑池内 OLS",
                "task": "factor_ols_pool",
                "mode": "watching_pooled",
                "stock_count": len(codes),
            }

        panels: List[Dict[str, Any]] = []
        for code in codes:
            quote = get_quote(code)
            sym = quote.get("stock_code") if quote.get("success") else code
            bars, src = bars_and_source(code, limit=lookback + 35)
            if not bars and quote.get("success"):
                bars, src = bars_and_source(sym, limit=lookback + 35)
            if not bars:
                panels.append({"code": str(sym), "bars": []})
                continue
            market, _ = resolve_market_code(code)
            index_bars, _ = index_bars_and_source(
                default_benchmark(market), limit=lookback + 35
            )
            fundamentals = self._experiment_fundamentals(code, sym)
            panels.append(
                {
                    "code": str(sym),
                    "bars": bars,
                    "index_bars": index_bars or None,
                    "fundamentals": fundamentals,
                    "data_source": src,
                }
            )

        report = compute_factor_ols_pooled_report(
            panels, horizon_days=horizon_days, ridge_lambda=ridge_lambda
        )
        report["task"] = "factor_ols_pool"
        report["lookback"] = lookback
        report["watching_limit"] = limit
        return report

    def run_factor_ols_cluster_experiment(self, **kwargs: Any) -> Dict[str, Any]:
        """分组 OLS 已退役。"""
        _ = kwargs
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def cluster_bars_status(self, *, watching_limit: int = WATCHING_MAX_SIZE) -> Dict[str, Any]:
        """观察池日线末 bar 覆盖（研究枢纽 UI）。"""
        from quant.research.cluster_bars_status import build_cluster_bars_status

        return build_cluster_bars_status(watching_limit=watching_limit)

    def cluster_bars_integrity(
        self, *, watching_limit: int = WATCHING_MAX_SIZE, days: int = 22
    ) -> Dict[str, Any]:
        """观察池日线逐日是否在仓（格子图，只读）。"""
        from quant.research.bars_integrity import build_daily_integrity

        return build_daily_integrity(watching_limit=watching_limit, days=days)

    def cluster_minute_integrity(
        self, *, watching_limit: int = WATCHING_MAX_SIZE, days: int = 22
    ) -> Dict[str, Any]:
        """观察池 5 分钟逐日头/尾是否齐（格子图，只读）。"""
        from quant.research.bars_integrity import build_minute_integrity

        return build_minute_integrity(watching_limit=watching_limit, days=days)

    def cluster_minute_day_slots(self, code: str, date: str) -> Dict[str, Any]:
        """单票单日 48 根 5 分钟是否在仓。"""
        from quant.research.bars_integrity import build_minute_day_slots

        return build_minute_day_slots(code, date)

    def run_cluster_bars_refresh(
        self,
        *,
        watching_limit: int = WATCHING_MAX_SIZE,
        lookback: int = 80,
        mode: str = "topup",
        progress_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """同步：更新观察池日线（``mode=topup|full``）。"""
        from quant.research.cluster_bars_status import refresh_cluster_bars_only

        return refresh_cluster_bars_only(
            watching_limit=watching_limit,
            lookback=lookback,
            mode=mode,
            progress_cb=progress_cb,
        )

    def start_cluster_bars_refresh_job(
        self,
        *,
        watching_limit: int = WATCHING_MAX_SIZE,
        lookback: int = 80,
        mode: str = "topup",
    ) -> Dict[str, Any]:
        """后台 Job：仅更新日线；轮询 ``GET /api/jobs/cluster-bars-refresh``。"""
        import threading

        from core.job_progress import cluster_bars_refresh_job
        from quant.research.watching_universe import clamp_watching_limit

        cluster_bars_refresh_job.reclaim_if_stale()
        if cluster_bars_refresh_job.is_running():
            return {
                "ok": True,
                "success": True,
                "background": True,
                "reused": True,
                "job": cluster_bars_refresh_job.get(),
            }

        watch_limit = clamp_watching_limit(watching_limit or WATCHING_MAX_SIZE, WATCHING_MAX_SIZE)
        mode_s = str(mode or "topup").strip().lower()
        if mode_s not in ("full", "topup"):
            mode_s = "topup"
        try:
            from core.watching.store import read_watching

            n_watch_all = len(list((read_watching() or {}).get("watchlist") or []))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
            n_watch_all = watch_limit
        n_watch = min(n_watch_all, watch_limit) if n_watch_all else watch_limit
        # 进度分母=观察池真实票数（与 5m Job 一致；不再 +5 垫高）
        job_total = max(1, n_watch)
        phase = "增量补齐日 K…" if mode_s == "topup" else "整窗强更日 K…"

        job_id = cluster_bars_refresh_job.start(
            kind="cluster_bars_refresh",
            total=job_total,
            message="排队中…",
        )

        def _progress(msg: str, cur: int = 0, tot: int = 0) -> None:
            t = max(1, int(tot or n_watch or 1))
            c = max(0, min(t, int(cur or 0)))
            cluster_bars_refresh_job.update(
                current=c,
                total=t,
                message=str(msg or phase),
                job_id=job_id,
            )

        def _worker() -> None:
            stop_hb = threading.Event()

            def _heartbeat() -> None:
                while not stop_hb.wait(8.0):
                    if not cluster_bars_refresh_job.touch(job_id=job_id):
                        return

            hb = threading.Thread(
                target=_heartbeat, name=f"cluster-bars-hb-{job_id}", daemon=True
            )
            hb.start()
            try:
                if cluster_bars_refresh_job.is_cancel_requested():
                    cluster_bars_refresh_job.finish(error="已取消", job_id=job_id)
                    return
                result = self.run_cluster_bars_refresh(
                    watching_limit=watch_limit,
                    lookback=lookback,
                    mode=mode_s,
                    progress_cb=_progress,
                )
                if cluster_bars_refresh_job.is_cancel_requested():
                    cluster_bars_refresh_job.finish(error="已取消", job_id=job_id)
                    return
                if not result.get("success"):
                    cluster_bars_refresh_job.finish(
                        error=str(result.get("error") or "日线更新失败"),
                        result=result,
                        job_id=job_id,
                    )
                    return
                cluster_bars_refresh_job.finish(result=result, job_id=job_id)
            except Exception as e:
                logger.exception("unexpected error in cluster_bars_refresh worker")
                cluster_bars_refresh_job.finish(error=str(e), job_id=job_id)
            finally:
                stop_hb.set()

        threading.Thread(
            target=_worker, name=f"cluster-bars-{job_id}", daemon=True
        ).start()
        return {
            "ok": True,
            "success": True,
            "background": True,
            "job": cluster_bars_refresh_job.get(),
        }

    def cluster_minute_status(
        self,
        *,
        watching_limit: int = WATCHING_MAX_SIZE,
        period: str = "5",
        min_span_days: int = 40,
        include_label_portrait: bool = False,
    ) -> Dict[str, Any]:
        """观察池 5m 分钟缓存覆盖（研究枢纽 UI）。"""
        from quant.research.cluster_minute_status import build_cluster_minute_status

        return build_cluster_minute_status(
            watching_limit=watching_limit,
            period=period,
            min_span_days=min_span_days,
            include_label_portrait=include_label_portrait,
        )

    def run_cluster_minute_refresh(
        self,
        *,
        watching_limit: int = WATCHING_MAX_SIZE,
        period: str = "5",
        lookback_days: int = 120,
        mode: str = "full",
        topup_lookback_days: int = 5,
        progress_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """同步：预热观察池 5m 分钟线（``mode=full|topup``）。"""
        from quant.research.cluster_minute_status import refresh_cluster_minute_only

        return refresh_cluster_minute_only(
            watching_limit=watching_limit,
            period=period,
            lookback_days=lookback_days,
            mode=mode,
            topup_lookback_days=topup_lookback_days,
            progress_cb=progress_cb,
        )

    def start_cluster_minute_refresh_job(
        self,
        *,
        watching_limit: int = WATCHING_MAX_SIZE,
        period: str = "5",
        lookback_days: int = 120,
        mode: str = "full",
        topup_lookback_days: int = 5,
    ) -> Dict[str, Any]:
        """后台 Job：预热 5m 分钟线；轮询 ``GET /api/jobs/cluster-minute-refresh``。"""
        import threading

        from core.job_progress import cluster_minute_refresh_job
        from quant.research.watching_universe import clamp_watching_limit

        cluster_minute_refresh_job.reclaim_if_stale()
        if cluster_minute_refresh_job.is_running():
            return {
                "ok": True,
                "success": True,
                "background": True,
                "reused": True,
                "job": cluster_minute_refresh_job.get(),
            }

        watch_limit = clamp_watching_limit(watching_limit or WATCHING_MAX_SIZE, WATCHING_MAX_SIZE)
        mode_s = str(mode or "full").strip().lower()
        if mode_s not in ("full", "topup", "repair"):
            mode_s = "full"
        try:
            from core.watching.store import read_watching

            n_watch_all = len(list((read_watching() or {}).get("watchlist") or []))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
            n_watch_all = watch_limit
        n_watch = min(n_watch_all, watch_limit) if n_watch_all else watch_limit
        job_total = max(1, n_watch)
        phase = {
            "topup": "增量补齐 5m…",
            "repair": "东财补缺 5m…",
        }.get(mode_s, "预热 5m…")

        job_id = cluster_minute_refresh_job.start(
            kind="cluster_minute_refresh",
            total=job_total,
            message="排队中…",
        )

        def _progress(msg: str, cur: int = 0, tot: int = 0) -> None:
            t = max(1, int(tot or n_watch or 1))
            c = max(0, min(t, int(cur or 0)))
            cluster_minute_refresh_job.update(
                current=c,
                total=job_total,
                message=str(msg or phase),
                job_id=job_id,
            )

        def _worker() -> None:
            stop_hb = threading.Event()

            def _heartbeat() -> None:
                while not stop_hb.wait(8.0):
                    if not cluster_minute_refresh_job.touch(job_id=job_id):
                        return

            hb = threading.Thread(
                target=_heartbeat, name=f"cluster-minute-hb-{job_id}", daemon=True
            )
            hb.start()
            try:
                if cluster_minute_refresh_job.is_cancel_requested():
                    cluster_minute_refresh_job.finish(error="已取消", job_id=job_id)
                    return
                result = self.run_cluster_minute_refresh(
                    watching_limit=watch_limit,
                    period=period,
                    lookback_days=lookback_days,
                    mode=mode_s,
                    topup_lookback_days=topup_lookback_days,
                    progress_cb=_progress,
                )
                if cluster_minute_refresh_job.is_cancel_requested():
                    cluster_minute_refresh_job.finish(error="已取消", job_id=job_id)
                    return
                if not result.get("success"):
                    cluster_minute_refresh_job.finish(
                        error=str(result.get("error") or "分钟预热失败"),
                        result=result,
                        job_id=job_id,
                    )
                    return
                cluster_minute_refresh_job.finish(result=result, job_id=job_id)
            except Exception as e:
                logger.exception("unexpected error in cluster_minute_refresh worker")
                cluster_minute_refresh_job.finish(error=str(e), job_id=job_id)
            finally:
                stop_hb.set()

        threading.Thread(
            target=_worker, name=f"cluster-minute-{job_id}", daemon=True
        ).start()
        return {
            "ok": True,
            "success": True,
            "background": True,
            "mode": mode_s,
            "job": cluster_minute_refresh_job.get(),
        }

    def start_factor_ols_cluster_job(self, **kwargs: Any) -> Dict[str, Any]:
        """FH2：分组 OLS Job 已退役。"""
        _ = kwargs
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def run_cluster_multi_score(self, **kwargs: Any) -> Dict[str, Any]:
        """分组多权复打分已退役。"""
        _ = kwargs
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def cluster_live_status(self, **kwargs: Any) -> Dict[str, Any]:
        """分组 live 状态已退役。"""
        _ = kwargs
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
            "mode": "off",
            "enabled": False,
            "active": None,
            "draft": None,
            "light": True,
        }

    def compare_cluster_partition_vs_active(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """分组 promote 预检已退役。"""
        _ = args, kwargs
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def promote_cluster_live(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """分组 promote 已退役。"""
        _ = args, kwargs
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def rollback_cluster_live(self, *, to_version: Optional[int] = None) -> Dict[str, Any]:
        """分组 live 回滚已退役。"""
        _ = to_version
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def set_cluster_live_mode(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """分组 live mode 已退役。"""
        _ = args, kwargs
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def set_cluster_universe_fit_tiers(self, tiers: Any) -> Dict[str, Any]:
        """宇宙拟合档设置已退役。"""
        _ = tiers
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def cluster_live_fit_tiers(self) -> Dict[str, Any]:
        """拟合档查询已退役。"""
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
            "code_fit_tiers": {},
            "fit_tier_counts": {"A": 0, "B": 0, "C": 0},
        }

    def save_cluster_live_draft(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        """分组草稿保存已退役。"""
        _ = artifact
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def refresh_cluster_live_book(self) -> Dict[str, Any]:
        """分池簿刷新已退役。"""
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def rank_cluster_live_pools(self, **kwargs: Any) -> Dict[str, Any]:
        """分池排序已退役。"""
        _ = kwargs
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def apply_cluster_live_shortcut(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """分组 live 一键应用已退役。"""
        _ = args, kwargs
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def preview_cluster_paper_rebalance(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """分池簿纸面调仓已退役。"""
        _ = args, kwargs
        return {
            "success": False,
            "ok": False,
            "error": "cluster_retired",
            "cluster_retired": True,
        }

    def run_factor_cs_ic_experiment(
        self,
        *,
        lookback: int = 120,
        horizon_days: int = 3,
        watching_limit: int = 12,
        min_names: int = 5,
        pit_fundamentals: bool = True,
    ) -> Dict[str, Any]:
        """研究池逐因子日频截面 IC（S1；显式触发，不写 config）。"""
        from core.backtest.factor_cs_ic import compute_factor_cross_section_ic
        from core.data.facade import bars_and_source_research as bars_and_source
        from core.data.facade import get_quote
        from core.watching.store import read_watching

        uni = read_watching()
        codes = list(uni.get("watchlist") or [])
        limit = max(3, min(int(watching_limit or 12), 30))
        codes = codes[:limit]
        if len(codes) < 3:
            return {
                "success": False,
                "ok": False,
                "error": "研究池至少 3 只才可跑因子截面 IC",
                "task": "factor_cs_ic",
                "stock_count": len(codes),
            }

        stock_bars: Dict[str, Any] = {}
        for code in codes:
            quote = get_quote(code)
            sym = quote.get("stock_code") if quote.get("success") else code
            bars, _src = bars_and_source(code, limit=lookback + 35)
            if not bars and quote.get("success"):
                bars, _src = bars_and_source(sym, limit=lookback + 35)
            if bars:
                stock_bars[str(sym)] = bars

        if len(stock_bars) < 3:
            return {
                "success": False,
                "ok": False,
                "error": "有效日线不足 3 只",
                "task": "factor_cs_ic",
                "stock_count": len(stock_bars),
            }

        report = compute_factor_cross_section_ic(
            stock_bars,
            horizon_days=horizon_days,
            min_names=min_names,
            pit_fundamentals=pit_fundamentals,
        )
        report["task"] = "factor_cs_ic"
        report["lookback"] = lookback
        report["watching_limit"] = limit
        report["codes"] = list(stock_bars.keys())
        return report

    def suggest_weights(self, code: str = "茅台", **kwargs: Any) -> Dict[str, Any]:
        from core.signal.weight_suggest import format_weight_config_diff, suggest_weights_from_ic

        lookback = int(kwargs.get("lookback") or 120)
        horizon_days = int(kwargs.get("horizon_days") or 3)
        use_cs_ic = bool(kwargs.get("use_cs_ic", True))
        watching_limit = int(kwargs.get("watching_limit") or 12)

        ic_mode = "single"
        exp: Dict[str, Any]
        cs_ic: Optional[Dict[str, Any]] = None
        if use_cs_ic:
            cs_ic = self.run_factor_cs_ic_experiment(
                lookback=lookback,
                horizon_days=horizon_days,
                watching_limit=watching_limit,
            )
            if cs_ic.get("success") and (cs_ic.get("factors") or []):
                exp = cs_ic
                ic_mode = "cs_ic"
            else:
                exp = self.run_factor_experiment(
                    code, lookback=lookback, horizon_days=horizon_days
                )
                ic_mode = "single"
        else:
            exp = self.run_factor_experiment(
                code, lookback=lookback, horizon_days=horizon_days
            )

        if not exp.get("success"):
            return exp

        ols = self.run_factor_ols_experiment(
            code,
            lookback=lookback,
            horizon_days=horizon_days,
            ridge_lambda=float(kwargs.get("ridge_lambda") or 0.0),
        )
        corr = self.run_factor_corr(codes=None, limit=30, horizon_days=horizon_days)
        suggestion = suggest_weights_from_ic(
            exp,
            ols_report=ols if ols.get("success") else None,
            corr_report=corr if corr.get("success") else None,
            ic_mode=ic_mode,
        )
        run_oos_gate = bool(kwargs.get("run_oos_gate", True))
        oos_gate: Optional[Dict[str, Any]] = None
        if run_oos_gate and suggestion.get("success"):
            from core.signal.weight_oos_gate import evaluate_weight_suggestion_oos

            oos_gate = evaluate_weight_suggestion_oos(
                suggestion.get("current_weights") or {},
                suggestion.get("suggested_weights") or {},
                lookback=min(lookback, 90),
                top_k=int(kwargs.get("oos_top_k") or 3),
                horizon_days=horizon_days,
                watching_limit=min(watching_limit, 10),
                oos_tol_pp=float(kwargs.get("oos_tol_pp") or 1.0),
            )
            suggestion["oos_gate"] = oos_gate
            suggestion["promote_ready"] = bool(
                oos_gate.get("ok") and oos_gate.get("passed") and not oos_gate.get("skipped")
            )
        else:
            suggestion["oos_gate"] = {
                "ok": False,
                "passed": False,
                "skipped": True,
                "reason": "gate_disabled",
            }
            suggestion["promote_ready"] = False

        suggestion["factor_experiment"] = exp
        suggestion["factor_cs_ic"] = cs_ic
        suggestion["factor_ols"] = ols
        suggestion["factor_corr"] = corr
        suggestion["config_diff"] = format_weight_config_diff(suggestion)
        # FS1：趋势族共线提示挂到晋升建议
        try:
            from core.signal.factors.meta.collinearity import trend_family_collinearity

            rows = []
            for it in (corr.get("items") or corr.get("rows") or []):
                if isinstance(it, dict) and (it.get("sub_scores") or it.get("factors")):
                    rows.append(it.get("sub_scores") or it.get("factors"))
            if len(rows) >= 3:
                suggestion["trend_collinearity"] = trend_family_collinearity(rows)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
            pass
        return suggestion

    def run_alt_sentiment_ic(
        self,
        *,
        lookback: int = 80,
        horizon_days: int = 3,
        watching_limit: int = 8,
        pit_fundamentals: bool = True,
    ) -> Dict[str, Any]:
        """FS2：观察池 alt_sentiment as_of TS IC（研究只读；不改 live 闸）。"""
        from core.data.facade import bars_and_source_research as bars_and_source
        from core.data.facade import get_quote
        from core.research.sentiment_ic import summarize_alt_sentiment_ic_pool
        from core.watching.store import read_watching

        uni = read_watching()
        codes = list(uni.get("watchlist") or [])[: max(1, min(int(watching_limit or 8), 20))]
        panels = []
        for code in codes:
            quote = get_quote(code)
            sym = quote.get("stock_code") if quote.get("success") else code
            bars, _src = bars_and_source(code, limit=lookback + 35)
            if not bars and quote.get("success"):
                bars, _src = bars_and_source(sym, limit=lookback + 35)
            if bars:
                panels.append({"code": str(sym), "bars": bars})
        out = summarize_alt_sentiment_ic_pool(
            panels,
            horizon_days=horizon_days,
            pit_fundamentals=pit_fundamentals,
        )
        out["task"] = "alt_sentiment_ic"
        out["lookback"] = lookback
        return out

    def run_factor_corr(
        self,
        *,
        codes: Optional[List[str]] = None,
        limit: int = 30,
        horizon_days: int = 3,
    ) -> Dict[str, Any]:
        """观察池/候选截面 sub_scores 相关矩阵（研究只读）。"""
        from core.signal.factors.meta.corr import compute_factor_corr_matrix
        from core.signal.service import get_default_signal_service

        ranked = get_default_signal_service().rank_cross_section(
            codes,
            horizon_days=horizon_days,
            limit=max(3, min(int(limit or 30), 50)),
            min_score=0.0,
        ).as_dict()
        if not ranked.get("success"):
            return {
                "success": False,
                "error": ranked.get("error") or "横截面失败",
                "task": "factor_corr",
            }
        items = ranked.get("ranking") or []
        from core.signal.config import load_signal_config
        from core.signal.factors.meta.corr import redundancy_warnings_from_corr

        report = compute_factor_corr_matrix(items)
        cfg = load_signal_config()
        if report.get("success"):
            report["redundancy_warnings"] = redundancy_warnings_from_corr(
                report,
                factor_groups=cfg.get("factor_groups") or {},
                weights=cfg.get("weights") or {},
            )
        report["task"] = "factor_corr"
        report["ranking_count"] = len(items)
        report["neutralization"] = ranked.get("neutralization")
        return report

    def suggest_thresholds(
        self,
        code: str = "茅台",
        *,
        lookback: int = 120,
        use_watching: bool = False,
        watching_limit: int = 5,
    ) -> Dict[str, Any]:
        from core.backtest.engine import scan_signal_parameters_oos
        from core.data.facade import bars_and_source_research as bars_and_source
        from core.data.facade import get_quote
        from core.signal.threshold_suggest import (
            format_threshold_config_diff,
            suggest_stance_thresholds_from_oos,
            suggest_stance_thresholds_from_watching_oos,
        )
        from core.watching.store import read_watching

        if use_watching:
            try:
                uni = read_watching()
                codes = uni.get("watchlist") or []
            except FileNotFoundError:
                codes = []
            if len(codes) < 2:
                return {"success": False, "error": "watching watchlist 不足，无法聚合 OOS"}
            suggestion = suggest_stance_thresholds_from_watching_oos(
                codes,
                lookback=lookback,
                max_stocks=watching_limit,
            )
            if suggestion.get("success"):
                suggestion["config_diff"] = format_threshold_config_diff(suggestion)
            return suggestion

        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        bars, src = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = bars_and_source(sym, limit=lookback + 35)
        if not bars or len(bars) < 50:
            return {"success": False, "error": f"无法获取足够日线: {code}"}

        from core.signal.config import get_stance_thresholds, load_signal_config
        from core.signal.threshold_suggest import scan_yhat_wait_oos

        base = get_stance_thresholds(load_signal_config())
        predicted = True
        try:
            predicted = float((base or {}).get("avoid", 0)) < 10.0
        except (TypeError, ValueError):
            predicted = True

        if predicted:
            # 分组 return_model 已退役；单票 ŷ 扫描用全局模型 / None
            oos = scan_yhat_wait_oos(bars, model=None, horizon_days=3)
        else:
            oos = scan_signal_parameters_oos(
                bars,
                min_scores=[45, 50, 55, 60, 65],
                horizon_days_list=[2, 3],
            )
            if oos.get("success"):
                oos = dict(oos)
                oos["score_scale"] = "heuristic_0_100"

        suggestion = suggest_stance_thresholds_from_oos(oos)
        suggestion["stock_code"] = sym
        suggestion["data_source"] = src
        suggestion["mode"] = "single_stock_oos"
        suggestion["oos_scan"] = oos
        suggestion["config_diff"] = format_threshold_config_diff(suggestion)
        return suggestion


# ---------- P1：分组报告 24h 缓存（watchlist+参数指纹未变即复用）----------


def _usable_stock_name(code: str, name: Any) -> str:
    """拒绝「名=代码」伪名，避免探针下拉被污染。"""
    c = str(code or "").strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    nm = str(name or "").strip().replace(" ", "")
    if not c or not nm:
        return ""
    if nm == c or (nm.isdigit() and len(nm) == 6):
        return ""
    return nm


def _enrich_cluster_name_by_code(report: Dict[str, Any]) -> Dict[str, Any]:
    """用观察池落盘名补齐 / 覆盖报告 name_by_code，并回填 ranking 伪名。"""
    if not isinstance(report, dict):
        return report
    name_by_code: Dict[str, str] = {}
    for raw, nm0 in (report.get("name_by_code") or {}).items():
        c = str(raw or "").strip()
        nm = _usable_stock_name(c, nm0)
        if c and nm:
            name_by_code[c] = nm
            bare = c.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
            if bare and bare != c:
                name_by_code.setdefault(bare, nm)
    try:
        from core.watching.store import read_watching, watchlist_names_for

        data = read_watching() or {}
        wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
        names = watchlist_names_for(data) if wl else []
        for i, code in enumerate(wl):
            nm = _usable_stock_name(code, names[i] if i < len(names) else "")
            if not nm:
                continue
            name_by_code[code] = nm
            bare = code.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
            if bare and bare != code:
                name_by_code[bare] = nm
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        logger.debug("enrich cluster names from watching failed", exc_info=True)
    report["name_by_code"] = name_by_code
    for cl in report.get("clusters") or []:
        if not isinstance(cl, dict):
            continue
        for key in ("group_ranking", "ranking", "predicted_ranking"):
            rows = cl.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("stock_code") or row.get("code") or "").strip()
                if not code:
                    continue
                bare = code.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
                true_nm = name_by_code.get(code) or name_by_code.get(bare) or ""
                if true_nm and not _usable_stock_name(code, row.get("stock_name")):
                    row["stock_name"] = true_nm
    return report


# 选 k / 拆组算法版本。改分区逻辑时必须 bump，否则 refresh_bars=False 会命中 24h 旧分区。
CLUSTER_CACHE_ALGO_VERSION = "v5-eod-holdout-beta"


def _cluster_cache_fingerprint(
    watchlist: List[Any],
    *,
    lookback: int,
    horizon_days: int,
    holdout_trading_days: int,
    watching_limit: int,
    n_clusters: Optional[int],
    ridge_lambda: float,
    pit_fundamentals: bool,
    l2_normalize_betas: Optional[bool],
    beta_scale: str,
    cluster_method: str,
    cluster_linkage: str,
    within_dist_quantile: float,
    run_oos_gate: bool,
    oos_tol_pp: float,
    run_group_score: bool,
    run_pool_merge: bool,
    top_n_per_group: int,
    respect_regime: bool,
    select_ridge: bool,
    collinearity_policy: str,
) -> str:
    """watchlist 代码（排序）+ 关键参数 + 算法版本 → 稳定指纹；任一变化即视为需重算。"""
    import hashlib

    codes = []
    for item in watchlist or []:
        if isinstance(item, dict):
            c = str(item.get("code") or item.get("stock_code") or "").strip()
        else:
            c = str(item or "").strip()
        if c:
            codes.append(c)
    codes = sorted(set(codes))
    parts = [
        f"codes={','.join(codes)}",
        f"lb={int(lookback)}",
        f"hz={int(horizon_days)}",
        f"hold={int(holdout_trading_days)}",
        f"wlim={int(watching_limit)}",
        f"k={n_clusters if n_clusters is not None else 'auto'}",
        f"ridge={float(ridge_lambda):.4f}",
        f"pit={int(bool(pit_fundamentals))}",
        f"l2n={l2_normalize_betas}",
        f"bscale={beta_scale}",
        f"cmethod={cluster_method}",
        f"clink={cluster_linkage}",
        f"wdq={float(within_dist_quantile):.3f}",
        f"oos={int(bool(run_oos_gate))}",
        f"oostol={float(oos_tol_pp):.3f}",
        f"grp={int(bool(run_group_score))}",
        f"pool={int(bool(run_pool_merge))}",
        f"topn={int(top_n_per_group)}",
        f"regime={int(bool(respect_regime))}",
        f"sridge={int(bool(select_ridge))}",
        f"colpol={collinearity_policy}",
        f"algo={CLUSTER_CACHE_ALGO_VERSION}",
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cluster_cache_age_hours(report: Dict[str, Any]) -> Optional[float]:
    """从缓存报告里的 created_at / cached_at 估算年龄（小时）。"""
    from datetime import datetime, timezone

    ts = report.get("cache_created_at") or report.get("created_at")
    if not ts:
        return None
    try:
        if isinstance(ts, str):
            ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
        elif isinstance(ts, datetime):
            dt = ts
        else:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        return round(max(0.0, age), 2)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        return None


def _draft_saved_at_iso() -> Optional[str]:
    """草稿落盘时间；用于判断指纹缓存是否已过期于最新草稿。"""
    import json
    import os

    try:
        from core.paths import CLUSTER_WEIGHTS_DRAFT_PATH
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        return None
    path = CLUSTER_WEIGHTS_DRAFT_PATH
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        return None
    if not isinstance(doc, dict):
        return None
    return doc.get("saved_at") or doc.get("created_at")


def _iso_newer(a: Optional[str], b: Optional[str]) -> bool:
    """True if ``a`` is strictly newer than ``b`` (UTC ISO). Missing → False."""
    if not a or not b:
        return False
    try:
        from datetime import datetime

        def _p(s: str) -> datetime:
            s = str(s).replace("Z", "+00:00")
            return datetime.fromisoformat(s)

        return _p(a) > _p(b)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        return False


def _save_last_cluster_report(report: Dict[str, Any]) -> None:
    """无论指纹缓存是否启用，都落「最近一次成功分组」便于刷新恢复。"""
    from datetime import datetime, timezone

    from core.io_atomic import atomic_write_json
    from core.paths import CLUSTER_LAST_REPORT_PATH

    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("clusters"), list) or not report.get("clusters"):
        return
    try:
        import copy

        report_copy = copy.deepcopy(report)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        report_copy = dict(report)
    for k in ("progress_cb", "_progress_cb"):
        report_copy.pop(k, None)
    try:
        from core.signal.score_display import json_safe

        report_copy = json_safe(report_copy)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        pass
    try:
        from core.signal.factors.meta.taxonomy import strip_removed_factors_from_cluster_report

        strip_removed_factors_from_cluster_report(report_copy)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        pass
    doc = {
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report": report_copy,
    }
    try:
        atomic_write_json(CLUSTER_LAST_REPORT_PATH, doc)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        pass


def _oos_summary_from_clusters(clusters: list) -> Dict[str, Any]:
    passed = failed = skipped = 0
    for cl in clusters:
        if not isinstance(cl, dict):
            continue
        gate = cl.get("oos_gate") if isinstance(cl.get("oos_gate"), dict) else {}
        if gate.get("skipped"):
            skipped += 1
        elif gate.get("ok") and gate.get("passed"):
            passed += 1
        elif gate:
            failed += 1
        elif cl.get("oos_passed") is True:
            passed += 1
        elif cl.get("oos_passed") is False:
            failed += 1
        else:
            skipped += 1
    return {
        "run": True,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "n": len(clusters),
    }


def _report_from_cluster_draft() -> Optional[Dict[str, Any]]:
    """分组草稿恢复已退役。"""
    return None


def _load_latest_cluster_report() -> Optional[Dict[str, Any]]:
    """分组报告水合已退役。"""
    return None


def hydrate_ols_clusters_job_result(
    result: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """分组 Job 水合已退役；原样返回。"""
    return result


def _load_cluster_cache(
    fingerprint: str, *, max_age_hours: int = 24
) -> Optional[Dict[str, Any]]:
    """读缓存：指纹匹配 + 年龄未超限 → 返回报告（深拷贝避免被调用方污染）。"""
    import json
    import os

    from core.paths import CLUSTER_REPORT_CACHE_PATH

    if not fingerprint or not os.path.isfile(CLUSTER_REPORT_CACHE_PATH):
        return None
    try:
        with open(CLUSTER_REPORT_CACHE_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        return None
    if not isinstance(doc, dict):
        return None
    if str(doc.get("fingerprint") or "") != str(fingerprint):
        return None
    report = doc.get("report")
    if not isinstance(report, dict) or not report.get("success"):
        return None
    # 草稿更新过且指纹缓存更旧 → 作废（常见于 refresh_bars 跑完只写 draft）
    cache_ts = doc.get("cache_created_at") or doc.get("created_at")
    if _iso_newer(_draft_saved_at_iso(), cache_ts):
        return None
    age = _cluster_cache_age_hours(doc) or 0.0
    if age > float(max_age_hours):
        return None
    # 深拷贝避免上层把缓存对象改脏
    try:
        import copy

        report = copy.deepcopy(report)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        pass
    report["cache_created_at"] = doc.get("cache_created_at") or doc.get("created_at")
    try:
        from core.signal.factors.meta.taxonomy import strip_removed_factors_from_cluster_report

        strip_removed_factors_from_cluster_report(report)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        pass
    return report


def _save_cluster_cache(report: Dict[str, Any], fingerprint: str) -> None:
    """落盘缓存：报告 + 指纹 + 时间戳（原子写）。"""
    from datetime import datetime, timezone

    from core.io_atomic import atomic_write_json
    from core.paths import CLUSTER_REPORT_CACHE_PATH

    if not fingerprint or not isinstance(report, dict) or not report.get("success"):
        return
    try:
        import copy

        report_copy = copy.deepcopy(report)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        report_copy = report
    # 去掉进度回调残留字段（不可序列化）
    for k in ("progress_cb", "_progress_cb"):
        report_copy.pop(k, None)
    try:
        from core.signal.factors.meta.taxonomy import strip_removed_factors_from_cluster_report

        strip_removed_factors_from_cluster_report(report_copy)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        pass
    doc = {
        "fingerprint": str(fingerprint),
        "cache_created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report": report_copy,
    }
    try:
        atomic_write_json(CLUSTER_REPORT_CACHE_PATH, doc)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_service_factors.py", exc_info=True)
        pass
