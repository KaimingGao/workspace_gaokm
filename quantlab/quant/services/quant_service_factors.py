"""QuantService · 因子面板 / IC / OLS / 权重与阈值（进阶）；``run_cross_section`` 属 ② 回溯。"""


import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.research.materialize.consumer import consume_materialized
from core.research.task import records_experiment
from core.watching.store import MODEL_FIT_MAX_SIZE, WATCHING_MAX_SIZE

logger = logging.getLogger(__name__)


def _resolve_fit_universe(
    *,
    watching_limit: int,
) -> Dict[str, Any]:
    """解析拟合宇宙；失败时 ``ok=False`` 且带 ``error``。"""
    from core.research_universe import resolve_model_fit_codes

    return resolve_model_fit_codes(watching_limit=watching_limit)


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


def _desk_holdout_n(doc: Optional[Dict[str, Any]]) -> Optional[int]:
    """读文档 Holdout 日数（顶层或 oos）；解析失败返回 None。"""
    if not isinstance(doc, dict):
        return None
    raw = doc.get("holdout_trading_days")
    if raw is None and isinstance(doc.get("oos"), dict):
        raw = doc["oos"].get("holdout_trading_days")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _select_ridge_desk_doc(
    live: Optional[Dict[str, Any]],
    last: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """研究台展示：优先最近拟合报告；执行套仅在拟合标识不落后时覆盖。

    - Holdout / 样本口径不同 → 画 last（刚改 Holdout 重拟合）
    - 口径相同但 last.fitted_at 新于 live → 画 last（刚拟合/启用研究，执行仍是上一轮）
    - 否则画 live（研究已晋级，或仅有执行套）
    - Holdout=0 的 last 是全样本执行重训：有研究/执行槽时不抢研究台顶层
      （因子表靠 suite_research / suite_live）
    """
    last_ok = isinstance(last, dict) and isinstance(last.get("return_model"), dict)
    live_ok = isinstance(live, dict) and isinstance(live.get("return_model"), dict)
    if last_ok:
        if not live_ok:
            return last, True
        last_hold = _desk_holdout_n(last)
        slot_hold = _desk_holdout_n(live)
        # 全样本 last 不抢「有 Holdout 的研究槽」顶层（否则 KPI 被灌成 0 日）
        skip_full_sample_last = (
            last_hold == 0 and slot_hold is not None and slot_hold > 0
        )
        if not skip_full_sample_last:
            if _ridge_desk_key(last) != _ridge_desk_key(live):
                return last, True
            try:
                from core.research.holdout import model_fit_id

                last_fit = str(model_fit_id(last) or "")
                live_fit = str(model_fit_id(live) or "")
            except Exception:  # noqa: BLE001
                last_fit, live_fit = "", ""
            if last_fit and (not live_fit or last_fit > live_fit):
                return last, True
    if live_ok:
        return live, False
    return None, False


def _last_report_path_from_live(live_path: str) -> str:
    """``*_model.json`` → ``*_last_report.json``。"""
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


def _peek_model_stamp(path: str) -> Optional[str]:
    """读落盘模型的拟合时间标识；缺字段才退文件 mtime。"""
    try:
        if not path or not os.path.isfile(path):
            return None
        import json

        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict):
            from core.research.holdout import model_fit_id

            stamp = model_fit_id(doc)
            if stamp:
                return stamp
    except Exception:  # noqa: BLE001
        logger.debug("peek model stamp failed: %s", path, exc_info=True)
    return _file_mtime_iso(path)


def _peek_json_key(path: str, key: str) -> Optional[str]:
    """读落盘 JSON 顶层字符串字段；缺文件/缺键返回 None。"""
    try:
        if not path or not os.path.isfile(path):
            return None
        import json

        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict):
            val = doc.get(key)
            if val:
                return str(val)
    except Exception:  # noqa: BLE001
        logger.debug("peek json key failed: %s %s", path, key, exc_info=True)
    return None


def _suite_pack_from_doc(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """研究/执行切换用：压缩 return_model + oos（无则 None）。

    附带 y_spec / Holdout 日数，供 rank 因子表 KPI 与 y 公式在研究↔执行切换后仍可读。
    """
    if not isinstance(doc, dict):
        return None
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else None
    if rm is None and isinstance(doc.get("return_model_research"), dict):
        rm = doc.get("return_model_research")
    if rm is None and isinstance(doc.get("model"), dict):
        rm = doc.get("model")
    backends = doc.get("backends") if isinstance(doc.get("backends"), dict) else None
    if rm is None and isinstance(backends, dict):
        for key in ("lambdarank", "lightgbm", "ridge"):
            pack = backends.get(key)
            if not isinstance(pack, dict):
                continue
            cand = pack.get("return_model_research") or pack.get("return_model")
            if isinstance(cand, dict):
                rm = cand
                break
    if not isinstance(rm, dict):
        return None
    oos = doc.get("oos") if isinstance(doc.get("oos"), dict) else None
    y_spec = None
    if isinstance(doc.get("y_spec"), dict):
        y_spec = dict(doc["y_spec"])
    elif isinstance(rm.get("y_spec"), dict):
        y_spec = dict(rm["y_spec"])
    return {
        "return_model": dict(rm),
        "oos": dict(oos) if oos else None,
        "fitted_at": doc.get("fitted_at") or rm.get("fitted_at"),
        "promoted_at": doc.get("promoted_at"),
        "backend": doc.get("backend") or doc.get("recommended_backend") or rm.get("backend"),
        "backends": backends,
        "gate_oos_source": doc.get("gate_oos_source"),
        "deploy_full_sample": bool(doc.get("deploy_full_sample")),
        "feature_importance": doc.get("feature_importance")
        if isinstance(doc.get("feature_importance"), list)
        else rm.get("feature_importance"),
        "y_spec": y_spec,
        "n_train_days": doc.get("n_train_days")
        if doc.get("n_train_days") is not None
        else (oos.get("n_train_days") if isinstance(oos, dict) else None),
        "n_test_days": doc.get("n_test_days")
        if doc.get("n_test_days") is not None
        else (oos.get("n_test_days") if isinstance(oos, dict) else None),
        "holdout_trading_days": doc.get("holdout_trading_days")
        if doc.get("holdout_trading_days") is not None
        else (oos.get("holdout_trading_days") if isinstance(oos, dict) else None),
        "tau_end_hm": doc.get("tau_end_hm"),
        "sample_count": doc.get("sample_count")
        if doc.get("sample_count") is not None
        else rm.get("sample_count"),
    }


def _attach_ridge_role_flags(
    out: Dict[str, Any],
    live_path: str,
    *,
    live_present: Optional[bool] = None,
    accept_research: Optional[Any] = None,
    suite_research_doc: Optional[Dict[str, Any]] = None,
    suite_live_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """给 ŷ_* GET/POST 打上执行套 / 研究套是否已落盘及各自拟合时间标识。"""
    from core.research.holdout import research_model_path, research_sidecar_flags

    flags = research_sidecar_flags(
        live_path,
        accept=accept_research,
    )
    out["research_exists"] = bool(flags.get("research_exists"))
    out["research_path"] = flags.get("research_path")
    research_path = str(flags.get("research_path") or research_model_path(live_path) or "")
    research_fit = flags.get("research_fitted_at") or _peek_model_stamp(research_path)
    out["research_fitted_at"] = research_fit
    out["research_promoted_at"] = flags.get("research_promoted_at")
    if live_present is None:
        live_present = bool(live_path and os.path.isfile(live_path))
    out["live_model_present"] = bool(live_present)
    live_fit = _peek_model_stamp(live_path) if live_path and os.path.isfile(live_path) else None
    out["live_fitted_at"] = live_fit
    live_promoted = (
        _peek_json_key(live_path, "promoted_at")
        if live_path and os.path.isfile(live_path)
        else None
    )
    out["live_promoted_at"] = live_promoted
    fitted = out.get("fitted_at")
    if not fitted:
        last_path = _last_report_path_from_live(live_path)
        fitted = _peek_model_stamp(last_path) or _file_mtime_iso(last_path)
    if fitted:
        out["fitted_at"] = fitted
    # 同页研究/执行切换：调用方显式传入双套文档时附带压缩包
    if suite_research_doc is not None:
        pack = _suite_pack_from_doc(suite_research_doc)
        if pack is not None:
            out["suite_research"] = pack
    if suite_live_doc is not None:
        pack_l = _suite_pack_from_doc(suite_live_doc)
        if pack_l is not None:
            out["suite_live"] = pack_l
    return out


def _start_ridge_fit_job(
    *,
    slot: Any,
    kind: str,
    message: str,
    worker_fn: Any,
    watching_limit: int = MODEL_FIT_MAX_SIZE,
) -> Dict[str, Any]:
    """Ridge 拟合入队：立刻返回 ``background=true``，心跳防 5min 误杀。

    ``worker_fn`` 若接受 ``progress_cb`` 关键字，会收到 ``(cur, tot, msg)`` 刷新槽位。
    """
    import inspect
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
        n_watch_all = watching_limit
    cap = max(1, int(watching_limit or WATCHING_MAX_SIZE))
    n_watch = min(n_watch_all, cap) if n_watch_all else cap
    job_total = max(1, n_watch)
    job_id = slot.start(kind=kind, total=job_total, message=message)

    def _progress(cur: int = 0, tot: int = 0, msg: str = "") -> None:
        # 阶段文案常传 tot=1；面板批进度才抬高 total。勿把进度条总长压扁。
        tot_i = max(0, int(tot or 0))
        total = max(job_total, tot_i if tot_i > 1 else job_total)
        slot.update(
            current=max(0, int(cur or 0)),
            total=max(1, total),
            message=str(msg or message),
            job_id=job_id,
        )

    def _job_alive() -> bool:
        """槽仍属本 job 且未协作取消；孤儿 worker 勿再写 last_report。"""
        if slot.is_cancel_requested():
            return False
        snap = slot.get() or {}
        return (
            str(snap.get("id") or "") == str(job_id)
            and str(snap.get("status") or "") == "running"
        )

    def _call_worker() -> Any:
        try:
            params = inspect.signature(worker_fn).parameters
        except (TypeError, ValueError):
            params = {}
        has_varkw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        call_kw: Dict[str, Any] = {}
        if "progress_cb" in params or has_varkw:
            call_kw["progress_cb"] = _progress
        if "job_alive_cb" in params or has_varkw:
            call_kw["job_alive_cb"] = _job_alive
        if call_kw:
            return worker_fn(**call_kw)
        return worker_fn()

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
            result = _call_worker()
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
        except MemoryError:
            logger.exception("out of memory in %s worker", kind)
            slot.finish(
                error=(
                    "内存保护：分配失败，已停止以免网页进程被系统杀掉。"
                    "请缩小研究池，或缩短拟合窗口。"
                ),
                job_id=job_id,
            )
        except Exception as e:
            logger.exception("unexpected error in %s worker", kind)
            slot.finish(error=str(e).strip() or "拟合失败", job_id=job_id)
        finally:
            stop_hb.set()
            try:
                from core.research.horizon_l1 import release_allocator_cache

                release_allocator_cache()
            except Exception:  # noqa: BLE001
                logger.debug("%s release allocator cache failed", kind, exc_info=True)

    threading.Thread(target=_worker, name=f"{kind}-{job_id}", daemon=True).start()
    return {
        "ok": True,
        "success": True,
        "background": True,
        "job": slot.get(),
    }


_EXPR_CS_LIMIT = 30
_EXPR_CS_MIN = 5


def _expr_pool_rank_ic(
    expr: str,
    *,
    horizon_days: int,
    lookback: int,
    focus_code: str,
    focus_bars: List[dict],
) -> Dict[str, Any]:
    """观察池前 30 只的日度截面 Rank IC。失败时不挡住本票时序结果。"""
    out: Dict[str, Any] = {
        "cs_rank_ic": None,
        "cs_ir": None,
        "cs_days": 0,
        "cs_names": 0,
        "cs_positive_rate": None,
        "cs_note": "",
    }
    try:
        from core.data.service import get_research_service
        from core.signal.factors.expr import cross_section_rank_ic
        from core.watching.store import read_watching

        focus = str(focus_code or "").strip()
        codes: List[str] = []
        seen = set()
        watch: List[str] = []
        try:
            uni = read_watching()
            watch = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
        except Exception:
            logger.debug("expr rank ic: watching unavailable", exc_info=True)
        for c in ([focus] if focus else []) + watch:
            if not c or c in seen:
                continue
            seen.add(c)
            codes.append(c)
            if len(codes) >= _EXPR_CS_LIMIT:
                break
        bars_by: Dict[str, List[dict]] = {}
        if focus and focus_bars:
            bars_by[focus] = list(focus_bars)
        others = [c for c in codes if c != focus]
        if others:
            packs = get_research_service().get_bars_batch(others, limit=int(lookback) + 35)
            for code, pack in zip(others, packs):
                bars = pack.get("bars") if isinstance(pack, dict) else None
                if isinstance(bars, list) and len(bars) >= int(horizon_days) + 5:
                    bars_by[code] = bars
        if len(bars_by) < _EXPR_CS_MIN:
            out["cs_note"] = f"观察池有效标的 {len(bars_by)} 只，不足 {_EXPR_CS_MIN}，未计算截面 Rank IC"
            out["cs_names"] = len(bars_by)
            return out
        stats = cross_section_rank_ic(
            expr, bars_by, int(horizon_days), min_names=_EXPR_CS_MIN,
        )
        if stats.get("cs_rank_ic") is None:
            out["cs_note"] = "截面样本不足，未算出 Rank IC"
            out["cs_names"] = len(bars_by)
            return out
        ir = stats.get("cs_ir")
        pos = stats.get("cs_positive_rate")
        out.update({
            "cs_rank_ic": round(float(stats["cs_rank_ic"]), 4),
            "cs_ir": round(float(ir), 4) if ir is not None else None,
            "cs_days": int(stats.get("cs_days") or 0),
            "cs_names": int(stats.get("cs_names") or 0),
            "cs_positive_rate": round(float(pos) * 100, 1) if pos is not None else None,
            "cs_note": f"观察池 {len(bars_by)} 只 · 日度 Spearman 均值",
            "cs_ic_path": [
                {
                    "date": str(p.get("date") or "")[:10],
                    "value": round(float(p["value"]), 4),
                }
                for p in (stats.get("cs_ic_path") or [])
                if p.get("date") and p.get("value") is not None
            ],
        })
        return out
    except Exception as e:
        logger.warning("expr cross-section ic skipped: %s", e)
        out["cs_note"] = "截面 Rank IC 未计算"
        return out


class QuantFactorMixin:
    def list_factors(self) -> Dict[str, Any]:
        from core.signal.factors.meta.panel import build_factor_panel

        return build_factor_panel()

    def eval_factor_expr(
        self,
        code: str,
        expr: str,
        *,
        lookback: int = 120,
        horizon_days: int = 5,
    ) -> Dict[str, Any]:
        """DSL 表达式因子求值：本票时序值 + 时序 IC，以及观察池截面 Rank IC。"""
        import numpy as np
        from core.data.facade import bars_and_source_research as bars_and_source
        from core.data.facade import get_quote
        from core.signal.factors.expr import eval_expr_series, parse_expr

        # 先校验表达式
        try:
            parse_expr(expr)
        except ValueError as e:
            return {"success": False, "error": f"表达式语法错误: {e}"}

        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        bars, src_name = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src_name = bars_and_source(sym, limit=lookback + 35)
        if not bars:
            return {"success": False, "error": f"无法获取 {code} 日线"}

        n = len(bars)
        series = eval_expr_series(expr, bars)

        # 计算 IC：因子值 vs horizon_days 前瞻收益
        ic_vals = []
        spearman_vals = []
        valid_count = 0
        for i in range(n - horizon_days):
            fv = series[i]
            if fv != fv:  # NaN
                continue
            fwd = bars[i + horizon_days]["close"] / bars[i]["close"] - 1.0
            ic_vals.append((float(fv), float(fwd)))
            valid_count += 1

        ic = None
        rank_ic = None
        if len(ic_vals) >= 5:
            xs = np.array([v[0] for v in ic_vals])
            ys = np.array([v[1] for v in ic_vals])
            if np.std(xs) > 1e-12 and np.std(ys) > 1e-12:
                ic = float(np.corrcoef(xs, ys)[0, 1])
                # Spearman rank IC
                rx = np.argsort(np.argsort(xs)).astype(float)
                ry = np.argsort(np.argsort(ys)).astype(float)
                if np.std(rx) > 1e-12 and np.std(ry) > 1e-12:
                    rank_ic = float(np.corrcoef(rx, ry)[0, 1])

        series_points = []
        for i in range(n):
            v = series[i]
            if v != v:
                continue
            day = str(bars[i].get("date") or "")[:10]
            if not day:
                continue
            series_points.append({"date": day, "value": round(float(v), 6)})
        if len(series_points) > 160:
            series_points = series_points[-160:]
        recent = series_points[-10:]

        cs = _expr_pool_rank_ic(
            expr,
            horizon_days=horizon_days,
            lookback=lookback,
            focus_code=str(sym or code),
            focus_bars=bars,
        )
        return {
            "success": True,
            "expr": expr,
            "code": sym or code,
            "data_source": src_name,
            "sample_count": n,
            "valid_count": valid_count,
            "ic": round(ic, 4) if ic is not None else None,
            "rank_ic": round(rank_ic, 4) if rank_ic is not None else None,
            "horizon_days": horizon_days,
            "last_value": recent[-1]["value"] if recent else None,
            "last_date": recent[-1]["date"] if recent else None,
            "series": series_points,
            "recent": recent,
            **cs,
        }

    def list_experiments(
        self,
        model_type: Optional[str] = None,
        *,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """列出实验追踪器中的实验记录。"""
        from core import experiment_tracker as et

        exps = et.list_experiments(model_type, limit=limit)
        return {
            "success": True,
            "model_type": model_type,
            "count": len(exps),
            "experiments": exps,
        }

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

    @records_experiment("factor_ic")
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
        report["pit_fundamentals"] = True
        return report

    @records_experiment("factor_ols")
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
    ) -> Dict[str, Any]:
        """ŷ 行业残差 on/off 影子对照（不写盘）。"""
        from core.research.yhat_residual_shadow import compare_yhat_residual_shadow
        from core.watching.store import read_watching

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

    @records_experiment("tc_lgb")
    @consume_materialized
    def run_tau_lgb_experiment(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = MODEL_FIT_MAX_SIZE,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        persist: bool = False,
        note: str = "",
        tau_hm: Optional[str] = None,
        tau_end_hm: Optional[str] = None,
        force_promote: bool = False,
        persist_role: str = "live",
        deploy_full_sample: bool = False,
        holdout_trading_days: int = 20,
        cross_section_zscore: bool = True,
        progress_cb: Optional[Any] = None,
        job_alive_cb: Optional[Any] = None,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """观察池 ŷ_τc LightGBM；可选 persist live 模型（``tau_lgb_model*.json``）。

        默认用满观察池。``tau_end_hm`` 优先（09:30…11:00；09:30=0 前缀 open→close）；
        否则 ``tau_hm`` / ``dual_score.enable_minute_tau``（开则训至 11:00 网格）。
        日线与 ŷ_oo 同源（``load_portfolio_stock_bars``）。
        ``job_alive_cb()``：False 时跳过 ``save_tau_last_report``（槽已被接替/取消）。
        """
        from core.research.portfolio_bars import load_portfolio_stock_bars
        from core.signal.dual_score import get_dual_score_cfg
        from core.research.tc_model import (
            _usable_tau_lgb_doc,
            fit_tau_model_report,
            load_tau_last_report,
            load_tau_model,
            persist_tau_model,
            save_tau_last_report,
            tau_model_path,
            tau_promote_gate,
        )

        from core.watching.store import MODEL_FIT_MAX_SIZE

        def _job_alive() -> bool:
            if not callable(job_alive_cb):
                return True
            try:
                return bool(job_alive_cb())
            except Exception:  # noqa: BLE001
                logger.debug("tau_lgb job_alive_cb failed", exc_info=True)
                return False

        def _prog(cur: int, tot: int, msg: str) -> None:
            if not callable(progress_cb):
                return
            try:
                progress_cb(int(cur), int(tot), str(msg or ""))
            except Exception:  # noqa: BLE001
                pass

        _prog(0, 1, "ŷ_τc 解析宇宙…")
        resolved = _resolve_fit_universe(
            watching_limit=watching_limit,
        )
        if not resolved.get("ok", True):
            return {
                "success": False,
                "error": resolved.get("error") or "拟合宇宙解析失败",
                "task": "tc_lgb",
                "universe_source": resolved.get("source"),
            }
        codes = list(resolved.get("codes") or [])
        limit = max(2, min(int(watching_limit or MODEL_FIT_MAX_SIZE), int(MODEL_FIT_MAX_SIZE)))
        codes = codes[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑 ŷ_τc LightGBM",
                "task": "tc_lgb",
            }

        if persist:
            last = load_tau_last_report()
            if last:
                saved = persist_tau_model(
                    last,
                    note=note or "persist last tau report",
                    force=bool(force_promote),
                    role=persist_role,
                    deploy_full_sample=bool(deploy_full_sample),
                )
                out = dict(last)
                out["persisted"] = saved
                out["from_last_report"] = True
                out["promote_gate"] = saved.get("promote_gate") or tau_promote_gate(last)
                if saved.get("promoted_at"):
                    out["promoted_at"] = saved["promoted_at"]
                # 晋级失败时勿把草稿 success 冒充整包成功
                if not saved.get("success"):
                    out["success"] = False
                    out["error"] = saved.get("error") or "启用失败"
                return _attach_ridge_role_flags(
                    out,
                    tau_model_path(),
                    live_present=bool(load_tau_model()),
                    accept_research=_usable_tau_lgb_doc,
                )

        ds = get_dual_score_cfg()
        from core.signal.minute_tau_grid import resolve_tau_train_prefix

        prefix = resolve_tau_train_prefix(
            tau_end_hm=tau_end_hm,
            tau_hm=tau_hm,
            enable_minute_tau=bool(ds.get("enable_minute_tau")),
        )
        if prefix.get("error"):
            return {
                "success": False,
                "error": prefix["error"],
                "task": "tc_lgb",
            }
        use_minute = bool(prefix.get("use_minute"))
        tau_key = str(prefix.get("tau_hm") or "open")
        tau_grid = prefix.get("tau_grid")
        resolved_end = str(prefix.get("tau_end_hm") or "11:00")[:5]

        n_univ = max(1, len(codes))
        _prog(0, n_univ, f"ŷ_τc 拉日线 {len(codes)} 只…")
        stock_bars: List[Dict[str, Any]] = []
        # 5m 不在此整池预载：collect_tau_compact 按批 24 挂载并 pop（压峰值）
        loaded, _failures, _fund = load_portfolio_stock_bars(
            codes,
            lookback=int(lookback or 120),
            fetch_fundamentals=False,
        )
        for code in codes:
            bars = loaded.get(str(code))
            if not bars:
                continue
            stock_bars.append({"code": str(code), "bars": bars})
        _prog(0, max(1, len(stock_bars)), f"ŷ_τc 组面板 0/{len(stock_bars)}")
        report = fit_tau_model_report(
            stock_bars,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            tau_hm=tau_key,
            tau_grid=tau_grid,
            holdout_trading_days=holdout_trading_days,
            cross_section_zscore=bool(cross_section_zscore),
            progress_cb=progress_cb,
        )
        report["watching_limit"] = limit
        report["universe_source"] = resolved.get("source")
        report["watching_pool_size"] = int(
            resolved.get("watching_pool_size") or len(codes)
        )
        report["lookback"] = lookback
        report["tau_end_hm"] = resolved_end
        report["zero_prefix"] = bool(prefix.get("zero_prefix"))
        y_spec = report.get("y_spec")
        if isinstance(y_spec, dict):
            y_spec = dict(y_spec)
            y_spec["tau_end_hm"] = resolved_end
            y_spec["zero_prefix"] = bool(prefix.get("zero_prefix"))
            report["y_spec"] = y_spec
            for mk in ("return_model", "return_model_research"):
                blob = report.get(mk)
                if isinstance(blob, dict) and isinstance(blob.get("y_spec"), dict):
                    ys2 = dict(blob["y_spec"])
                    ys2["tau_end_hm"] = resolved_end
                    ys2["zero_prefix"] = bool(prefix.get("zero_prefix"))
                    blob["y_spec"] = ys2
        if use_minute:
            report["minute_cache_hit"] = int(report.get("minute_attached") or 0)
            report["minute_cache_universe"] = len(codes)
        else:
            report["minute_cache_hit"] = None
            report["minute_cache_universe"] = None
        if not _job_alive():
            report["success"] = False
            report["error"] = "任务已中断或被接替"
            report["orphaned"] = True
            report["persisted"] = {"success": False, "skipped": True, "orphaned": True}
            return _attach_ridge_role_flags(
                report,
                tau_model_path(),
                live_present=bool(load_tau_model()),
                accept_research=_usable_tau_lgb_doc,
            )
        if report.get("success"):
            save_tau_last_report(report)
            if not report.get("promote_gate"):
                report["promote_gate"] = tau_promote_gate(report)
        if persist and report.get("success"):
            saved = persist_tau_model(
                report,
                note=note or "api tc-lgb persist",
                force=bool(force_promote),
                role=persist_role,
            )
            from core.research.holdout import stamp_persist_outcome

            stamp_persist_outcome(report, saved)
            if not report.get("promote_gate"):
                report["promote_gate"] = tau_promote_gate(report)
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_tau_model()
            report["live_model_present"] = bool(live)
            if report.get("success") and not report.get("promote_gate"):
                report["promote_gate"] = tau_promote_gate(report)
        _attach_ridge_role_flags(
            report,
            tau_model_path(),
            live_present=bool(load_tau_model()),
                accept_research=_usable_tau_lgb_doc,
        )
        return report

    def start_tau_lgb_job(
        self,
        *,
        lookback: int = 120,
        watching_limit: int = MODEL_FIT_MAX_SIZE,
        gap_trigger_pct: float = 2.0,
        theme_boost: float = 1.5,
        note: str = "",
        persist_role: str = "live",
        holdout_trading_days: int = 20,
        tau_hm: Optional[str] = None,
        tau_end_hm: Optional[str] = None,
        cross_section_zscore: bool = True,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """后台 ŷ_τc 拟合；轮询 ``GET /api/jobs/tau-lgb``。不写盘。"""
        from core.job_progress import tau_lgb_job

        kwargs = dict(
            lookback=lookback,
            watching_limit=watching_limit,
            gap_trigger_pct=gap_trigger_pct,
            theme_boost=theme_boost,
            persist=False,
            note=note or "",
            persist_role=persist_role,
            holdout_trading_days=holdout_trading_days,
            tau_hm=tau_hm,
            tau_end_hm=tau_end_hm,
            cross_section_zscore=bool(cross_section_zscore),
        )
        return _start_ridge_fit_job(
            slot=tau_lgb_job,
            kind="tau_lgb",
            message="ŷ_τc 拟合中…",
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            worker_fn=lambda progress_cb=None, job_alive_cb=None: self.run_tau_lgb_experiment(
                **kwargs,
                progress_cb=progress_cb,
                job_alive_cb=job_alive_cb,
            ),
        )


    def get_tau_lgb_model(self) -> Dict[str, Any]:
        from core.research.tc_model import (
            _usable_tau_lgb_doc,
            load_tau_last_report,
            load_tau_model,
            tau_model_path,
            tau_promote_gate,
        )

        live = load_tau_model(role="live")
        research = load_tau_model(role="research")
        last = load_tau_last_report()
        if not live and not last and not research:
            return _attach_ridge_role_flags(
                {
                    "success": False,
                    "exists": False,
                    "path": tau_model_path(),
                    "last_report_exists": False,
                    "note": "尚无 ŷ_τc 模型；POST /api/quant/tc-lgb persist=true",
                },
                tau_model_path(),
                live_present=False,
                accept_research=_usable_tau_lgb_doc,
                suite_research_doc=research,
                suite_live_doc=live,
            )

        # 对齐 rank：研究槽优先作 desk 锚；Holdout=0 last 不抢研究套；须用 chosen
        persisted = research or live
        chosen, use_last = _select_ridge_desk_doc(persisted, last)
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
                    "last_report": last,
                }
            )
            return _attach_ridge_role_flags(
                out,
                tau_model_path(),
                live_present=bool(live),
                accept_research=_usable_tau_lgb_doc,
                suite_research_doc=research,
                suite_live_doc=live,
            )

        if not isinstance(chosen, dict):
            out = {
                "success": False,
                "exists": False,
                "path": tau_model_path(),
                "last_report_exists": bool(last),
                "note": "尚无 ŷ_τc 模型；POST /api/quant/tc-lgb persist=true",
            }
            if last:
                out["last_report"] = last
                out["promote_gate"] = tau_promote_gate(last)
                for k in ("tau_end_hm", "fitted_at", "y_spec", "tau_grid", "zero_prefix", "oos"):
                    if last.get(k) is not None:
                        out[k] = last.get(k)
            return _attach_ridge_role_flags(
                out,
                tau_model_path(),
                live_present=bool(live),
                accept_research=_usable_tau_lgb_doc,
                suite_research_doc=research,
                suite_live_doc=live,
            )

        from core.research.holdout import model_fit_id as _tau_fit_id

        live_fit = _tau_fit_id(live) if isinstance(live, dict) else None
        chosen_fit = _tau_fit_id(chosen)
        is_live_desk = bool(
            live
            and (
                chosen is live
                or (live_fit and chosen_fit and live_fit == chosen_fit)
            )
        )
        out = dict(chosen)
        out.update(
            {
                "success": True,
                "exists": True,
                "path": tau_model_path(),
                "promoted": bool(is_live_desk and live),
                "shadow": False,
                "last_report_exists": bool(last),
                "live_model_present": bool(live),
                "model_role": "live" if is_live_desk else "research",
                "promote_gate": tau_promote_gate(
                    chosen
                    if isinstance(chosen.get("oos"), dict)
                    else (last or chosen)
                ),
            }
        )
        if last:
            out["last_report"] = last
        if is_live_desk and live:
            out["live_tau"] = (live or {}).get("tau")
        return _attach_ridge_role_flags(
            out,
            tau_model_path(),
            live_present=bool(live),
            accept_research=_usable_tau_lgb_doc,
            suite_research_doc=research,
            suite_live_doc=live,
        )

    @records_experiment("oo_rank")
    @consume_materialized
    def run_oo_rank_experiment(
        self,
        *,
        lookback: int = 700,
        watching_limit: int = MODEL_FIT_MAX_SIZE,
        holdout_trading_days: int = 20,
        topk_track: int = 10,
        ndcg_k: int = 10,
        l2: float = 5.0,
        backend: str = "auto",
        persist: bool = False,
        persist_role: str = "live",
        deploy_full_sample: bool = False,
        note: str = "",
        cross_section_zscore: bool = True,
        progress_cb: Optional[Any] = None,
        job_alive_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """ŷ_oo_rank：单头 LambdaRank。

        日线与 ŷ_oo / ŷ_τc 同源（``load_portfolio_stock_bars``：
        本地仓够长则不补远端，避免拟合时把当日 K 线刷进仓里）。
        截面 z 与损失宇宙均用整观察池（训练档已下线）。
        成交明细 oo_rank=1..n；不进 ranking 融合分；进合成 rank_score（买序/阈值）。
        ``persist=True``：research=写入研究槽；live=研究槽晋级或全样本部署；落盘不重训。
        ``progress_cb(cur, tot, msg)`` / ``job_alive_cb()``：后台 Job 进度与孤儿保护。
        """
        from core.research.feature_standardize import ZSCORE_UNIVERSE_POOL
        from core.research.oo_rank_panel import resolve_rank_fit_plan
        from core.research.portfolio_bars import load_portfolio_stock_bars
        from core.research.oo_rank_lambdarank import (
            fit_oo_rank_report,
            load_oo_rank_last_report,
            oo_rank_model_path,
            persist_oo_rank_model,
            save_oo_rank_last_report,
            stamp_rank_zscore_universe_meta,
        )

        def _prog(cur: int, tot: int, msg: str) -> None:
            if not callable(progress_cb):
                return
            try:
                progress_cb(int(cur), int(tot), str(msg or ""))
            except Exception:  # noqa: BLE001
                logger.debug("oo_rank progress_cb failed", exc_info=True)

        def _job_alive() -> bool:
            if not callable(job_alive_cb):
                return True
            try:
                return bool(job_alive_cb())
            except Exception:  # noqa: BLE001
                logger.debug("oo_rank job_alive_cb failed", exc_info=True)
                return False

        if persist:
            last = load_oo_rank_last_report()
            if not last:
                return {
                    "success": False,
                    "error": "尚无上次拟合，请先点「拟合」",
                    "task": "oo_rank",
                    "path": oo_rank_model_path(),
                }
            saved = persist_oo_rank_model(
                last,
                note=note or "",
                role=persist_role or "live",
                deploy_full_sample=bool(deploy_full_sample),
            )
            if not saved.get("success"):
                return {
                    "success": False,
                    "error": saved.get("error") or "上次拟合缺少可启用模型",
                    "task": "oo_rank",
                    "path": oo_rank_model_path(),
                }
            out = dict(last)
            out["persisted"] = saved
            out["from_last_report"] = True
            out["path"] = saved.get("path") or oo_rank_model_path()
            out["model_role"] = saved.get("model_role") or persist_role or "live"
            out["backend"] = saved.get("backend") or last.get("recommended_backend")
            out["recommended_backend"] = (
                saved.get("recommended_backend")
                or last.get("recommended_backend")
                or out.get("backend")
            )
            if note:
                out["api_note"] = str(note)[:200]
            return _attach_ridge_role_flags(
                out,
                oo_rank_model_path(),
                live_present=os.path.isfile(oo_rank_model_path()),
            )

        plan = resolve_rank_fit_plan(
            watching_limit=watching_limit,
            task="oo_rank",
        )
        if not plan.get("success"):
            return plan
        codes = list(plan.get("codes") or [])
        cap = int(plan.get("cap") or watching_limit)
        pool_n = int(plan.get("watching_pool_size") or len(codes))
        train_u = str(plan.get("train_universe") or "pool")

        _prog(0, max(1, len(codes)), f"ŷ_oo_rank 拉日线 {len(codes)} 只…")
        loaded, _failures, _fund = load_portfolio_stock_bars(
            codes,
            lookback=int(lookback or 120),
            fetch_fundamentals=False,
            offline_ok=True,
        )
        stock_bars: List[Dict[str, Any]] = []
        for code in codes:
            bars = loaded.get(str(code))
            if bars:
                stock_bars.append({"code": str(code), "bars": bars})
        _prog(0, max(1, len(stock_bars)), f"ŷ_oo_rank 组面板 0/{len(stock_bars)}")
        try:
            report = fit_oo_rank_report(
                stock_bars,
                holdout_trading_days=holdout_trading_days,
                topk_track=topk_track,
                ndcg_k=ndcg_k,
                l2=l2,
                backend=backend or "auto",
                persist=False,
                cross_section_zscore=bool(cross_section_zscore),
                train_codes=None,
                progress_cb=progress_cb,
            )
        finally:
            stock_bars.clear()
            try:
                from core.research.horizon_l1 import release_allocator_cache

                release_allocator_cache()
            except Exception:  # noqa: BLE001
                logger.debug("oo_rank release after fit failed", exc_info=True)
        report["watching_limit"] = cap
        report["watching_pool_size"] = pool_n
        report["universe_source"] = plan.get("universe_source") or "watching"
        report["n_codes"] = len(codes)
        report["lookback"] = lookback
        stamp_rank_zscore_universe_meta(
            report,
            zscore_universe=ZSCORE_UNIVERSE_POOL,
            train_universe=train_u,
        )
        if note:
            report["api_note"] = str(note)[:200]
        if not _job_alive():
            report["success"] = False
            report["error"] = "任务已中断或被接替"
            report["orphaned"] = True
            report["persisted"] = {"success": False, "skipped": True, "orphaned": True}
            report["path"] = oo_rank_model_path()
            return _attach_ridge_role_flags(
                report,
                oo_rank_model_path(),
                live_present=os.path.isfile(oo_rank_model_path()),
            )
        if report.get("success"):
            save_oo_rank_last_report(report)
        if persist and report.get("success"):
            saved = persist_oo_rank_model(
                report, note=note or "", role=persist_role or "live"
            )
            from core.research.holdout import stamp_persist_outcome

            stamp_persist_outcome(report, saved)
        else:
            report["persisted"] = {"success": False, "skipped": True}
        report["path"] = oo_rank_model_path()
        return _attach_ridge_role_flags(
            report,
            oo_rank_model_path(),
            live_present=os.path.isfile(oo_rank_model_path()),
        )

    def get_oo_rank_model(self) -> Dict[str, Any]:
        from core.research.oo_rank_lambdarank import (
            load_oo_rank_last_report,
            load_oo_rank_model,
            oo_rank_last_report_path,
            oo_rank_model_path,
        )

        path = oo_rank_model_path()
        research = load_oo_rank_model(prefer_research=True)
        live = load_oo_rank_model(prefer_research=False)
        persisted = research or live
        last_path = oo_rank_last_report_path()
        last = load_oo_rank_last_report()
        from core.research.holdout import model_fit_id

        chosen, use_last = _select_ridge_desk_doc(persisted, last)
        live_file = os.path.isfile(path)
        fitted = model_fit_id(chosen) or model_fit_id(last) or model_fit_id(persisted)
        if not fitted:
            fitted = _peek_model_stamp(last_path) or _peek_model_stamp(path)
        if not chosen:
            return _attach_ridge_role_flags(
                {
                    "success": False,
                    "exists": False,
                    "path": path,
                    "last_report_exists": bool(last),
                    "fitted_at": fitted,
                    "shadow": False,
                    "note": "尚无 ŷ_oo_rank；先拟合再启用研究/执行",
                },
                path,
                live_present=False,
                suite_research_doc=research,
                suite_live_doc=live,
            )
        out = dict(chosen)
        # 单头 LambdaRank：若落盘缺 backends.lambdarank，从同窗 last_report 补
        def _has_lambda_backend(doc: Any) -> bool:
            be = doc.get("backends") if isinstance(doc, dict) else None
            if not isinstance(be, dict):
                return False
            pack = be.get("lambdarank")
            if not isinstance(pack, dict):
                return False
            rm = pack.get("return_model_research") or pack.get("return_model")
            return isinstance(rm, dict) and bool(rm.get("booster_b64") or rm.get("coefficients"))

        _same_fit_last = bool(
            model_fit_id(out)
            and isinstance(last, dict)
            and model_fit_id(last)
            and model_fit_id(out) == model_fit_id(last)
        )
        _allow_last_side = bool(use_last or _same_fit_last)
        # 缺 backends.lambdarank：仅画 last 或同 fit 时从 last 补，避免全样本 last 污染研究槽
        if (
            _allow_last_side
            and not _has_lambda_backend(out)
            and isinstance(last, dict)
            and _has_lambda_backend(last)
        ):
            be_out = (
                dict(out["backends"])
                if isinstance(out.get("backends"), dict)
                else {}
            )
            be_last = last.get("backends") if isinstance(last.get("backends"), dict) else {}
            if isinstance(be_last.get("lambdarank"), dict):
                be_out["lambdarank"] = be_last["lambdarank"]
            if be_out:
                out["backends"] = be_out
        # 清掉旧 Ridge 对照包，避免因子表再走双列
        if isinstance(out.get("backends"), dict) and "ridge" in out["backends"]:
            be_clean = {
                k: v
                for k, v in out["backends"].items()
                if str(k).strip().lower() != "ridge"
            }
            out["backends"] = be_clean
        out.pop("ridge_return_model_research", None)
        out.pop("shadow_track", None)
        # 仅画 last 时回填 OOS；勿把 holdout=0 last 挂到研究/执行槽文档上
        if (
            use_last
            and not isinstance(out.get("oos"), dict)
            and isinstance(last, dict)
            and isinstance(last.get("oos"), dict)
        ):
            out["oos"] = last["oos"]
        if isinstance(out.get("oos"), dict):
            from core.research.oo_rank_lambdarank import _scrub_rank_oos

            out["oos"] = _scrub_rank_oos(
                out["oos"], drop_keys=("ridge_oo_baseline", "ridge_tc_baseline")
            )
        oos_out = out.get("oos") if isinstance(out.get("oos"), dict) else {}
        for _k in ("n_train_days", "n_test_days", "holdout_trading_days"):
            if out.get(_k) is not None:
                continue
            if oos_out.get(_k) is not None:
                out[_k] = oos_out.get(_k)
                continue
            # 训/测/Holdout：仅画 last 或同 fit 时从 last 补（异 fit 全样本 last 勿盖研究槽）
            if _allow_last_side and isinstance(last, dict) and last.get(_k) is not None:
                out[_k] = last.get(_k)
        rm = out.get("return_model") if isinstance(out.get("return_model"), dict) else {}
        backend = (
            out.get("backend")
            or out.get("recommended_backend")
            or (rm.get("backend") if isinstance(rm, dict) else None)
        )
        out.update(
            {
                "success": True,
                "exists": True,
                "path": path,
                "shadow": bool(use_last) or not live_file,
                "from_last_report": bool(use_last),
                "last_report_exists": bool(last),
                "backend": backend,
                "recommended_backend": out.get("recommended_backend") or backend,
            }
        )
        if fitted:
            out["fitted_at"] = fitted
        return _attach_ridge_role_flags(
            out,
            path,
            live_present=live_file,
            suite_research_doc=research,
            suite_live_doc=live,
        )


    def start_oo_rank_job(
        self,
        *,
        lookback: int = 700,
        watching_limit: int = MODEL_FIT_MAX_SIZE,
        holdout_trading_days: int = 20,
        topk_track: int = 10,
        ndcg_k: int = 10,
        l2: float = 5.0,
        backend: str = "auto",
        note: str = "",
        cross_section_zscore: bool = True,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """后台 ŷ_oo_rank 拟合；轮询 ``GET /api/jobs/oo-rank``。不写盘。"""
        from core.job_progress import oo_rank_job

        kwargs = dict(
            lookback=lookback,
            watching_limit=watching_limit,
            holdout_trading_days=holdout_trading_days,
            topk_track=topk_track,
            ndcg_k=ndcg_k,
            l2=l2,
            backend=backend,
            persist=False,
            note=note or "",
            cross_section_zscore=bool(cross_section_zscore),
        )
        return _start_ridge_fit_job(
            slot=oo_rank_job,
            kind="oo_rank",
            message="ŷ_oo_rank 拟合中…",
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            worker_fn=lambda progress_cb=None, job_alive_cb=None: self.run_oo_rank_experiment(
                **kwargs,
                progress_cb=progress_cb,
                job_alive_cb=job_alive_cb,
            ),
        )

    @records_experiment("tc_rank")
    @consume_materialized
    def run_tc_rank_experiment(
        self,
        *,
        lookback: int = 700,
        watching_limit: int = MODEL_FIT_MAX_SIZE,
        holdout_trading_days: int = 20,
        topk_track: int = 10,
        ndcg_k: int = 10,
        l2: float = 5.0,
        backend: str = "auto",
        persist: bool = False,
        persist_role: str = "live",
        deploy_full_sample: bool = False,
        note: str = "",
        cross_section_zscore: bool = True,
        tau_end_hm: Optional[str] = None,
        progress_cb: Optional[Any] = None,
        job_alive_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """ŷ_τc_rank：单头 LambdaRank。

        日线与 ŷ_oo / ŷ_τc 同源（``load_portfolio_stock_bars``：
        本地仓够长则不补远端）。
        ``tau_end_hm`` 与 ŷ_τc 同组前缀（默认 09:30）。
        截面 z 与损失宇宙均用整观察池（训练档已下线）。
        启用只落盘；不进 fuse；相对分进合成 rank_score（买序/阈值）。
        ``progress_cb(cur, tot, msg)`` / ``job_alive_cb()``：后台 Job 进度与孤儿保护。
        """
        from core.research.feature_standardize import ZSCORE_UNIVERSE_POOL
        from core.research.oo_rank_lambdarank import stamp_rank_zscore_universe_meta
        from core.research.oo_rank_panel import resolve_rank_fit_plan
        from core.research.portfolio_bars import load_portfolio_stock_bars
        from core.research.tc_rank import (
            fit_tc_rank_report,
            load_tc_rank_last_report,
            persist_tc_rank_model,
            save_tc_rank_last_report,
            tc_rank_model_path,
        )

        def _prog(cur: int, tot: int, msg: str) -> None:
            if not callable(progress_cb):
                return
            try:
                progress_cb(int(cur), int(tot), str(msg or ""))
            except Exception:  # noqa: BLE001
                logger.debug("tc_rank progress_cb failed", exc_info=True)

        def _job_alive() -> bool:
            if not callable(job_alive_cb):
                return True
            try:
                return bool(job_alive_cb())
            except Exception:  # noqa: BLE001
                logger.debug("tc_rank job_alive_cb failed", exc_info=True)
                return False

        if persist:
            last = load_tc_rank_last_report()
            if not last:
                return {
                    "success": False,
                    "error": "尚无上次拟合，请先点「拟合」",
                    "task": "tc_rank",
                    "path": tc_rank_model_path(),
                }
            saved = persist_tc_rank_model(
                last,
                note=note or "",
                role=persist_role or "live",
                deploy_full_sample=bool(deploy_full_sample),
            )
            if not saved.get("success"):
                return {
                    "success": False,
                    "error": saved.get("error") or "上次拟合缺少可启用模型",
                    "task": "tc_rank",
                    "path": tc_rank_model_path(),
                }
            out = dict(last)
            out["persisted"] = saved
            out["from_last_report"] = True
            out["path"] = saved.get("path") or tc_rank_model_path()
            out["model_role"] = saved.get("model_role") or persist_role or "live"
            out["backend"] = saved.get("backend") or last.get("recommended_backend")
            out["recommended_backend"] = (
                saved.get("recommended_backend")
                or last.get("recommended_backend")
                or out.get("backend")
            )
            if note:
                out["api_note"] = str(note)[:200]
            return _attach_ridge_role_flags(
                out,
                tc_rank_model_path(),
                live_present=os.path.isfile(tc_rank_model_path()),
            )

        plan = resolve_rank_fit_plan(
            watching_limit=watching_limit,
            task="tc_rank",
        )
        if not plan.get("success"):
            return plan
        codes = list(plan.get("codes") or [])
        cap = int(plan.get("cap") or watching_limit)
        pool_n = int(plan.get("watching_pool_size") or len(codes))
        train_u = str(plan.get("train_universe") or "pool")

        _prog(0, max(1, len(codes)), f"ŷ_τc_rank 拉日线 {len(codes)} 只…")
        loaded, _failures, _fund = load_portfolio_stock_bars(
            codes,
            lookback=int(lookback or 700),
            fetch_fundamentals=False,
            offline_ok=True,
        )
        stock_bars: List[Dict[str, Any]] = []
        for code in codes:
            bars = loaded.get(str(code))
            if bars:
                stock_bars.append({"code": str(code), "bars": bars})
        _prog(0, max(1, len(stock_bars)), f"ŷ_τc_rank 组面板 0/{len(stock_bars)}")
        try:
            report = fit_tc_rank_report(
                stock_bars,
                holdout_trading_days=holdout_trading_days,
                topk_track=topk_track,
                ndcg_k=ndcg_k,
                l2=l2,
                backend=backend or "auto",
                cross_section_zscore=bool(cross_section_zscore),
                train_codes=None,
                tau_end_hm=tau_end_hm,
                progress_cb=progress_cb,
            )
        finally:
            stock_bars.clear()
            try:
                from core.research.horizon_l1 import release_allocator_cache

                release_allocator_cache()
            except Exception:  # noqa: BLE001
                logger.debug("tc_rank release after fit failed", exc_info=True)
        report["watching_limit"] = cap
        report["watching_pool_size"] = pool_n
        report["universe_source"] = plan.get("universe_source") or "watching"
        report["n_codes"] = len(codes)
        report["lookback"] = lookback
        stamp_rank_zscore_universe_meta(
            report,
            zscore_universe=ZSCORE_UNIVERSE_POOL,
            train_universe=train_u,
        )
        if note:
            report["api_note"] = str(note)[:200]
        if not _job_alive():
            report["success"] = False
            report["error"] = "任务已中断或被接替"
            report["orphaned"] = True
            report["persisted"] = {"success": False, "skipped": True, "orphaned": True}
            report["path"] = tc_rank_model_path()
            return _attach_ridge_role_flags(
                report,
                tc_rank_model_path(),
                live_present=os.path.isfile(tc_rank_model_path()),
            )
        if report.get("success"):
            save_tc_rank_last_report(report)
        report["persisted"] = {"success": False, "skipped": True}
        report["path"] = tc_rank_model_path()
        return _attach_ridge_role_flags(
            report,
            tc_rank_model_path(),
            live_present=os.path.isfile(tc_rank_model_path()),
        )

    def get_tc_rank_model(self) -> Dict[str, Any]:
        from core.research.holdout import model_fit_id
        from core.research.tc_rank import (
            load_tc_rank_last_report,
            load_tc_rank_model,
            tc_rank_last_report_path,
            tc_rank_model_path,
        )

        path = tc_rank_model_path()
        research = load_tc_rank_model(prefer_research=True)
        live = load_tc_rank_model(prefer_research=False)
        persisted = research or live
        last_path = tc_rank_last_report_path()
        last = load_tc_rank_last_report()
        chosen, use_last = _select_ridge_desk_doc(persisted, last)
        live_file = os.path.isfile(path)
        fitted = model_fit_id(chosen) or model_fit_id(last) or model_fit_id(persisted)
        if not fitted:
            fitted = _peek_model_stamp(last_path) or _peek_model_stamp(path)
        if not chosen:
            return _attach_ridge_role_flags(
                {
                    "success": False,
                    "exists": False,
                    "path": path,
                    "last_report_exists": bool(last),
                    "fitted_at": fitted,
                    "shadow": False,
                    "note": "尚无 ŷ_τc_rank；先拟合再启用研究/执行",
                },
                path,
                live_present=False,
                suite_research_doc=research,
                suite_live_doc=live,
            )
        out = dict(chosen)

        def _has_lambda_backend(doc: Any) -> bool:
            be = doc.get("backends") if isinstance(doc, dict) else None
            if not isinstance(be, dict):
                return False
            pack = be.get("lambdarank")
            if not isinstance(pack, dict):
                return False
            rm0 = pack.get("return_model_research") or pack.get("return_model")
            return isinstance(rm0, dict) and bool(
                rm0.get("booster_b64") or rm0.get("coefficients")
            )

        _same_fit_last = bool(
            model_fit_id(out)
            and isinstance(last, dict)
            and model_fit_id(last)
            and model_fit_id(out) == model_fit_id(last)
        )
        _allow_last_side = bool(use_last or _same_fit_last)
        if (
            _allow_last_side
            and not _has_lambda_backend(out)
            and isinstance(last, dict)
            and _has_lambda_backend(last)
        ):
            be_out = dict(out["backends"]) if isinstance(out.get("backends"), dict) else {}
            be_last = last.get("backends") if isinstance(last.get("backends"), dict) else {}
            if isinstance(be_last.get("lambdarank"), dict):
                be_out["lambdarank"] = be_last["lambdarank"]
            if be_out:
                out["backends"] = be_out
        if isinstance(out.get("backends"), dict) and "ridge" in out["backends"]:
            out["backends"] = {
                k: v
                for k, v in out["backends"].items()
                if str(k).strip().lower() != "ridge"
            }
        out.pop("ridge_return_model_research", None)
        out.pop("shadow_track", None)
        if (
            use_last
            and not isinstance(out.get("oos"), dict)
            and isinstance(last, dict)
            and isinstance(last.get("oos"), dict)
        ):
            out["oos"] = last["oos"]
        if isinstance(out.get("oos"), dict):
            from core.research.oo_rank_lambdarank import _scrub_rank_oos

            out["oos"] = _scrub_rank_oos(
                out["oos"], drop_keys=("ridge_oo_baseline", "ridge_tc_baseline")
            )
        oos_out = out.get("oos") if isinstance(out.get("oos"), dict) else {}
        for _k in (
            "n_train_days",
            "n_test_days",
            "holdout_trading_days",
            "tau_end_hm",
            "tau_hm",
            "tau_grid",
            "zero_prefix",
            "y_spec",
        ):
            if out.get(_k) is not None:
                continue
            if oos_out.get(_k) is not None:
                out[_k] = oos_out.get(_k)
                continue
            # Holdout/前缀/y_spec：勿用异 fit 的 last（含全样本）盖研究槽
            if _allow_last_side and isinstance(last, dict) and last.get(_k) is not None:
                out[_k] = last.get(_k)
        # 落盘包缺前缀戳时，从 return_model.y_spec 抬一层，供 UI restore
        if out.get("tau_end_hm") is None:
            rm0 = out.get("return_model") if isinstance(out.get("return_model"), dict) else {}
            ys0 = rm0.get("y_spec") if isinstance(rm0.get("y_spec"), dict) else {}
            if ys0.get("tau_end_hm") is not None:
                out["tau_end_hm"] = ys0.get("tau_end_hm")
            if out.get("zero_prefix") is None and "zero_prefix" in ys0:
                out["zero_prefix"] = ys0.get("zero_prefix")
            if out.get("tau_grid") is None and ys0.get("tau_grid") is not None:
                out["tau_grid"] = ys0.get("tau_grid")
        rm = out.get("return_model") if isinstance(out.get("return_model"), dict) else {}
        backend = (
            out.get("backend")
            or out.get("recommended_backend")
            or (rm.get("backend") if isinstance(rm, dict) else None)
        )
        out.update(
            {
                "success": True,
                "exists": True,
                "path": path,
                "shadow": bool(use_last) or not live_file,
                "from_last_report": bool(use_last),
                "last_report_exists": bool(last),
                "backend": backend,
                "recommended_backend": out.get("recommended_backend") or backend,
            }
        )
        if fitted:
            out["fitted_at"] = fitted
        return _attach_ridge_role_flags(
            out,
            path,
            live_present=live_file,
            suite_research_doc=research,
            suite_live_doc=live,
        )

    def start_tc_rank_job(
        self,
        *,
        lookback: int = 700,
        watching_limit: int = MODEL_FIT_MAX_SIZE,
        holdout_trading_days: int = 20,
        topk_track: int = 10,
        ndcg_k: int = 10,
        l2: float = 5.0,
        backend: str = "auto",
        note: str = "",
        cross_section_zscore: bool = True,
        tau_end_hm: Optional[str] = None,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """后台 ŷ_τc_rank 拟合；轮询 ``GET /api/jobs/tc-rank``。不写盘。"""
        from core.job_progress import tc_rank_job

        kwargs = dict(
            lookback=lookback,
            watching_limit=watching_limit,
            holdout_trading_days=holdout_trading_days,
            topk_track=topk_track,
            ndcg_k=ndcg_k,
            l2=l2,
            backend=backend,
            persist=False,
            note=note or "",
            cross_section_zscore=bool(cross_section_zscore),
            tau_end_hm=tau_end_hm,
        )
        return _start_ridge_fit_job(
            slot=tc_rank_job,
            kind="tc_rank",
            message="ŷ_τc_rank 拟合中…",
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            worker_fn=lambda progress_cb=None, job_alive_cb=None: self.run_tc_rank_experiment(
                **kwargs,
                progress_cb=progress_cb,
                job_alive_cb=job_alive_cb,
            ),
        )


    def _run_horizon_ridge_experiment(
        self,
        horizon: str,
        *,
        lookback: int = 120,
        watching_limit: int = MODEL_FIT_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        tau_hm: Optional[str] = None,
        tau_end_hm: Optional[str] = None,
        persist: bool = False,
        note: str = "",
        force_promote: bool = False,
        persist_role: str = "live",
        holdout_trading_days: int = 20,
        cross_section_zscore: bool = True,
        progress_cb: Optional[Any] = None,
        job_alive_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """泛化 ŷ_τ horizon：同窗双训 LightGBM + Ridge，AUC 择优。horizon ∈ {t30…t90}。

        建面板 L1-first：``hz_*`` → stitch ``tc_HHMM``（日线指纹）→ 未命中再读本地 5m。
        进度：``L1命中… · 5m回退…``；报告 ``timing.panel_s`` / ``l1_hit_rate``。
        产品消费路径（fail-closed）：开跑前 ``l1_tau_coverage`` cold / 探测失败 /
        湖关闭 → ``materialize_required``；建面板实际 L1 命中过低（含指纹 miss）
        提前或收尾阻断，不整池啃 5m。面板数据超过 3GB，或进程当前占用超过
        机器内存约六成时，任务停在「内存保护」并放掉已建面板。判定前先把
        上一头拟合已归还的堆页交回系统，避免旧水位把下一头立刻停掉。
        ``tau_end_hm``：训练前缀终点 09:35…11:00（ŷ_τ* 组滑条，与 ŷ_τc 独立；默认 11:00）。
        方案 B：窗头仅分钟钟，``09:30``/open 上抬为 09:35；不读日线 open Cube。
        日线与 ŷ_oo / ŷ_τc 同源（``load_portfolio_stock_bars``：本地仓够长则不补远端）。
        ``lookback`` 为日线上限；5m 回退按**单票本地缓存**有多少用多少（仅热 L1 下
        个别票 miss 时）。
        截面 z 只用 L2；某一天的某一钟没有立方时丢掉这些行，
        训练行全部没有立方才 ``materialize_required``。报告里的 ``minute_days``/
        ``minute_span`` 仍记宇宙够格窗，仅作对照。
        ``progress_cb(cur, tot, msg)``：job 槽刷新（流式建面板 + 截面/双训阶段）。
        ``job_alive_cb()``：False 时跳过 ``save_ridge_last_report``（槽已被接替/取消）。
        """
        from core.research.horizon_prob import horizon_promote_gate
        from core.research.horizon_ridge import (
            fit_horizon_dual_report,
            load_ridge_last_report,
            load_ridge_model,
            persist_ridge_model,
            ridge_model_path,
            save_ridge_last_report,
        )
        from core.research.materialize.consumer import (
            MaterializeRequired,
            consumer_read_only,
        )
        from core.research.materialize.cover_policy import (
            resolve_fit_minute_covered_dates,
        )
        from core.signal.minute_tau_grid import (
            resolve_horizon_train_prefix,
            sanitize_horizon_tau_grid,
        )

        hn = horizon[1:]
        task = f"{horizon}_ridge"

        def _progress(cur: int, tot: int, msg: str) -> None:
            if not callable(progress_cb):
                return
            try:
                progress_cb(int(cur), int(tot), str(msg or ""))
            except Exception:  # noqa: BLE001
                logger.debug("%s progress_cb failed", task, exc_info=True)

        def _job_alive() -> bool:
            if not callable(job_alive_cb):
                return True
            try:
                return bool(job_alive_cb())
            except Exception:  # noqa: BLE001
                logger.debug("%s job_alive_cb failed", task, exc_info=True)
                return False

        if persist:
            last = load_ridge_last_report(horizon)
            if last:
                saved = persist_ridge_model(
                    horizon,
                    last,
                    note=note or f"persist last {horizon} report",
                    force=bool(force_promote),
                    role=persist_role,
                )
                out = dict(last)
                from core.research.holdout import stamp_persist_outcome

                stamp_persist_outcome(out, saved)
                out["from_last_report"] = True
                if not out.get("promote_gate"):
                    out["promote_gate"] = horizon_promote_gate(last)
                return _attach_ridge_role_flags(out, ridge_model_path(horizon))

        from core.watching.store import MODEL_FIT_MAX_SIZE

        resolved = _resolve_fit_universe(
            watching_limit=watching_limit,
        )
        if not resolved.get("ok", True):
            return {
                "success": False,
                "error": resolved.get("error") or "拟合宇宙解析失败",
                "task": task,
                "universe_source": resolved.get("source"),
            }
        codes = list(resolved.get("codes") or [])
        limit = max(2, min(int(watching_limit or MODEL_FIT_MAX_SIZE), int(MODEL_FIT_MAX_SIZE)))
        codes = codes[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": f"研究池至少 2 只才可跑 ŷ_τ{hn} Ridge",
                "task": task,
            }

        period = str(minute_period or "5").strip() or "5"
        prefix = resolve_horizon_train_prefix(
            tau_end_hm=tau_end_hm,
            tau_hm=tau_hm,
            enable_minute_tau=True,
        )
        if prefix.get("error"):
            return {
                "success": False,
                "error": prefix["error"],
                "task": task,
            }
        resolved_end = str(prefix.get("tau_end_hm") or "11:00")[:5]
        # 窗头无 0 前缀；网格自 09:35（误传 open/09:30 会被剥掉）
        tau_key = str(prefix.get("tau_hm") or resolved_end)
        tau_grid = sanitize_horizon_tau_grid(prefix.get("tau_grid"))
        if not tau_grid:
            return {
                "success": False,
                "error": "窗头 τ 网格为空（须 09:35…11:00）；请检查前缀滑条",
                "task": task,
            }
        zero_prefix = False

        from core.research.horizon_l1 import (
            TAU_L1_EARLY_ABORT_MIN,
            TAU_L1_MIN_HIT_RATE,
            confirm_fit_memory_stop,
            release_allocator_cache,
            materialize_required_tau_l1_payload,
            panel_payload_bytes,
            physical_ram_bytes,
            probe_tau_grid_l1_coverage,
            process_rss_bytes,
            sync_horizon_head_panel,
            tau_l1_coverage_blocks_fit,
            tau_l1_hit_rate_too_low,
            try_load_horizon_head_panel,
            try_stitch_horizon_l1_panel,
        )
        from core.research.horizon_ridge import (
            enrich_horizon_raw_panels,
            horizon_panel_keep_names,
        )
        from core.research.tc_model_compact import (
            TAU_PANEL_POOL_BATCH,
            build_tau_panels_from_bars,
        )
        from core.ports.market import resolve_market_code
        from core.store import load_minute_cache
        import gc
        import time as _time

        # 宇宙够格日仅诊断（与 L2 物化窗对照）；拟合不按此裁单票 5m
        covered_list = resolve_fit_minute_covered_dates(codes, period=period)
        minute_span = (
            f"{covered_list[0]}→{covered_list[-1]}" if covered_list else None
        )

        def _block_tau_l1(
            *,
            reason: str,
            cov: Optional[Dict[str, Any]] = None,
            l1_head: int = 0,
            l1_stitch: int = 0,
            rebuild: int = 0,
        ) -> None:
            if not consumer_read_only():
                return
            raise MaterializeRequired(
                materialize_required_tau_l1_payload(
                    tau_grid=tau_grid,
                    task=task,
                    cov=cov if cov is not None else l1_tau_cov,
                    reason=reason,
                    l1_head_hit=l1_head,
                    l1_stitch_hit=l1_stitch,
                    rebuild_5m=rebuild,
                )
            )

        # P4：拟合前轻量覆盖探测；产品只读路径 fail-closed（对齐 ŷ_oo）
        l1_tau_cov: Dict[str, Any] = {}
        try:
            l1_tau_cov = probe_tau_grid_l1_coverage(codes, tau_grid)
        except Exception:  # noqa: BLE001
            logger.debug("%s L1 coverage probe failed", horizon, exc_info=True)
            l1_tau_cov = {
                "rate": 0.0,
                "cold": True,
                "probe_ok": False,
                "lake_enabled": True,
                "hint": "τ 网格 L1 覆盖探测异常",
            }
        if l1_tau_cov.get("hint"):
            _progress(
                0,
                max(1, len(codes)),
                f"ŷ_τ{hn} {l1_tau_cov.get('hint')}",
            )
        if tau_l1_coverage_blocks_fit(l1_tau_cov):
            reason = "cold"
            if l1_tau_cov.get("probe_ok") is False:
                reason = "probe_failed"
            elif l1_tau_cov.get("lake_enabled") is False:
                reason = "lake_disabled"
            _block_tau_l1(reason=reason, cov=l1_tau_cov)

        # L1-first：hz_* → stitch tc_HHMM → 5m 回退（分批 ProcessPool）
        stock_bars: List[Dict[str, Any]] = []
        raw_acc: List[Dict[str, Any]] = []
        enriched_hz: List[Dict[str, Any]] = []
        l1_head_hit = 0
        l1_stitch_hit = 0
        rebuild_5m = 0
        minute_hit = 0
        minute_codes_miss = 0
        min_hist = 12
        n_codes = max(1, len(codes))
        grid_n = len(tau_grid) if isinstance(tau_grid, (list, tuple)) else 1
        label_heads = (horizon,)
        keep_names = horizon_panel_keep_names(horizon)
        batch_n = max(4, int(TAU_PANEL_POOL_BATCH))
        span_bit = f" · 5m窗{minute_span}" if minute_span else ""
        cov_bit = ""
        if isinstance(l1_tau_cov.get("rate"), (int, float)):
            tag = "冷" if l1_tau_cov.get("cold") else "热"
            cov_bit = f" · L1{tag}{float(l1_tau_cov['rate']):.0%}"
        panel_t0 = _time.perf_counter()

        def _panel_msg(cur: int) -> str:
            l1_n = int(l1_head_hit) + int(l1_stitch_hit)
            return (
                f"ŷ_τ{hn} 建面板 {cur}/{n_codes}"
                + (f" · τ×{grid_n}" if grid_n > 1 else "")
                + f" · L1命中{l1_n} · 5m回退{rebuild_5m}"
                + cov_bit
                + span_bit
            )

        _progress(0, n_codes, _panel_msg(0))

        scanned = 0
        batch_items: List[Dict[str, Any]] = []
        batch_end_scanned = 0

        def _guard_low_l1_hit(*, early: bool) -> None:
            """产品路径：实际 L1 命中过低则阻断（防热探测 + 指纹全 miss）。"""
            if not consumer_read_only():
                return
            pending = len(batch_items)
            # early：把待 flush 的 5m 票也计入 attempted，避免先跑完一整批再拦
            rebuild_eff = int(rebuild_5m) + (pending if early else 0)
            min_att = int(TAU_L1_EARLY_ABORT_MIN) if early else 1
            if not tau_l1_hit_rate_too_low(
                l1_head_hit=l1_head_hit,
                l1_stitch_hit=l1_stitch_hit,
                rebuild_5m=rebuild_eff,
                min_attempted=min_att,
                min_rate=float(TAU_L1_MIN_HIT_RATE),
            ):
                return
            batch_items.clear()
            _block_tau_l1(
                reason="early_abort" if early else "low_hit",
                cov=l1_tau_cov,
                l1_head=l1_head_hit,
                l1_stitch=l1_stitch_hit,
                rebuild=rebuild_eff,
            )

        def _flush_batch() -> bool:
            nonlocal batch_items, rebuild_5m, memory_error
            if not batch_items:
                return True
            if not _job_alive():
                batch_items = []
                return False
            _guard_low_l1_hit(early=True)
            end_scanned = batch_end_scanned
            n_batch = len(batch_items)

            def _cb(done: int, _tot: int, _msg: str) -> None:
                cur = min(
                    n_codes,
                    max(0, end_scanned - n_batch) + max(0, int(done or 0)),
                )
                _progress(cur, n_codes, _panel_msg(cur))

            try:
                chunk = build_tau_panels_from_bars(
                    batch_items,
                    min_history=min_hist,
                    gap_trigger_pct=gap_trigger_pct,
                    tau_hm=tau_key,
                    tau_grid=tau_grid,
                    include_alpha_th=True,
                    attach_breadth=False,
                    label_heads=label_heads,
                    progress_cb=_cb,
                    # 外层 hz/stitch 已 miss，勿在池内再读湖
                    skip_l1_stitch=True,
                )
            except MemoryError:
                memory_error = _oom_message()
                batch_items = []
                return True
            raw_acc.extend(chunk)
            if _track_panels(chunk):
                batch_items = []
                return True
            rebuild_5m += len(batch_items)
            for it in batch_items:
                it.pop("minute_bars", None)
            batch_items = []
            _progress(end_scanned, n_codes, _panel_msg(end_scanned))
            return True

        ram_bytes = physical_ram_bytes()
        panel_bytes = 0
        memory_error = ""

        def _release_fit_panels() -> None:
            raw_acc.clear()
            enriched_hz.clear()
            stock_bars.clear()
            batch_items.clear()
            release_allocator_cache()

        def _track_panels(panels: List[Any]) -> str:
            nonlocal panel_bytes, memory_error
            if memory_error:
                return memory_error
            for p in panels:
                panel_bytes += panel_payload_bytes(p)
            memory_error = confirm_fit_memory_stop(
                panel_bytes=panel_bytes,
                rss_bytes=process_rss_bytes(),
                ram_bytes=ram_bytes,
                reread_rss=process_rss_bytes,
            )
            return memory_error

        def _oom_message() -> str:
            return (
                "内存保护：分配失败，已停止以免网页进程被系统杀掉。"
                "请缩小研究池，或把窗头终点提前。"
            )

        release_allocator_cache()
        if not _job_alive():
            return {
                "success": False,
                "error": "任务已中断或被接替",
                "orphaned": True,
                "task": task,
            }
        from core.research.portfolio_bars import load_portfolio_stock_bars

        loaded_bars, _bar_fail, _fund = load_portfolio_stock_bars(
            codes,
            lookback=int(lookback or 120),
            fetch_fundamentals=False,
            offline_ok=True,
        )
        orphaned = False
        for i, code in enumerate(codes):
            if memory_error:
                break
            if i and i % 40 == 0:
                gc.collect()
            if not _job_alive():
                orphaned = True
                break
            bars = list(loaded_bars.get(str(code)) or [])
            scanned = i + 1
            if not bars:
                if scanned == 1 or scanned % 8 == 0 or scanned >= n_codes:
                    _progress(scanned, n_codes, _panel_msg(scanned))
                continue
            code_s = str(code)
            daily_row: Dict[str, Any] = {"code": code_s, "bars": bars}
            stock_bars.append(daily_row)

            # P2：整头 hz_* 已 relabel
            try:
                hz_hit = try_load_horizon_head_panel(
                    horizon,
                    code_s,
                    bars,
                    tau_grid,
                    min_history=min_hist,
                    as_matrix=True,
                    project_names=keep_names,
                )
            except MemoryError:
                memory_error = _oom_message()
                break
            except Exception:  # noqa: BLE001
                logger.debug("%s hz load failed %s", horizon, code_s, exc_info=True)
                hz_hit = None
            if hz_hit is not None:
                enriched_hz.append(hz_hit)
                l1_head_hit += 1
                if _track_panels([hz_hit]):
                    break
                if scanned == 1 or scanned % 8 == 0 or scanned >= n_codes:
                    _progress(scanned, n_codes, _panel_msg(scanned))
                continue

            # P1：按钟 stitch tc_HHMM（日线指纹；免读 5m）
            try:
                stitched = try_stitch_horizon_l1_panel(
                    code_s,
                    bars,
                    tau_grid,
                    min_history=min_hist,
                    include_alpha_tc=True,
                    include_alpha_th=True,
                    as_matrix=True,
                    project_names=keep_names,
                )
            except MemoryError:
                memory_error = _oom_message()
                break
            except Exception:  # noqa: BLE001
                logger.debug("%s L1 stitch failed %s", horizon, code_s, exc_info=True)
                stitched = None
            if stitched is not None:
                raw_acc.append(stitched)
                l1_stitch_hit += 1
                if _track_panels([stitched]):
                    break
                if scanned == 1 or scanned % 8 == 0 or scanned >= n_codes:
                    _progress(scanned, n_codes, _panel_msg(scanned))
                continue

            # 回退：读本地 5m 现算
            item: Dict[str, Any] = {"code": code_s, "bars": bars}
            try:
                mkt, pure = resolve_market_code(code_s)
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or code_s,
                    period,
                    min_bars=1,
                    ignore_age=True,
                )
                if packed:
                    mb, _meta = packed
                    if mb:
                        item["minute_bars"] = mb
                        minute_hit += 1
                        batch_items.append(item)
                        batch_end_scanned = scanned
                    else:
                        minute_codes_miss += 1
                else:
                    minute_codes_miss += 1
            except MemoryError:
                memory_error = _oom_message()
                break
            except Exception:  # noqa: BLE001
                logger.debug(
                    "%s ridge minute cache miss for %s", horizon, code, exc_info=True
                )
                minute_codes_miss += 1
            # 攒够 early 门槛且仍几乎全 5m → 先拦，不启动 ProcessPool
            if len(batch_items) >= int(TAU_L1_EARLY_ABORT_MIN):
                _guard_low_l1_hit(early=True)
            if len(batch_items) >= batch_n:
                if not _flush_batch():
                    orphaned = True
                    break
            elif scanned == 1 or scanned % 8 == 0 or scanned >= n_codes:
                _progress(scanned, n_codes, _panel_msg(scanned))
        if not orphaned and not memory_error and not _flush_batch():
            orphaned = True
        if memory_error:
            _progress(scanned, n_codes, memory_error)
            _release_fit_panels()
            return {
                "success": False,
                "error": memory_error,
                "task": task,
            }
        if orphaned or not _job_alive():
            return {
                "success": False,
                "error": "任务已中断或被接替",
                "orphaned": True,
                "task": task,
            }
        _progress(n_codes, n_codes, _panel_msg(n_codes))
        # 收尾闸：热探测但指纹大面积 miss → 阻断，不进截面广度（省内存）
        _guard_low_l1_hit(early=False)

        panel_ok = l1_head_hit + l1_stitch_hit + rebuild_5m
        if panel_ok < 1:
            return {
                "success": False,
                "error": (
                    f"无可用面板（L1 未命中且无本地分钟缓存 period={period} · 池 {len(codes)} 只）；"
                    "请先分钟线「物化特征（L1/L2）」或「强更 5m（L0）」"
                ),
                "task": task,
                "minute_period": period,
                "watching_limit": limit,
                "watching_pool_size": len(codes),
                "minute_cache_only": True,
                "minute_codes_miss": minute_codes_miss,
                "l1_head_hit": l1_head_hit,
                "l1_stitch_hit": l1_stitch_hit,
                "rebuild_5m": rebuild_5m,
            }

        _progress(n_codes, n_codes, f"ŷ_τ{hn} 截面广度+relabel…")
        try:
            enriched = enrich_horizon_raw_panels(
                horizon,
                raw_acc,
                gap_trigger_pct=gap_trigger_pct,
                already_breadth=False,
            )
        except MemoryError:
            _release_fit_panels()
            return {
                "success": False,
                "error": _oom_message(),
                "task": task,
            }
        raw_acc.clear()
        # P2 write-through：enrich 后写入 hz_*（仅 writer_active 时真正落盘）
        for p in enriched:
            if not isinstance(p, dict):
                continue
            code_p = str(p.get("code") or "")
            bars_p = None
            for sb in stock_bars:
                if str(sb.get("code") or "") == code_p:
                    bars_p = sb.get("bars")
                    break
            if not code_p or not bars_p:
                continue
            try:
                sync_horizon_head_panel(
                    horizon,
                    code_p,
                    p,
                    bars_p,
                    tau_grid,
                    min_history=min_hist,
                )
            except Exception:  # noqa: BLE001
                logger.debug("%s hz sync failed %s", horizon, code_p, exc_info=True)
        if enriched_hz:
            enriched = list(enriched) + list(enriched_hz)
            enriched_hz.clear()
        panel_s = float(_time.perf_counter() - panel_t0)
        l1_n = int(l1_head_hit) + int(l1_stitch_hit)
        denom = max(1, l1_n + int(rebuild_5m))
        l1_hit_rate = float(l1_n) / float(denom)

        _progress(n_codes, n_codes, f"ŷ_τ{hn} 折矩阵…")
        from core.research.horizon_compact import take_calendar_and_release
        from core.research.horizon_ridge import prepare_horizon_compact

        prepared = prepare_horizon_compact(
            horizon,
            enriched_panels=enriched,
            tau_hm=tau_key,
            tau_grid=tau_grid,
        )
        compact = prepared["compact"]
        calendar = take_calendar_and_release(stock_bars)

        _progress(n_codes, n_codes, f"ŷ_τ{hn} LightGBM + Ridge 双训…")
        report = fit_horizon_dual_report(
            horizon,
            stock_bars,
            ridge_lambda=ridge_lambda,
            min_history=min_hist,
            gap_trigger_pct=gap_trigger_pct,
            holdout_trading_days=holdout_trading_days,
            cross_section_zscore=bool(cross_section_zscore),
            tau_hm=tau_key,
            tau_grid=tau_grid,
            compact_panel=compact,
            calendar_dates=calendar,
        )
        # 双训结束即可卸面板和日立方。任务标完成之前做，下一头不会撞上 5GB 水位。
        del prepared, compact, enriched
        _release_fit_panels()
        report["watching_limit"] = limit
        report["universe_source"] = resolved.get("source")
        report["watching_pool_size"] = int(
            resolved.get("watching_pool_size") or len(codes)
        )
        timing = report.get("timing") if isinstance(report.get("timing"), dict) else {}
        timing = dict(timing)
        timing["panel_s"] = panel_s
        timing["l1_hit_rate"] = l1_hit_rate
        timing["l1_head_hit"] = int(l1_head_hit)
        timing["l1_stitch_hit"] = int(l1_stitch_hit)
        timing["rebuild_5m"] = int(rebuild_5m)
        report["timing"] = timing
        report["l1_head_hit"] = int(l1_head_hit)
        report["l1_stitch_hit"] = int(l1_stitch_hit)
        report["rebuild_5m"] = int(rebuild_5m)
        report["l1_hit_rate"] = l1_hit_rate
        if l1_tau_cov:
            report["l1_tau_coverage"] = dict(l1_tau_cov)
            timing["l1_tau_coverage_rate"] = float(l1_tau_cov.get("rate") or 0.0)
        report["lookback"] = lookback
        report["minute_period"] = period
        if covered_list is not None:
            report["minute_days"] = len(covered_list)
            report["minute_span"] = minute_span
        report["tau_end_hm"] = resolved_end
        report["zero_prefix"] = bool(zero_prefix)
        y_spec = report.get("y_spec")
        if isinstance(y_spec, dict):
            y_spec = dict(y_spec)
            y_spec["tau_end_hm"] = resolved_end
            y_spec["zero_prefix"] = bool(zero_prefix)
            report["y_spec"] = y_spec
            for mk in ("return_model", "return_model_research", "tree_return_model"):
                blob = report.get(mk)
                if isinstance(blob, dict) and isinstance(blob.get("y_spec"), dict):
                    ys2 = dict(blob["y_spec"])
                    ys2["tau_end_hm"] = resolved_end
                    ys2["zero_prefix"] = bool(zero_prefix)
                    blob["y_spec"] = ys2
        report["minute_codes_hit"] = minute_hit
        report["minute_codes_miss"] = minute_codes_miss
        report["minute_codes_universe"] = len(codes)
        report["minute_cache_only"] = True
        if not _job_alive():
            report["success"] = False
            report["error"] = "任务已中断或被接替"
            report["orphaned"] = True
            report["persisted"] = {"success": False, "skipped": True, "orphaned": True}
            return _attach_ridge_role_flags(report, ridge_model_path(horizon))
        if report.get("success"):
            save_ridge_last_report(horizon, report)
        if persist and report.get("success"):
            saved = persist_ridge_model(
                horizon,
                report,
                note=note or f"api {horizon}-ridge persist",
                force=bool(force_promote),
                role=persist_role,
            )
            from core.research.holdout import stamp_persist_outcome

            stamp_persist_outcome(report, saved)
            if not report.get("promote_gate"):
                report["promote_gate"] = horizon_promote_gate(report)
        else:
            report["persisted"] = {"success": False, "skipped": True}
            live = load_ridge_model(horizon)
            report["live_model_present"] = bool(live)
            if report.get("success") and not report.get("promote_gate"):
                report["promote_gate"] = horizon_promote_gate(report)
        return _attach_ridge_role_flags(report, ridge_model_path(horizon))

    def _get_horizon_ridge_model(self, horizon: str) -> Dict[str, Any]:
        from core.research.horizon_prob import horizon_promote_gate
        from core.research.horizon_ridge import (
            load_ridge_last_report,
            load_ridge_model,
            ridge_model_path,
        )
        from core.research.holdout import model_fit_id

        hn = horizon[1:]
        doc = load_ridge_model(horizon)
        last = load_ridge_last_report(horizon)
        chosen, use_last = _select_ridge_desk_doc(doc, last)
        if not chosen:
            out = {
                "success": False,
                "exists": False,
                "path": ridge_model_path(horizon),
                "last_report_exists": bool(last),
                "note": f"尚无 ŷ_τ{hn} 模型；POST /api/quant/{horizon}-ridge persist=true",
            }
            if last:
                out["promote_gate"] = horizon_promote_gate(last)
                out["oos"] = last.get("oos")
                out["recommended_backend"] = last.get("recommended_backend")
                out["backends"] = last.get("backends")
                # 供 UI 前缀滑条按「上次拟合」恢复（无 live 时 desk 仍可读 last）
                out["last_report"] = last
                for k in ("tau_end_hm", "fitted_at", "y_spec", "tau_grid", "zero_prefix"):
                    if last.get(k) is not None and out.get(k) is None:
                        out[k] = last.get(k)
            return _attach_ridge_role_flags(out, ridge_model_path(horizon), live_present=False)
        gate_src = last if use_last else (doc if not doc.get("_shadow") else (last or doc))
        packed = {
            **chosen,
            "success": True,
            "exists": True,
            "path": ridge_model_path(horizon),
            "promoted": (not use_last) and not bool(chosen.get("_shadow")),
            "shadow": bool(use_last) or bool(chosen.get("_shadow")),
            "last_report_exists": bool(last),
            "promote_gate": horizon_promote_gate(gate_src),
        }
        # 因子表 / 对照：落盘缺 backends 时从同窗 last_report 补
        def _merge_backend_side(be_out: Dict[str, Any], be_src: Dict[str, Any], key: str) -> None:
            if key in be_out:
                return
            pack = be_src.get(key)
            if isinstance(pack, dict) and isinstance(
                pack.get("return_model") or pack.get("return_model_research"), dict
            ):
                be_out[key] = pack

        if isinstance(last, dict) and isinstance(last.get("backends"), dict):
            be_out = (
                dict(packed["backends"])
                if isinstance(packed.get("backends"), dict)
                else {}
            )
            be_last = last["backends"]
            same_fit = bool(
                model_fit_id(packed)
                and model_fit_id(last)
                and model_fit_id(packed) == model_fit_id(last)
            )
            if use_last or same_fit or not be_out:
                _merge_backend_side(be_out, be_last, "ridge")
                _merge_backend_side(be_out, be_last, "tree")
                if be_out:
                    packed["backends"] = be_out
        if isinstance(last, dict):
            oos_last = last.get("oos") if isinstance(last.get("oos"), dict) else {}
            # 整包 OOS 仅画 last 时回填；勿把 holdout=0 last 挂到槽文档
            if use_last and not isinstance(packed.get("oos"), dict) and oos_last:
                packed["oos"] = dict(oos_last)
            elif use_last and isinstance(packed.get("oos"), dict) and oos_last:
                # 补嵌套对照：落盘常只有胜出侧扁平 oos，缺 oos.tree / oos.ridge
                oos_out = dict(packed["oos"])
                if "ridge" not in oos_out and isinstance(oos_last.get("ridge"), dict):
                    oos_out["ridge"] = oos_last["ridge"]
                if "tree" not in oos_out and isinstance(oos_last.get("tree"), dict):
                    oos_out["tree"] = oos_last["tree"]
                packed["oos"] = oos_out
            # 嵌套对照 / tree_oos / ridge_oos / 训测日：仅 use_last 才从 last 补，
            # 避免执行全样本 last 污染研究槽对照板
            if use_last:
                if (
                    isinstance(packed.get("oos"), dict)
                    and "tree" not in packed["oos"]
                    and isinstance(last.get("tree_oos"), dict)
                    and last.get("tree_oos")
                ):
                    packed["oos"] = dict(packed["oos"])
                    packed["oos"]["tree"] = last["tree_oos"]
                if packed.get("tree_oos") is None and isinstance(
                    last.get("tree_oos"), dict
                ):
                    packed["tree_oos"] = last["tree_oos"]
                if packed.get("ridge_oos") is None and isinstance(
                    last.get("ridge_oos"), dict
                ):
                    packed["ridge_oos"] = last["ridge_oos"]
                # 扁平 oos 缺训/测行数时从嵌套对照侧回填（旧落盘 / 仅胜出侧）
                if isinstance(packed.get("oos"), dict):
                    oos_fix = packed["oos"]
                    if oos_fix.get("n_train") is None or oos_fix.get("n_test") is None:
                        for side in (
                            oos_fix.get("ridge"),
                            oos_fix.get("tree"),
                            packed.get("ridge_oos"),
                            packed.get("tree_oos"),
                            last.get("ridge_oos"),
                            last.get("tree_oos"),
                        ):
                            if not isinstance(side, dict):
                                continue
                            if (
                                oos_fix.get("n_train") is None
                                and side.get("n_train") is not None
                            ):
                                oos_fix["n_train"] = side.get("n_train")
                            if (
                                oos_fix.get("n_test") is None
                                and side.get("n_test") is not None
                            ):
                                oos_fix["n_test"] = side.get("n_test")
                            if (
                                oos_fix.get("n_train") is not None
                                and oos_fix.get("n_test") is not None
                            ):
                                break
        # LightGBM gain / 特征包：仅画 last 或同窗同 fit 时从 last_report 补
        _same_fit_last = bool(
            isinstance(last, dict)
            and model_fit_id(packed)
            and model_fit_id(last)
            and model_fit_id(packed) == model_fit_id(last)
        )
        _allow_last_side = bool(use_last or _same_fit_last)
        if (
            _allow_last_side
            and not (
                isinstance(packed.get("feature_importance"), list)
                and packed.get("feature_importance")
            )
            and isinstance(last, dict)
            and isinstance(last.get("feature_importance"), list)
            and last.get("feature_importance")
        ):
            packed["feature_importance"] = last["feature_importance"]
        if (
            _allow_last_side
            and packed.get("tree_success") is None
            and isinstance(last, dict)
            and last.get("tree_success") is not None
        ):
            packed["tree_success"] = last.get("tree_success")
            if last.get("tree_error"):
                packed["tree_error"] = last.get("tree_error")
        # alpha_τh 特征包：落盘缺字段时从 last_report 补（同 fit / 画 last）
        if _allow_last_side and isinstance(last, dict):
            for pk in (
                "feature_pack",
                "feature_pack_key",
                "feature_pack_formula",
                "include_alpha_th",
                "n_alpha_th_features",
            ):
                if packed.get(pk) is None and last.get(pk) is not None:
                    packed[pk] = last.get(pk)
        rm_main = packed.get("return_model")
        if isinstance(rm_main, dict):
            packed["return_model_research"] = dict(rm_main)
        elif isinstance(packed.get("return_model_research"), dict):
            packed["return_model"] = dict(packed["return_model_research"])
        backend = (
            packed.get("backend")
            or packed.get("recommended_backend")
            or (
                rm_main.get("backend")
                if isinstance(rm_main, dict)
                else None
            )
            or "ridge"
        )
        packed["backend"] = backend
        packed["recommended_backend"] = packed.get("recommended_backend") or backend
        return _attach_ridge_role_flags(packed, ridge_model_path(horizon))

    def _start_horizon_ridge_job(
        self,
        horizon: str,
        *,
        lookback: int = 120,
        watching_limit: int = MODEL_FIT_MAX_SIZE,
        ridge_lambda: float = 1.0,
        gap_trigger_pct: float = 2.0,
        minute_period: str = "5",
        tau_hm: Optional[str] = None,
        tau_end_hm: Optional[str] = None,
        note: str = "",
        holdout_trading_days: int = 20,
        cross_section_zscore: bool = True,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """后台 ŷ_τ horizon 拟合；不写盘。

        须透传 ``tau_end_hm``（与 UI 前缀滑条对齐）；日截面 Z 恒开。
        """
        from core import job_progress

        hn = horizon[1:]
        slot = getattr(job_progress, f"{horizon}_ridge_job")
        run_fn = getattr(self, f"run_{horizon}_ridge_experiment")

        kwargs = dict(
            lookback=lookback,
            watching_limit=watching_limit,
            ridge_lambda=ridge_lambda,
            gap_trigger_pct=gap_trigger_pct,
            minute_period=minute_period,
            tau_hm=tau_hm,
            tau_end_hm=tau_end_hm,
            persist=False,
            note=note or "",
            holdout_trading_days=holdout_trading_days,
            cross_section_zscore=bool(cross_section_zscore),
        )
        return _start_ridge_fit_job(
            slot=slot,
            kind=f"{horizon}_ridge",
            message=f"ŷ_τ{hn} 拟合中…",
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            worker_fn=lambda progress_cb=None, job_alive_cb=None: run_fn(
                **kwargs,
                progress_cb=progress_cb,
                job_alive_cb=job_alive_cb,
            ),
        )

    @records_experiment("t30_ridge")
    @consume_materialized
    def run_t30_ridge_experiment(self, **kwargs):
        return self._run_horizon_ridge_experiment("t30", **kwargs)

    def get_t30_ridge_model(self):
        return self._get_horizon_ridge_model("t30")

    def start_t30_ridge_job(self, **kwargs):
        return self._start_horizon_ridge_job("t30", **kwargs)

    @records_experiment("t45_ridge")
    @consume_materialized
    def run_t45_ridge_experiment(self, **kwargs):
        return self._run_horizon_ridge_experiment("t45", **kwargs)

    def get_t45_ridge_model(self):
        return self._get_horizon_ridge_model("t45")

    def start_t45_ridge_job(self, **kwargs):
        return self._start_horizon_ridge_job("t45", **kwargs)

    @records_experiment("t60_ridge")
    @consume_materialized
    def run_t60_ridge_experiment(self, **kwargs):
        return self._run_horizon_ridge_experiment("t60", **kwargs)

    def get_t60_ridge_model(self):
        return self._get_horizon_ridge_model("t60")

    def start_t60_ridge_job(self, **kwargs):
        return self._start_horizon_ridge_job("t60", **kwargs)

    @records_experiment("t75_ridge")
    @consume_materialized
    def run_t75_ridge_experiment(self, **kwargs):
        return self._run_horizon_ridge_experiment("t75", **kwargs)

    def get_t75_ridge_model(self):
        return self._get_horizon_ridge_model("t75")

    def start_t75_ridge_job(self, **kwargs):
        return self._start_horizon_ridge_job("t75", **kwargs)

    @records_experiment("t90_ridge")
    @consume_materialized
    def run_t90_ridge_experiment(self, **kwargs):
        return self._run_horizon_ridge_experiment("t90", **kwargs)

    def get_t90_ridge_model(self):
        return self._get_horizon_ridge_model("t90")

    def start_t90_ridge_job(self, **kwargs):
        return self._start_horizon_ridge_job("t90", **kwargs)


    @records_experiment("factor_ols_pool")
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

    def bars_status(self, *, watching_limit: int = WATCHING_MAX_SIZE) -> Dict[str, Any]:
        """观察池日线末 bar 覆盖（研究枢纽 UI）。"""
        from quant.research.bars_status import build_bars_status

        return build_bars_status(watching_limit=watching_limit)

    def bars_integrity(
        self, *, watching_limit: int = WATCHING_MAX_SIZE, days: int = 22
    ) -> Dict[str, Any]:
        """观察池日线逐日是否在仓（格子图，只读）。"""
        from quant.research.bars_integrity import build_daily_integrity

        return build_daily_integrity(watching_limit=watching_limit, days=days)

    def minute_integrity(
        self, *, watching_limit: int = WATCHING_MAX_SIZE, days: int = 22
    ) -> Dict[str, Any]:
        """观察池 5 分钟逐日头/尾是否齐（格子图，只读）。"""
        from quant.research.bars_integrity import build_minute_integrity

        return build_minute_integrity(watching_limit=watching_limit, days=days)

    def minute_day_slots(self, code: str, date: str) -> Dict[str, Any]:
        """单票单日 48 根 5 分钟是否在仓。"""
        from quant.research.bars_integrity import build_minute_day_slots

        return build_minute_day_slots(code, date)

    def run_bars_refresh(
        self,
        *,
        watching_limit: int = WATCHING_MAX_SIZE,
        lookback: Optional[int] = None,
        mode: str = "topup",
        progress_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """同步：更新观察池日线（``mode=topup|full``）。"""
        from core.data.policy import BARS_DAILY_LOOKBACK
        from quant.research.bars_status import refresh_bars_only

        return refresh_bars_only(
            watching_limit=watching_limit,
            lookback=int(lookback or BARS_DAILY_LOOKBACK),
            mode=mode,
            progress_cb=progress_cb,
        )

    def start_bars_refresh_job(
        self,
        *,
        watching_limit: int = WATCHING_MAX_SIZE,
        lookback: Optional[int] = None,
        mode: str = "topup",
    ) -> Dict[str, Any]:
        """后台 Job：仅更新日线；轮询 ``GET /api/jobs/bars-refresh``。"""
        import threading

        from core.data.policy import BARS_DAILY_LOOKBACK
        from core.job_progress import bars_refresh_job
        from quant.research.watching_universe import clamp_watching_limit

        lookback = int(lookback or BARS_DAILY_LOOKBACK)

        bars_refresh_job.reclaim_if_stale()
        if bars_refresh_job.is_running():
            return {
                "ok": True,
                "success": True,
                "background": True,
                "reused": True,
                "job": bars_refresh_job.get(),
            }

        watch_limit = clamp_watching_limit(watching_limit or WATCHING_MAX_SIZE, WATCHING_MAX_SIZE)
        mode_s = str(mode or "topup").strip().lower()
        if mode_s not in ("full", "topup"):
            mode_s = "topup"
        try:
            from core.watching.store import read_watching

            n_watch_all = len(list((read_watching() or {}).get("watchlist") or []))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            n_watch_all = watch_limit
        n_watch = min(n_watch_all, watch_limit) if n_watch_all else watch_limit
        # 进度分母=观察池真实票数（与 5m Job 一致；不再 +5 垫高）
        job_total = max(1, n_watch)
        phase = "增量补齐（L0）…" if mode_s == "topup" else "强更日 K（L0）…"

        job_id = bars_refresh_job.start(
            kind="bars_refresh",
            total=job_total,
            message="排队中…",
        )

        def _progress(msg: str, cur: int = 0, tot: int = 0) -> None:
            t = max(1, int(tot or n_watch or 1))
            c = max(0, min(t, int(cur or 0)))
            bars_refresh_job.update(
                current=c,
                total=t,
                message=str(msg or phase),
                job_id=job_id,
            )

        def _worker() -> None:
            stop_hb = threading.Event()

            def _heartbeat() -> None:
                while not stop_hb.wait(8.0):
                    if not bars_refresh_job.touch(job_id=job_id):
                        return

            hb = threading.Thread(
                target=_heartbeat, name=f"bars-hb-{job_id}", daemon=True
            )
            hb.start()
            try:
                if bars_refresh_job.is_cancel_requested():
                    bars_refresh_job.finish(error="已取消", job_id=job_id)
                    return
                result = self.run_bars_refresh(
                    watching_limit=watch_limit,
                    lookback=lookback,
                    mode=mode_s,
                    progress_cb=_progress,
                )
                if bars_refresh_job.is_cancel_requested():
                    bars_refresh_job.finish(error="已取消", job_id=job_id)
                    return
                if not result.get("success"):
                    bars_refresh_job.finish(
                        error=str(result.get("error") or "日线更新失败"),
                        result=result,
                        job_id=job_id,
                    )
                    return
                bars_refresh_job.finish(result=result, job_id=job_id)
            except Exception as e:
                logger.exception("unexpected error in bars_refresh worker")
                bars_refresh_job.finish(error=str(e), job_id=job_id)
            finally:
                stop_hb.set()
                try:
                    from core.research.horizon_l1 import release_allocator_cache

                    release_allocator_cache()
                except Exception:  # noqa: BLE001
                    logger.debug("bars refresh release allocator cache failed", exc_info=True)

        threading.Thread(
            target=_worker, name=f"bars-{job_id}", daemon=True
        ).start()
        return {
            "ok": True,
            "success": True,
            "background": True,
            "job": bars_refresh_job.get(),
        }

    def materialize_catalog(self) -> Dict[str, Any]:
        from core.research.materialize import catalog_payload

        return {"success": True, "ok": True, **catalog_payload()}

    def run_materialize(
        self,
        *,
        families: Optional[List[str]] = None,
        clocks: Optional[List[str]] = None,
        do_l1: bool = True,
        do_l1_tau_grid: bool = False,
        do_l2: bool = True,
        do_session_open_l2: bool = False,
        cycle_day: Optional[str] = None,
        lookback: Optional[int] = None,
        watching_limit: int = WATCHING_MAX_SIZE,
        progress_cb: Optional[Any] = None,
        cancel_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """同步物化特征（L1/L2）（可选 τ 网格 L1）。L1 与 open L2 共用 ``lookback``。

        ``do_session_open_l2``：盘中补齐会话日官方 open 立方。
        """
        from core.data.policy import BARS_DAILY_LOOKBACK
        from core.research.materialize import run_materialize

        return run_materialize(
            families=families,
            clocks=clocks,
            do_l1=bool(do_l1),
            do_l1_tau_grid=bool(do_l1_tau_grid),
            do_l2=bool(do_l2),
            do_session_open_l2=bool(do_session_open_l2),
            cycle_day=cycle_day,
            lookback=int(lookback or BARS_DAILY_LOOKBACK),
            watching_limit=int(watching_limit or WATCHING_MAX_SIZE),
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )

    def start_materialize_job(
        self,
        *,
        families: Optional[List[str]] = None,
        clocks: Optional[List[str]] = None,
        do_l1: bool = True,
        do_l1_tau_grid: bool = False,
        do_l2: bool = True,
        do_session_open_l2: bool = False,
        cycle_day: Optional[str] = None,
        lookback: Optional[int] = None,
        watching_limit: int = WATCHING_MAX_SIZE,
    ) -> Dict[str, Any]:
        """后台 Job：物化特征（L1/L2）；轮询 ``GET /api/jobs/materialize-l1l2``。"""
        import threading

        from core.data.policy import BARS_DAILY_LOOKBACK
        from core.job_progress import materialize_job
        from quant.research.watching_universe import clamp_watching_limit

        materialize_job.reclaim_if_stale()
        # open / τ / 混合 分 kind，避免日线与分钟线互抢同一 in-flight 结果
        if do_session_open_l2:
            job_kind = "materialize_open"
        elif do_l1_tau_grid and not do_l1:
            job_kind = "materialize_tau"
        elif do_l1 and not do_l1_tau_grid:
            job_kind = "materialize_open"
        else:
            job_kind = "materialize_l1l2"

        watch_limit = clamp_watching_limit(
            watching_limit or WATCHING_MAX_SIZE, WATCHING_MAX_SIZE
        )
        if do_session_open_l2:
            steps = 1
        else:
            steps = (1 if do_l1 else 0) + (1 if do_l1_tau_grid else 0) + (1 if do_l2 else 0)
        # 原子开槽，避免 is_running→start 之间双开
        job_id, started_or_running = materialize_job.try_start(
            kind=job_kind,
            total=max(1, steps),
            message="排队中…",
        )
        if job_id is None:
            running = started_or_running or {}
            running_kind = str(running.get("kind") or "")
            if running_kind and running_kind != job_kind:
                _kind_zh = {
                    "materialize_open": "日线物化",
                    "materialize_tau": "分钟 τ 物化",
                    "materialize_l1l2": "混合物化",
                }
                peer = _kind_zh.get(running_kind, running_kind or "物化")
                want = _kind_zh.get(job_kind, job_kind or "物化")
                return {
                    "ok": False,
                    "success": False,
                    "conflict": True,
                    "error": (
                        f"{peer}进行中，与本次{want}共用同一任务槽；"
                        f"请稍候或点一次后将自动等待对侧结束再启动"
                    ),
                    "job": running,
                }
            return {
                "ok": True,
                "success": True,
                "background": True,
                "reused": True,
                "job": running,
            }

        planned = max(1, int(steps or 2))

        def _progress(msg: str, cur: int = 0, tot: int = 0) -> None:
            """宏步骤保持 planned。子进度推进 pct。这一拍失败不影响物化。"""
            from core.research.materialize.progress import fold_materialize_progress

            try:
                try:
                    snap = materialize_job.get() or {}
                    prev_c = snap.get("current") or 0
                    prev_pct = snap.get("pct") or 0.0
                except Exception:  # noqa: BLE001
                    prev_c, prev_pct = 0, 0.0
                c, t, pct = fold_materialize_progress(
                    planned=planned,
                    prev_current=prev_c,
                    prev_pct=prev_pct,
                    cur=cur,
                    tot=tot,
                    message=str(msg or ""),
                )
                materialize_job.update(
                    current=c,
                    total=t,
                    pct=pct,
                    message=str(msg or "物化…"),
                    job_id=job_id,
                )
            except Exception:  # noqa: BLE001
                logger.debug("materialize progress dropped", exc_info=True)

        def _worker() -> None:
            stop_hb = threading.Event()

            def _heartbeat() -> None:
                while not stop_hb.wait(8.0):
                    if not materialize_job.touch(job_id=job_id):
                        return

            hb = threading.Thread(
                target=_heartbeat, name=f"mat-hb-{job_id}", daemon=True
            )
            hb.start()
            try:
                if materialize_job.is_cancel_requested():
                    materialize_job.finish(error="已取消", job_id=job_id)
                    return

                def _pcb(c: int, t: int, m: str = "") -> None:
                    _progress(m, c, t)

                def _cancel() -> bool:
                    return bool(materialize_job.is_cancel_requested())

                result = self.run_materialize(
                    families=families,
                    clocks=clocks,
                    do_l1=do_l1,
                    do_l1_tau_grid=do_l1_tau_grid,
                    do_l2=do_l2,
                    do_session_open_l2=do_session_open_l2,
                    cycle_day=cycle_day,
                    lookback=int(lookback or BARS_DAILY_LOOKBACK),
                    watching_limit=watch_limit,
                    progress_cb=_pcb,
                    cancel_cb=_cancel,
                )
                if materialize_job.is_cancel_requested():
                    materialize_job.finish(error="已取消", job_id=job_id)
                    return
                if not result.get("success"):
                    materialize_job.finish(
                        error=str(result.get("error") or "物化失败"),
                        result=result,
                        job_id=job_id,
                    )
                    return
                materialize_job.finish(result=result, job_id=job_id)
            except Exception as e:
                logger.exception("unexpected error in materialize worker")
                materialize_job.finish(error=str(e), job_id=job_id)
            finally:
                stop_hb.set()
                try:
                    from core.research.horizon_l1 import release_allocator_cache

                    release_allocator_cache()
                except Exception:  # noqa: BLE001
                    logger.debug("materialize release allocator cache failed", exc_info=True)

        threading.Thread(
            target=_worker, name=f"mat-{job_id}", daemon=True
        ).start()
        return {
            "ok": True,
            "success": True,
            "background": True,
            "job": materialize_job.get(),
        }

    def minute_status(
        self,
        *,
        watching_limit: int = WATCHING_MAX_SIZE,
        period: str = "5",
        min_span_days: int = 40,
        include_label_portrait: bool = False,
    ) -> Dict[str, Any]:
        """观察池 5m 分钟缓存覆盖（研究枢纽 UI）。"""
        from quant.research.minute_status import build_minute_status

        return build_minute_status(
            watching_limit=watching_limit,
            period=period,
            min_span_days=min_span_days,
            include_label_portrait=include_label_portrait,
        )

    def run_minute_refresh(
        self,
        *,
        watching_limit: int = WATCHING_MAX_SIZE,
        period: str = "5",
        mode: str = "full",
        progress_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """同步：预热观察池 5m 分钟线（``mode=full|topup``）。"""
        from quant.research.minute_status import refresh_minute_only

        return refresh_minute_only(
            watching_limit=watching_limit,
            period=period,
            mode=mode,
            progress_cb=progress_cb,
        )

    def start_minute_refresh_job(
        self,
        *,
        watching_limit: int = WATCHING_MAX_SIZE,
        period: str = "5",
        mode: str = "full",
    ) -> Dict[str, Any]:
        """后台 Job：预热 5m 分钟线；轮询 ``GET /api/jobs/minute-refresh``。"""
        import threading

        from core.job_progress import minute_refresh_job
        from quant.research.watching_universe import clamp_watching_limit

        minute_refresh_job.reclaim_if_stale()
        if minute_refresh_job.is_running():
            return {
                "ok": True,
                "success": True,
                "background": True,
                "reused": True,
                "job": minute_refresh_job.get(),
            }

        watch_limit = clamp_watching_limit(watching_limit or WATCHING_MAX_SIZE, WATCHING_MAX_SIZE)
        mode_s = str(mode or "full").strip().lower()
        if mode_s not in ("full", "topup"):
            mode_s = "full"
        try:
            from core.watching.store import read_watching

            n_watch_all = len(list((read_watching() or {}).get("watchlist") or []))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            n_watch_all = watch_limit
        n_watch = min(n_watch_all, watch_limit) if n_watch_all else watch_limit
        job_total = max(1, n_watch)
        phase = "增量补齐（L0）…" if mode_s == "topup" else "预热 5m…"

        job_id = minute_refresh_job.start(
            kind="minute_refresh",
            total=job_total,
            message="排队中…",
        )

        def _progress(msg: str, cur: int = 0, tot: int = 0) -> None:
            t = max(1, int(tot or n_watch or 1))
            c = max(0, min(t, int(cur or 0)))
            minute_refresh_job.update(
                current=c,
                total=job_total,
                message=str(msg or phase),
                job_id=job_id,
            )

        def _worker() -> None:
            stop_hb = threading.Event()

            def _heartbeat() -> None:
                while not stop_hb.wait(8.0):
                    if not minute_refresh_job.touch(job_id=job_id):
                        return

            hb = threading.Thread(
                target=_heartbeat, name=f"minute-hb-{job_id}", daemon=True
            )
            hb.start()
            try:
                if minute_refresh_job.is_cancel_requested():
                    minute_refresh_job.finish(error="已取消", job_id=job_id)
                    return
                result = self.run_minute_refresh(
                    watching_limit=watch_limit,
                    period=period,
                    mode=mode_s,
                    progress_cb=_progress,
                )
                if minute_refresh_job.is_cancel_requested():
                    minute_refresh_job.finish(error="已取消", job_id=job_id)
                    return
                if not result.get("success"):
                    minute_refresh_job.finish(
                        error=str(result.get("error") or "分钟预热失败"),
                        result=result,
                        job_id=job_id,
                    )
                    return
                minute_refresh_job.finish(result=result, job_id=job_id)
            except Exception as e:
                logger.exception("unexpected error in minute_refresh worker")
                minute_refresh_job.finish(error=str(e), job_id=job_id)
            finally:
                stop_hb.set()
                try:
                    from core.research.horizon_l1 import release_allocator_cache

                    release_allocator_cache()
                except Exception:  # noqa: BLE001
                    logger.debug("minute refresh release allocator cache failed", exc_info=True)

        threading.Thread(
            target=_worker, name=f"minute-{job_id}", daemon=True
        ).start()
        return {
            "ok": True,
            "success": True,
            "background": True,
            "mode": mode_s,
            "job": minute_refresh_job.get(),
        }


    @records_experiment("factor_cs_ic")
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
            from core.signal.weight_oos_gate import (
                YHAT_WEIGHTS_DECOUPLED,
                evaluate_weight_suggestion_oos,
            )

            oos_gate = evaluate_weight_suggestion_oos(
                suggestion.get("current_weights") or {},
                suggestion.get("suggested_weights") or {},
                oos_tol_pp=float(kwargs.get("oos_tol_pp") or 1.0),
            )
            suggestion["oos_gate"] = oos_gate
            # weights 与 ŷ_oo LightGBM 正交：解耦跳过时允许人审 promote（仍受健康拦截）
            if str(oos_gate.get("reason") or "") == YHAT_WEIGHTS_DECOUPLED:
                suggestion["promote_ready"] = True
            else:
                suggestion["promote_ready"] = bool(
                    oos_gate.get("ok")
                    and oos_gate.get("passed")
                    and not oos_gate.get("skipped")
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
            pass
        return suggestion

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
        items = ranked.get("book") or []
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
        report["book_count"] = len(items)
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
