"""ŷ_τc_rank：与 ŷ_oo_rank 同协议的截面排序头（单头 LambdaRank）。

标签：price[τ]→close[T]（与 ŷ_τc 同前缀族；默认 09:30=open 即 close/open−1），embargo=0。
特征：alpha_τc（z_τc_* ∪ raw_atc_*），日截面 z-score；非开盘前缀按 date|clock 成组。
面板按股落在 ``tc_rank/``；与 ŷ_τc 的 ``tc/`` 分片 fingerprint 一致时直接复制，不重算。
落盘 LambdaRank（``backend=auto`` / ``lambdarank`` 同义；``ridge`` 拒）。
研究枢纽启用只落盘；不进 y_oo_fuse。
回测/live 写入当日截面名次；相对分进合成 rank_score（买序/阈值）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Collection, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.oo_rank_lambdarank import LAMBDARANK_L2

FORMULA_TC_RANK = "rank(price[τ]->close[T])"
SCHEMA = "tc_rank_v1"
TASK = "tc_rank"
DEFAULT_MIN_NAMES = 8
DEFAULT_TOPK_TRACK = 10
DEFAULT_NDCG_K = 10
DEFAULT_HOLDOUT_TRADING_DAYS_TC_RANK = 20

_MODEL_BASENAME = "tc_rank_model.json"
_LAST_REPORT_BASENAME = "tc_rank_last_report.json"


def tc_rank_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, _MODEL_BASENAME)


def tc_rank_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, _LAST_REPORT_BASENAME)


def _load_model_file(path: str) -> Optional[Dict[str, Any]]:
    from core.research.oo_rank_lambdarank import _scrub_rank_label_meta

    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load tc_rank json failed: %s", path, exc_info=True)
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("return_model"), dict):
        return None
    if doc.get("success") is False:
        return None
    return _scrub_rank_label_meta(doc)


def load_tc_rank_model(*, prefer_research: bool = True) -> Optional[Dict[str, Any]]:
    """加载已启用模型。按槽严格取，缺文件返回 None（不跨槽回落）。

    - ``prefer_research=True``：只读研究套 sidecar
    - ``prefer_research=False``：只读执行套
    """
    from core.research.oo_rank_lambdarank import _scrub_rank_label_meta

    live = tc_rank_model_path()
    if prefer_research:
        try:
            from core.research.holdout import load_research_promoted_json

            doc = load_research_promoted_json(live)
            return _scrub_rank_label_meta(doc) if isinstance(doc, dict) else doc
        except Exception:  # noqa: BLE001
            logger.debug("tc_rank research load failed", exc_info=True)
            return None
    return _load_model_file(live)


def save_tc_rank_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    from core.research.oo_rank_lambdarank import (
        _scrub_rank_label_meta,
        stamp_oo_rank_fitted_at,
    )

    stamp_oo_rank_fitted_at(report)
    path = tc_rank_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, _scrub_rank_label_meta(report))


def load_tc_rank_last_report() -> Optional[Dict[str, Any]]:
    return _load_model_file(tc_rank_last_report_path()) or (
        _load_json_report(tc_rank_last_report_path())
    )


def _load_json_report(path: str) -> Optional[Dict[str, Any]]:
    """上次拟合草稿可以没有可推理包，只要 success。"""
    from core.research.oo_rank_lambdarank import _scrub_rank_label_meta

    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load tc_rank report failed: %s", path, exc_info=True)
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    return _scrub_rank_label_meta(doc)


def _relabel_metrics(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(raw or {})
    if "topk_mean_y_oo" in out:
        out["topk_mean_y_tc"] = out.pop("topk_mean_y_oo")
    return out


def _stamp_model(
    model: Dict[str, Any],
    *,
    backend: str,
    y_spec: Dict[str, Any],
    ndcg_k: int,
) -> Dict[str, Any]:
    out = dict(model)
    out["backend"] = backend
    out["y_spec"] = y_spec
    out["horizon_days"] = 0
    out["horizon_mode"] = "tc_rank"
    out["target"] = "tc_rank"
    out["model_role"] = "research"
    out["ndcg_k"] = int(ndcg_k)
    out.pop("shadow_only", None)
    if backend == "lambdarank":
        out["intercept"] = 0.0
    return out


def fit_tc_rank_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    min_history: int = 12,
    min_names: int = DEFAULT_MIN_NAMES,
    holdout_trading_days: int = DEFAULT_HOLDOUT_TRADING_DAYS_TC_RANK,
    l2: float = LAMBDARANK_L2,
    topk_track: int = DEFAULT_TOPK_TRACK,
    ndcg_k: int = DEFAULT_NDCG_K,
    backend: str = "auto",
    cross_section_zscore: bool = True,
    train_codes: Optional[Collection[str]] = None,
    tau_end_hm: Optional[str] = None,
    tau_hm: Optional[str] = None,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """拟合 ŷ_τc_rank。``stock_bars``: ``[{code, bars}, ...]``。不落盘。

    前缀与 ŷ_τc 同族：``tau_end_hm`` → ``resolve_tau_train_prefix``。
    拟合固定日截面 Z；``train_codes`` 给定时损失与 Holdout 缩到该子集。
    非开盘前缀按 ``date|clock`` 成 LambdaRank 组。
    ``progress_cb(cur, tot, msg)``：建面板 / LambdaRank / Holdout 阶段刷新。
    """
    from core.research.feature_standardize import reject_global_zscore
    from core.research.holdout import (
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        make_research_model,
        refuse_insufficient_holdout,
        resolve_ridge_split,
    )
    from core.research.oo_rank_lambdarank import (
        DEFAULT_GATE_N,
        DEFAULT_GATE_STRONG_N,
        _oos_day_metrics,
        fit_lambdarank,
        select_oo_rank_backend,
        stamp_oo_rank_fitted_at,
    )
    from core.research.rank_day_panels import build_rank_day_panels
    from core.signal.dual_score import get_dual_score_cfg
    from core.signal.minute_tau_grid import resolve_tau_train_prefix

    def _prog(cur: int, tot: int, msg: str) -> None:
        if not callable(progress_cb):
            return
        try:
            progress_cb(int(cur), int(tot), str(msg or ""))
        except Exception:  # noqa: BLE001
            logger.debug("tc_rank progress_cb failed", exc_info=True)

    err_cs = reject_global_zscore(cross_section_zscore)
    if err_cs:
        return {"success": False, "error": err_cs, "task": TASK}

    # 物化刚扫过的整窗立方还在进程缓存里。面板起来之前先卸掉。
    try:
        from core.research.horizon_l1 import release_allocator_cache

        release_allocator_cache()
    except Exception:  # noqa: BLE001
        logger.debug("tc_rank release before panel failed", exc_info=True)

    # 产品默认 09:30（与 UI τc 组滑条一致）；勿在未传 end 时吃 enable_minute_tau→整网 11:00
    end_raw = tau_end_hm
    if end_raw is None and tau_hm is None:
        end_raw = "09:30"
    ds = get_dual_score_cfg()
    prefix = resolve_tau_train_prefix(
        tau_end_hm=end_raw,
        tau_hm=tau_hm,
        enable_minute_tau=bool(ds.get("enable_minute_tau")),
    )
    if prefix.get("error"):
        return {
            "success": False,
            "error": prefix["error"],
            "task": TASK,
        }
    tau_key = str(prefix.get("tau_hm") or "open")
    tau_grid = prefix.get("tau_grid")
    resolved_end = str(prefix.get("tau_end_hm") or "09:30")[:5]
    zero_prefix = bool(prefix.get("zero_prefix"))
    use_minute = bool(prefix.get("use_minute"))

    bars = [it for it in (stock_bars or []) if isinstance(it, dict)]
    calendar = calendar_dates_from_stock_bars(bars)
    if holdout_trading_days is None:
        hold_n = int(DEFAULT_HOLDOUT_TRADING_DAYS_TC_RANK)
    else:
        try:
            hold_n = max(0, int(holdout_trading_days))
        except (TypeError, ValueError):
            hold_n = int(DEFAULT_HOLDOUT_TRADING_DAYS_TC_RANK)
    # 日历预切：raw 入模列只看训窗（与旧 collect→feature_dates 同口径）
    _pre_tr, _pre_te, split_pre = resolve_ridge_split(
        calendar,
        holdout_trading_days=hold_n,
        label_horizon_days=0,
        calendar_dates=calendar,
    )
    blocked_pre = refuse_insufficient_holdout(split_pre)
    if blocked_pre:
        blocked_pre["task"] = TASK
        return blocked_pre
    fit_dates = sorted(split_pre.get("train_days") or [])
    panel_stats: Dict[str, Any] = {}
    try:
        _prog(0, 1, "ŷ_τc_rank 组面板…")
        days_all = build_rank_day_panels(
            bars,
            family="tc_rank",
            fit_dates=fit_dates,
            min_history=int(min_history),
            min_names=int(min_names),
            stats_out=panel_stats,
            tau_hm=tau_key,
            tau_grid=tau_grid,
            progress_cb=progress_cb,
        )
    except Exception as exc:  # noqa: BLE001
        # MaterializeRequired 等：透传给 consume_materialized
        from core.research.materialize.consumer import MaterializeRequired

        if isinstance(exc, MaterializeRequired):
            raise
        logger.debug("tc_rank panel failed", exc_info=True)
        return {
            "success": False,
            "error": str(exc) or "tc_rank_panel_failed",
            "task": TASK,
            "tau_end_hm": resolved_end,
            "zero_prefix": zero_prefix,
        }
    n_stocks = int(panel_stats.get("n_stocks") or 0)
    n_rows = int(panel_stats.get("n_rows") or 0)
    if n_rows < 20 or not days_all:
        return {
            "success": False,
            "error": f"rows_insufficient n={n_rows}（需≥20）",
            "task": TASK,
            "n_rows": n_rows,
            "n_stocks": n_stocks,
            "tau_end_hm": resolved_end,
            "zero_prefix": zero_prefix,
            "tau_hm": tau_key,
            "tau_grid": list(tau_grid) if tau_grid else None,
        }

    # 与 oo_rank 同构：按交易日切 Holdout（date[:10]；多钟同日同侧）
    date_rows = [str(d.get("date") or "")[:10] for d in days_all]
    train_idx, test_idx, split_meta = resolve_ridge_split(
        date_rows,
        holdout_trading_days=hold_n,
        label_horizon_days=0,
        calendar_dates=calendar,
    )
    blocked_split = refuse_insufficient_holdout(split_meta)
    if blocked_split:
        blocked_split["task"] = TASK
        return blocked_split
    days_tr = [days_all[i] for i in train_idx]
    days_te = [days_all[i] for i in test_idx]
    # Holdout=0（执行全样本）test 为空属正常；与 oo_rank 同构只卡训练日
    if len(days_tr) < 5:
        return {
            "success": False,
            "error": f"train_days_insufficient n={len(days_tr)}",
            "task": TASK,
            "n_days": len(days_all),
            "n_train_days": len(days_tr),
            "n_test_days": len(days_te),
            "tau_end_hm": resolved_end,
            "zero_prefix": zero_prefix,
        }
    if hold_n > 0 and len(days_te) < 1:
        return {
            "success": False,
            "error": f"split_insufficient train={len(days_tr)} test={len(days_te)}",
            "task": TASK,
            "n_days": len(days_all),
            "n_train_days": len(days_tr),
            "n_test_days": len(days_te),
            "tau_end_hm": resolved_end,
            "zero_prefix": zero_prefix,
        }

    force_backend = str(backend or "auto").strip().lower()
    if force_backend == "ridge":
        return {
            "success": False,
            "error": "ŷ_τc_rank 已下线 Ridge 对照；请用 auto / lambdarank",
            "task": TASK,
            "backend": force_backend,
        }
    if force_backend not in {"auto", "lambdarank"}:
        return {
            "success": False,
            "error": f"unsupported backend={force_backend}；支持 auto / lambdarank",
            "task": TASK,
        }

    kept_names = list(days_tr[0].get("names") or [])
    train_allow = {
        str(c).strip() for c in (train_codes or []) if str(c or "").strip()
    } or None
    try:
        _prog(0, 1, f"ŷ_τc_rank LambdaRank · 训 {len(days_tr)} 日…")
        fit_tr = fit_lambdarank(
            days_tr,
            feature_names=kept_names,
            l2=float(l2),
            cross_section_zscore=True,
            train_codes=train_allow,
        )
    except Exception as exc:  # noqa: BLE001
        from core.research.materialize.consumer import MaterializeRequired

        if isinstance(exc, MaterializeRequired):
            raise
        return {
            "success": False,
            "error": str(exc) or "lambdarank_fit_failed",
            "task": TASK,
            "n_days": len(days_all),
            "backend": "lambdarank",
            "tau_end_hm": resolved_end,
        }
    if not fit_tr.get("success"):
        return {
            "success": False,
            "error": fit_tr.get("error") or "lambdarank_fit_failed",
            "task": TASK,
            "n_days": len(days_all),
            "backend": "lambdarank",
            "tau_end_hm": resolved_end,
            "zero_prefix": zero_prefix,
        }

    _prog(0, 1, f"ŷ_τc_rank Holdout 评估 · 测 {len(days_te)} 日…")
    oos_rank = _relabel_metrics(
        _oos_day_metrics(
            days_te,
            fit_tr,
            topk_track=topk_track,
            ndcg_k=ndcg_k,
            train_codes=train_allow,
        )
    )

    recommended = select_oo_rank_backend()
    chosen = "lambdarank"

    tau_label = "open" if zero_prefix or not use_minute else tau_key
    y_spec_base = {
        "formula": FORMULA_TC_RANK,
        "unit": "score",
        "anchor": "open[T]" if tau_label == "open" else f"price[{tau_label}]",
        "label": "rank(price[τ]->close[T]) cross-section",
        "tau": tau_label,
        "tau_end_hm": resolved_end,
        "zero_prefix": zero_prefix,
    }
    if tau_grid:
        y_spec_base["tau_grid"] = list(tau_grid)

    def _pack(backend_key: str, holdout_src: Dict[str, Any]) -> Dict[str, Any]:
        note = (
            "LambdaRank 相对分；不进 y_oo_fuse；进合成 rank_score（买序/阈值）；"
            "前缀与 ŷ_τc 同组"
        )
        y_spec = dict(y_spec_base, backend=backend_key, note=note)
        research = _stamp_model(
            make_research_model(holdout_src),
            backend=backend_key,
            y_spec=y_spec,
            ndcg_k=ndcg_k,
        )
        return {
            "return_model": research,
            "return_model_research": research,
            "y_spec": y_spec,
        }

    lambda_pack = _pack("lambdarank", dict(fit_tr))
    backends: Dict[str, Any] = {"lambdarank": lambda_pack}
    chosen_pack = backends[chosen]
    model = chosen_pack["return_model"]
    hold_n_meta = int(split_meta.get("holdout_trading_days") or hold_n)

    report: Dict[str, Any] = {
        "success": True,
        "task": TASK,
        "schema": SCHEMA,
        "backend": chosen,
        "recommended_backend": recommended,
        "solver": str(model.get("solver") or ""),
        "n_days": len(days_all),
        "n_train_days": len(days_tr),
        "n_test_days": len(days_te),
        "n_stocks": n_stocks,
        "sample_count": int(model.get("sample_count") or 0),
        "n_features": len(kept_names),
        "ndcg_k": int(ndcg_k),
        "tau_hm": tau_label,
        "tau_end_hm": resolved_end,
        "zero_prefix": zero_prefix,
        "tau_grid": list(tau_grid) if tau_grid else None,
        "oos": {
            "tc_rank": oos_rank,
            "holdout_trading_days": hold_n_meta,
            "topk_track": int(topk_track),
            "ndcg_k": int(ndcg_k),
            "gate_n": int(
                oos_rank.get("gate_n")
                if oos_rank.get("gate_n") is not None
                else DEFAULT_GATE_N
            ),
            "gate_strong_n": int(
                oos_rank.get("gate_strong_n")
                if oos_rank.get("gate_strong_n") is not None
                else DEFAULT_GATE_STRONG_N
            ),
            "recommended_backend": recommended,
        },
        "backends": backends,
        "return_model": model,
        "return_model_research": chosen_pack["return_model_research"],
        "y_spec": chosen_pack["y_spec"],
        "cross_section_zscore": True,
        "zscore_universe": "pool",
        "note": (
            "ŷ_τc_rank=LambdaRank；只训 Holdout 前一套；"
            "前缀与 ŷ_τc 同组（默认 09:30）；"
            "启用研究→研究槽；启用执行=研究槽晋级；"
            "OOS 主看 Gate lift；IC 仅对照；"
            "不进 y_oo_fuse；进合成 rank_score（买序/阈值）"
        ),
    }
    attach_holdout_meta(report, split_meta)
    stamp_oo_rank_fitted_at(report)
    return report


def persist_tc_rank_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    role: str = "live",
    deploy_full_sample: bool = False,
) -> Dict[str, Any]:
    """拟合研究=报告→研究槽；晋级执行=研究槽→执行槽（或全样本报告直写执行槽）。"""
    from core.numbers import now_iso_utc
    from core.research.holdout import (
        MODEL_ROLE_LIVE,
        MODEL_ROLE_RESEARCH,
        parse_persist_role,
        refuse_ungated_full_sample,
        require_research_suite,
        research_model_path,
        select_fit_return_model,
        stamp_live_suite_doc,
    )
    from core.research.oo_rank_lambdarank import (
        _backend_pack_from_report,
        _oo_rank_model_valid,
        _oo_rank_use_booster,
        _scrub_rank_label_meta,
        _scrub_rank_oos,
        select_oo_rank_backend,
        stamp_oo_rank_fitted_at,
    )

    live = tc_rank_model_path()
    role_n, role_err = parse_persist_role(role)
    if role_err:
        return {"success": False, "error": role_err}
    if role_n == MODEL_ROLE_LIVE:
        research_doc, err = require_research_suite(live)
        has_research = isinstance(research_doc, dict) and not err
        if deploy_full_sample:
            from core.research.holdout import require_full_sample_report

            blocked_fs = require_full_sample_report(report)
            if blocked_fs:
                return blocked_fs
            stamp_oo_rank_fitted_at(report)
            backend = select_oo_rank_backend()
            pack = _backend_pack_from_report(report, backend)
            if not isinstance(pack, dict) or not isinstance(pack.get("return_model"), dict):
                return {"success": False, "error": "lambdarank pack missing（全样本重训失败）"}
            view = dict(report)
            view["return_model"] = pack.get("return_model")
            view.pop("return_model_research", None)
            model = select_fit_return_model(view)
            if not isinstance(model, dict) or not _oo_rank_use_booster(model):
                return {"success": False, "error": "全样本报告缺少可用 LambdaRank 包"}
            model = _scrub_rank_label_meta(dict(model))
            model["model_role"] = MODEL_ROLE_LIVE
            model["backend"] = backend
            if has_research:
                doc = stamp_live_suite_doc(
                    research_doc,
                    note=note
                    or "tc_rank full-sample refit → live（研究套 OOS 作门禁溯源）",
                )
                if isinstance(research_doc.get("oos"), dict):
                    doc["oos"] = dict(research_doc["oos"])
                    doc["gate_oos_source"] = "research"
            else:
                blocked_gate = refuse_ungated_full_sample(False)
                if blocked_gate:
                    return blocked_gate
                doc = {}
            doc["return_model"] = model
            doc.pop("return_model_research", None)
            doc["backend"] = backend
            doc["recommended_backend"] = backend
            doc["model_role"] = MODEL_ROLE_LIVE
            doc["deploy_full_sample"] = True
            doc["promoted_from"] = "full_sample_refit"
            doc["backends"] = {
                "lambdarank": {
                    "return_model": dict(model),
                    "oos": dict(doc.get("oos") or {})
                    if isinstance(doc.get("oos"), dict)
                    else {},
                }
            }
            for k in (
                "sample_count",
                "n_days",
                "n_train_days",
                "n_test_days",
                "holdout_trading_days",
                "y_spec",
                "tau_end_hm",
            ):
                if report.get(k) is not None:
                    doc[k] = report.get(k)
            from core.research.holdout import stamp_full_sample_live_fitted_at

            stamp_full_sample_live_fitted_at(doc, source=report)
            from core.research.model_versions import refuse_incomplete_suite_doc

            blocked_inc = refuse_incomplete_suite_doc(doc, sink="执行套")
            if blocked_inc:
                return blocked_inc
            os.makedirs(os.path.dirname(live), exist_ok=True)
            atomic_write_json(live, doc)
            out_fs = {
                "success": True,
                "path": live,
                "fitted_at": doc.get("fitted_at"),
                "promoted_at": doc.get("promoted_at"),
                "model_role": MODEL_ROLE_LIVE,
                "backend": backend,
                "recommended_backend": backend,
                "promoted_from": "full_sample_refit",
                "deploy_full_sample": True,
                "live": live,
            }
            from core.research.model_versions import archive_persist_safe

            archive_persist_safe("tc_rank", MODEL_ROLE_LIVE, out_fs)
            return out_fs
        if err:
            return err
        doc = stamp_live_suite_doc(
            research_doc, note=note or "tc_rank research → live promote"
        )
        from core.research.model_versions import refuse_incomplete_suite_doc

        blocked_inc = refuse_incomplete_suite_doc(doc, sink="执行套")
        if blocked_inc:
            return blocked_inc
        os.makedirs(os.path.dirname(live), exist_ok=True)
        atomic_write_json(live, doc)
        out_live = {
            "success": True,
            "path": live,
            "promoted_at": doc.get("promoted_at"),
            "model_role": MODEL_ROLE_LIVE,
            "backend": doc.get("backend"),
            "recommended_backend": doc.get("recommended_backend") or doc.get("backend"),
            "promoted_from": "research",
            "live": live,
        }
        from core.research.model_versions import archive_persist_safe

        archive_persist_safe("tc_rank", MODEL_ROLE_LIVE, out_live)
        return out_live

    if not isinstance(report, dict) or not report.get("success"):
        return {
            "success": False,
            "error": report.get("error") if isinstance(report, dict) else "no report",
        }
    stamp_oo_rank_fitted_at(report)
    backend = select_oo_rank_backend()
    pack = _backend_pack_from_report(report, backend)
    if not isinstance(pack, dict) or not isinstance(pack.get("return_model"), dict):
        return {
            "success": False,
            "error": (
                "lambdarank pack missing（请先拟合再启用；"
                "旧 Ridge 草稿不可晋级为 LambdaRank）"
            ),
        }
    view = dict(report)
    view["return_model"] = pack.get("return_model")
    if isinstance(pack.get("return_model_research"), dict):
        view["return_model_research"] = pack.get("return_model_research")
    model = select_fit_return_model(view)
    if not isinstance(model, dict):
        return {"success": False, "error": "return_model missing"}
    if not _oo_rank_use_booster(model):
        return {
            "success": False,
            "error": (
                "lambdarank pack missing（请先拟合再启用；"
                "旧 Ridge 草稿不可晋级为 LambdaRank）"
            ),
        }
    if not _oo_rank_model_valid(model):
        return {"success": False, "error": "return_model missing usable tc_rank pack"}
    model = _scrub_rank_label_meta(dict(model))
    model["model_role"] = MODEL_ROLE_RESEARCH
    model["backend"] = backend
    model.pop("shadow_only", None)
    fitted_at = report.get("fitted_at") or model.get("fitted_at")
    promoted_at = now_iso_utc()
    y_spec = report.get("y_spec") or model.get("y_spec")
    if isinstance(y_spec, dict):
        y_spec = dict(y_spec)
        y_spec["backend"] = backend
    doc = {
        "success": True,
        "task": TASK,
        "schema": SCHEMA,
        "backend": backend,
        "recommended_backend": backend,
        "return_model": model,
        "return_model_research": dict(model),
        "oos": _scrub_rank_oos(
            report.get("oos"), drop_keys=("ridge_tc_baseline", "ridge_oo_baseline")
        ),
        "y_spec": y_spec,
        "note": note or report.get("note") or f"tc_rank promote research backend={backend}",
        "n_days": report.get("n_days"),
        "n_train_days": report.get("n_train_days"),
        "n_test_days": report.get("n_test_days"),
        "holdout_trading_days": report.get("holdout_trading_days"),
        "sample_count": report.get("sample_count") or model.get("sample_count"),
        "fitted_at": fitted_at,
        "promoted_at": promoted_at,
        "model_role": MODEL_ROLE_RESEARCH,
    }
    for k in (
        "zscore_universe",
        "zscore_universe_tiers",
        "tier_codes",
        "n_tier_codes",
        "tier_fingerprint",
        "tier_as_of_last",
        "holdout_n",
        "tier_n",
        "cross_section_zscore",
        "train_universe",
        "train_universe_tiers",
        "tau_end_hm",
        "tau_hm",
        "tau_grid",
        "zero_prefix",
    ):
        if k in report and report.get(k) is not None:
            doc[k] = report.get(k)
            model[k] = report.get(k)
            if isinstance(doc.get("return_model_research"), dict):
                doc["return_model_research"][k] = report.get(k)
    backends = report.get("backends")
    if isinstance(backends, dict) and backends:
        lr_only = {
            k: _scrub_rank_label_meta(v)
            for k, v in backends.items()
            if str(k).strip().lower() == "lambdarank" and isinstance(v, dict)
        }
        if lr_only:
            doc["backends"] = lr_only
    doc.pop("ridge_return_model_research", None)
    doc.pop("shadow_track", None)
    doc = _scrub_rank_label_meta(doc)
    from core.research.model_versions import refuse_incomplete_suite_doc

    blocked_inc = refuse_incomplete_suite_doc(doc, sink="研究套")
    if blocked_inc:
        return blocked_inc
    path = research_model_path(live)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    save_tc_rank_last_report(report)
    out_res = {
        "success": True,
        "path": path,
        "fitted_at": fitted_at or doc.get("fitted_at"),
        "promoted_at": promoted_at,
        "model_role": MODEL_ROLE_RESEARCH,
        "backend": backend,
        "recommended_backend": backend,
        "research": path,
    }
    from core.research.model_versions import archive_persist_safe

    archive_persist_safe("tc_rank", MODEL_ROLE_RESEARCH, out_res)
    return out_res


def _resolve_tc_rank_fit(model_doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """显式文档无效时不回落磁盘，避免误用别的头。"""
    from core.research.oo_rank_lambdarank import _oo_rank_model_valid

    if not isinstance(model_doc, dict):
        return None
    model = model_doc.get("return_model")
    if _oo_rank_model_valid(model):
        return model
    if _oo_rank_model_valid(model_doc):
        return model_doc
    return None


_TC_RANK_DROP = (
    "y_tc_rank",
    "y_tc_rank_hat",
    "y_tc_rank_score",
    "y_tc_rank_n",
    "predicted_score_tc_rank",
    "formula_terms_tc_rank",
    "score_formula_terms_tc_rank",
)


def apply_tc_rank_scores(
    items: Sequence[dict],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
    score_codes: Optional[Collection[str]] = None,
    zscore_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
    clock: str = "open",
) -> List[dict]:
    """就地写入 y_tc_rank 当日截面名次。不改 y_oo_fuse / y_oo_rank / 买序。

    特征取 ``features_tau``（z_τc_* ∪ raw_atc_*）。截面 z **只用**全池 L2
    （``day_cube`` / 读盘 Cube μ/σ），不进批现算。``zscore_codes`` 保留兼容
    （无 Cube 时忽略）。``score_codes`` 只限制写分。
    """
    from core.research.feature_standardize import (
        is_cross_section_zscore,
        normalize_score_codes,
    )
    from core.research.oo_rank_lambdarank import (
        _explain_oo_rank_lambdarank_rows,
        _explain_oo_rank_ridge,
        _item_cs_group,
        _oo_rank_feature_names,
        _oo_rank_use_booster,
        _oo_rank_use_ridge,
        _predict_oo_rank_rows,
        rank_cross_section_z_dicts,
    )

    allow = normalize_score_codes(score_codes)
    _ = zscore_codes  # 统一池 L2 后不再缩进批 μ/σ
    model = _resolve_tc_rank_fit(model_doc)
    out = list(items or [])
    if model is None:
        return out
    names = _oo_rank_feature_names(model)
    raw_rows: List[dict] = []
    slots: List[int] = []
    for i, it in enumerate(out):
        if not isinstance(it, dict):
            continue
        feats = it.get("features_tau")
        if not isinstance(feats, dict) or not feats:
            continue
        raw_rows.append(dict(feats))
        slots.append(i)
    if not raw_rows:
        return out
    keep: List[int] = []
    for j, slot in enumerate(slots):
        code = str(out[slot].get("stock_code") or "").strip()
        if allow is not None and code not in allow:
            for key in _TC_RANK_DROP:
                out[slot].pop(key, None)
            continue
        keep.append(j)
    feat_rows = raw_rows
    if is_cross_section_zscore(model):
        try:
            from core.signal.factors.z_tc import stamp_z_tc_aliases

            for r in raw_rows:
                stamp_z_tc_aliases(r)
        except Exception:  # noqa: BLE001
            pass
        z_names = names or list(raw_rows[0].keys())
        codes = [str(out[s].get("stock_code") or "").strip() for s in slots]
        groups = [_item_cs_group(out[s]) for s in slots]
        feat_rows = rank_cross_section_z_dicts(
            raw_rows,
            [str(n) for n in z_names],
            groups,
            day_cube=day_cube,
            codes=codes,
            clock=str(clock or "open"),
        )
        if feat_rows is None:
            return out
    pred_rows = [feat_rows[j] for j in keep]
    hats_keep = (
        _predict_oo_rank_rows(model, pred_rows, impute_missing=True) if pred_rows else []
    )
    hats: List[Optional[float]] = [None] * len(raw_rows)
    for j, hat in zip(keep, hats_keep):
        hats[j] = hat
        if hat is None:
            continue
        try:
            v = round(float(hat), 6)
        except (TypeError, ValueError):
            continue
        out[slots[j]]["y_tc_rank_score"] = v
    if keep:
        try:
            expl_rows = [feat_rows[j] for j in keep]
            expl_hats = [hats[j] for j in keep]
            expl_slots = [slots[j] for j in keep]
            if _oo_rank_use_booster(model):
                expls = _explain_oo_rank_lambdarank_rows(expl_rows, model, hats=expl_hats)
                for slot, expl in zip(expl_slots, expls):
                    _stamp_formula_terms_tc_rank(out[slot], expl)
            elif _oo_rank_use_ridge(model):
                for slot, feats, hat in zip(expl_slots, expl_rows, expl_hats):
                    expl = _explain_oo_rank_ridge(feats, model, y_hat=hat)
                    _stamp_formula_terms_tc_rank(out[slot], expl)
        except Exception:  # noqa: BLE001
            logger.debug("stamp formula_terms_tc_rank failed", exc_info=True)
    _assign_tc_rank_day_ranks(out)
    return out


def _stamp_formula_terms_tc_rank(item: dict, expl: Optional[Dict[str, Any]]) -> None:
    if not isinstance(item, dict) or not isinstance(expl, dict):
        return
    stamped = dict(expl)
    stamped["head"] = "tc_rank"
    item["formula_terms_tc_rank"] = stamped
    item["score_formula_terms_tc_rank"] = stamped


def _assign_tc_rank_day_ranks(items: Sequence[dict]) -> None:
    """相对分 → 1..n（1=最高）。只写 y_tc_rank*，不碰 y_oo_rank。"""
    rows: List[dict] = []
    for it in items or []:
        if isinstance(it, dict) and it.get("y_tc_rank_score") is not None:
            rows.append(it)

    def _key(it: dict) -> tuple:
        return (-float(it["y_tc_rank_score"]), str(it.get("stock_code") or ""))

    rows.sort(key=_key)
    n = len(rows)
    for i, it in enumerate(rows):
        rank = i + 1
        it["y_tc_rank"] = rank
        it["y_tc_rank_hat"] = rank
        it["predicted_score_tc_rank"] = rank
        it["y_tc_rank_n"] = n
