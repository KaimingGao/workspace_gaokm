"""因子系数（收益分）模型产物：研究草稿 / live 生效（不写 signal_config.weights）。"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.io_atomic import atomic_write_json
from core.paths import (
    LIVE_DIR,
    QUANT_REPORTS_DIR,
    RETURN_SCORE_MODEL_ACTIVE_PATH,
    RETURN_SCORE_MODEL_DRAFT_PATH,
    RETURN_SCORE_MODEL_RESEARCH_PATH,
)
from core.signal.return_score import ReturnScoreModel, clamp_rank_mode
from core.watching.store import WATCHING_MAX_SIZE

logger = logging.getLogger(__name__)


def _ensure_dirs() -> None:
    os.makedirs(QUANT_REPORTS_DIR, exist_ok=True)
    os.makedirs(LIVE_DIR, exist_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_persist_role(role: Any) -> str:
    r = str(role or "live").strip().lower()
    if r in ("research", "research_suite", "backtest"):
        return "research"
    return "live"


def save_return_model_draft(
    model: ReturnScoreModel,
    *,
    meta: Optional[Dict[str, Any]] = None,
    oos: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """写入研究草稿 ``data/reports/last_return_score_model.json``。"""
    _ensure_dirs()
    payload = {
        "version": 1,
        "kind": "return_score_model",
        "role": "draft",
        "fitted_at": _utc_now(),
        "saved_at": None,
        "model": model.to_dict(),
        "meta": dict(meta or {}),
        "oos": dict(oos or {}) if isinstance(oos, dict) else {},
        "note": "研究草稿（因子系数产物）；启用研究/执行后才进回测或 live 横截面。",
    }
    payload["saved_at"] = payload["fitted_at"]
    atomic_write_json(RETURN_SCORE_MODEL_DRAFT_PATH, payload)
    return {
        "success": True,
        "path": RETURN_SCORE_MODEL_DRAFT_PATH,
        "role": "draft",
        "saved_at": payload["saved_at"],
        "sample_count": model.sample_count,
        "n_coefs": len(model.coefficients),
    }


def load_return_model_payload(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_return_model(
    *,
    prefer_active: bool = True,
    prefer_research: bool = False,
) -> Tuple[Optional[ReturnScoreModel], Dict[str, Any]]:
    """读全局 ŷ_oo 模型。

    - 默认：active → draft
    - ``prefer_research=True``：research → active → draft（历史回测）
    """
    paths = []
    if prefer_research:
        paths.append(("research", RETURN_SCORE_MODEL_RESEARCH_PATH))
    if prefer_active:
        paths.append(("active", RETURN_SCORE_MODEL_ACTIVE_PATH))
    paths.append(("draft", RETURN_SCORE_MODEL_DRAFT_PATH))
    if not prefer_active:
        paths.append(("active", RETURN_SCORE_MODEL_ACTIVE_PATH))

    seen = set()
    for role, path in paths:
        if path in seen:
            continue
        seen.add(path)
        raw = load_return_model_payload(path)
        if not raw:
            continue
        model = ReturnScoreModel.from_dict(raw.get("model") or raw)
        if model is None:
            continue
        return model, {
            "ok": True,
            "role": raw.get("role") or role,
            "path": path,
            "saved_at": raw.get("saved_at") or raw.get("promoted_at"),
            "meta": raw.get("meta") or {},
        }
    return None, {"ok": False, "reason": "no_model_file"}


def promote_return_model_draft(
    *,
    note: str = "",
    role: str = "live",
) -> Dict[str, Any]:
    """草稿 → 研究套或执行套（人审；不改 weights）。

    ``role=research`` → ``return_score_model_research.json``（历史回测）
    ``role=live`` → ``return_score_model_active.json``（交易执行）
    """
    role_n = _normalize_persist_role(role)
    raw = load_return_model_payload(RETURN_SCORE_MODEL_DRAFT_PATH)
    if not raw or not (raw.get("model") or {}).get("coefficients"):
        return {
            "success": False,
            "error": "无可用草稿模型，请先拟合并保存草稿",
            "draft_path": RETURN_SCORE_MODEL_DRAFT_PATH,
        }
    force = "force=1" in str(note or "") or "force:true" in str(note or "").lower()
    model_blob = dict(raw.get("model") or {})
    coefs_in = dict(model_blob.get("coefficients") or {})
    stripped_proxy: list = []
    try:
        from core.signal.factors.meta.health import (
            guard_weights_for_promote,
            strip_unsourced_coefficients,
        )

        # 无真源 proxy（money_flow 等）自动归零剔除，再过门；不依赖人手 force
        coefs, stripped_proxy = strip_unsourced_coefficients(coefs_in)
        model_blob["coefficients"] = coefs
        for zkey in ("z_means", "z_stds", "zscore_means", "zscore_stds"):
            zm = model_blob.get(zkey)
            if isinstance(zm, dict) and stripped_proxy:
                for name in stripped_proxy:
                    zm.pop(name, None)
        guard = guard_weights_for_promote(coefs, force=force)
        if guard.get("blocked"):
            return {
                "success": False,
                "error": guard.get("error") or "factor_health_blocked",
                "factor_health": guard.get("factor_health"),
                "hint": "proxy 因子系数须为 0；确需放行在 note 写 force=1",
            }
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in return_score_store.py", exc_info=True)
        model_blob = dict(raw.get("model") or {})
        pass
    _ensure_dirs()
    promoted_at = _utc_now()
    if role_n == "research":
        target = RETURN_SCORE_MODEL_RESEARCH_PATH
        payload_role = "research"
        note_out = "研究套 ŷ_oo；供历史回测加载（prefer_research）。"
    else:
        target = RETURN_SCORE_MODEL_ACTIVE_PATH
        payload_role = "active"
        note_out = "执行套 ŷ_oo；仅当 scoring.rank_mode=predicted_score 时横截面按 ŷ 排序。"
    meta_out = {
        **(raw.get("meta") or {}),
        "promote_note": str(note or ""),
        "persist_role": role_n,
    }
    if stripped_proxy:
        meta_out["stripped_unsourced"] = list(stripped_proxy)
    fitted_at = raw.get("fitted_at") or raw.get("saved_at")
    payload = {
        "version": int(raw.get("version") or 1),
        "kind": "return_score_model",
        "role": payload_role,
        "model_role": role_n,
        "fitted_at": fitted_at,
        "promoted_at": promoted_at,
        "source_saved_at": raw.get("saved_at") or fitted_at,
        "model": model_blob,
        "meta": meta_out,
        "oos": dict(raw.get("oos") or {}) if isinstance(raw.get("oos"), dict) else {},
        "note": note_out,
    }
    atomic_write_json(target, payload)
    return {
        "success": True,
        "path": target,
        "role": role_n,
        "persist_role": role_n,
        "promoted_at": promoted_at,
        "sample_count": (payload.get("model") or {}).get("sample_count"),
        "note": (
            "已写入研究套；历史回测优先加载。"
            if role_n == "research"
            else "已写入执行套；请确认 scoring.rank_mode=predicted_score。"
        ),
    }


def _payload_oos(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """取有评测意义的 oos（至少含 ic / sign_hit / n_test 之一）。"""
    if not isinstance(payload, dict):
        return None
    candidates = []
    if isinstance(payload.get("oos"), dict):
        candidates.append(payload["oos"])
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if isinstance(meta.get("oos"), dict):
        candidates.append(meta["oos"])
    for oos in candidates:
        if (
            oos.get("ic") is not None
            or oos.get("sign_hit") is not None
            or oos.get("sign_hit_rate") is not None
            or oos.get("n_test") is not None
        ):
            return dict(oos)
    return None


def return_model_status() -> Dict[str, Any]:
    draft = load_return_model_payload(RETURN_SCORE_MODEL_DRAFT_PATH)
    active = load_return_model_payload(RETURN_SCORE_MODEL_ACTIVE_PATH)
    research = load_return_model_payload(RETURN_SCORE_MODEL_RESEARCH_PATH)
    from core.signal.config import load_signal_config

    cfg = load_signal_config()
    scoring = cfg.get("scoring") or {}

    # 研究枢纽展示优先：执行 → 研究 → 草稿（刷新页恢复系数表）
    display_role = None
    display_raw = None
    for role_name, payload in (
        ("active", active),
        ("research", research),
        ("draft", draft),
    ):
        model_blob = (payload or {}).get("model") if isinstance(payload, dict) else None
        if isinstance(model_blob, dict) and model_blob.get("coefficients"):
            display_role = role_name
            display_raw = payload
            break

    return_model = None
    if display_raw and isinstance(display_raw.get("model"), dict):
        return_model = dict(display_raw["model"])
        meta = display_raw.get("meta") if isinstance(display_raw.get("meta"), dict) else {}
        if return_model.get("r_squared") is None and meta.get("r_squared") is not None:
            return_model["r_squared"] = meta.get("r_squared")
        if return_model.get("ridge_lambda") is None and meta.get("ridge_lambda") is not None:
            return_model["ridge_lambda"] = meta.get("ridge_lambda")

    # OOS：草稿若更新于展示套之后，优先用草稿评测（含 cs_ic）；否则展示套 → 草稿回退
    def _ts(payload: Optional[Dict[str, Any]]) -> str:
        if not isinstance(payload, dict):
            return ""
        return str(
            payload.get("saved_at")
            or payload.get("promoted_at")
            or payload.get("source_saved_at")
            or ""
        )

    draft_oos = _payload_oos(draft)
    display_oos = _payload_oos(display_raw)
    oos_out = display_oos
    oos_source = display_role
    if draft_oos is not None:
        draft_ts = _ts(draft)
        display_ts = _ts(display_raw)
        if oos_out is None or (draft_ts and draft_ts >= display_ts):
            oos_out = draft_oos
            oos_source = "draft"
    if oos_out is None:
        for role_name, payload in (("research", research), ("active", active)):
            oos_out = _payload_oos(payload)
            if oos_out is not None:
                oos_source = role_name
                break

    return {
        "success": True,
        "rank_mode": clamp_rank_mode(scoring.get("rank_mode")),
        "draft": {
            "exists": bool(draft),
            "path": RETURN_SCORE_MODEL_DRAFT_PATH,
            "fitted_at": (draft or {}).get("fitted_at") or (draft or {}).get("saved_at"),
            "saved_at": (draft or {}).get("saved_at"),
            "sample_count": ((draft or {}).get("model") or {}).get("sample_count"),
        },
        "active": {
            "exists": bool(active),
            "path": RETURN_SCORE_MODEL_ACTIVE_PATH,
            "fitted_at": (active or {}).get("fitted_at")
            or (active or {}).get("source_saved_at"),
            "promoted_at": (active or {}).get("promoted_at"),
            "source_saved_at": (active or {}).get("source_saved_at"),
            "sample_count": ((active or {}).get("model") or {}).get("sample_count"),
        },
        "research": {
            "exists": bool(research),
            "path": RETURN_SCORE_MODEL_RESEARCH_PATH,
            "fitted_at": (research or {}).get("fitted_at")
            or (research or {}).get("source_saved_at"),
            "promoted_at": (research or {}).get("promoted_at"),
            "source_saved_at": (research or {}).get("source_saved_at"),
            "sample_count": ((research or {}).get("model") or {}).get("sample_count"),
        },
        "display_role": display_role,
        "oos_source": oos_source,
        "return_model": return_model,
        "oos": oos_out,
        "note": "草稿 / 研究套 / 执行套分离；回测 prefer research，交易 prefer active。",
    }


def _oo_ic(preds: List[Optional[float]], ys: List[float]) -> Optional[float]:
    pairs = [(float(p), float(y)) for p, y in zip(preds, ys) if p is not None]
    if len(pairs) < 5:
        return None
    import math

    n = len(pairs)
    mx = sum(p for p, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    num = sum((p - mx) * (y - my) for p, y in pairs)
    dx = math.sqrt(sum((p - mx) ** 2 for p, _ in pairs))
    dy = math.sqrt(sum((y - my) ** 2 for _, y in pairs))
    if dx < 1e-12 or dy < 1e-12:
        return None
    return round(num / (dx * dy), 4)


def _oo_sign_hit(preds: List[Optional[float]], ys: List[float]) -> Optional[float]:
    hits = 0
    n = 0
    for p, y in zip(preds, ys):
        if p is None:
            continue
        if abs(float(p)) < 0.05:
            continue
        n += 1
        if (float(p) > 0 and float(y) > 0) or (float(p) < 0 and float(y) < 0):
            hits += 1
    if n < 5:
        return None
    return round(hits / n, 4)


def fit_watching_return_model(
    *,
    codes: Optional[list] = None,
    lookback: int = 600,
    horizon_days: int = 3,
    ridge_lambda: float = 0.0,
    watching_limit: int = 12,
    min_samples: int = 24,
    save_draft: bool = True,
    holdout_trading_days: int = 20,
    label_demean: bool = False,
) -> Dict[str, Any]:
    """研究池堆叠面板拟合收益模型 + Holdout OOS，可选落草稿。

    近 ``holdout_trading_days`` 个交易日只测；全样本重估 β 写入草稿（执行口径）。
    ``label_demean``：训练标签减全局均值，截距加回（落盘 ``y_demeaned``）。
    """
    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        attach_holdout_meta,
        resolve_ridge_split,
    )
    from core.research.oo_ridge_compact import (
        assemble_oo_panels,
        fit_oo_ridge_matrix,
        predict_oo_matrix,
    )
    from core.research.portfolio_bars import load_portfolio_stock_bars
    from core.watching.store import read_watching

    if codes:
        use_codes = [str(c).strip() for c in codes if str(c).strip()]
    else:
        uni = read_watching()
        use_codes = list(uni.get("watchlist") or [])
    limit = max(3, min(int(watching_limit or 12), int(WATCHING_MAX_SIZE)))
    use_codes = use_codes[:limit]
    if len(use_codes) < 2:
        return {"success": False, "error": "标的不足 2 只"}

    stock_bars, failures, _fund = load_portfolio_stock_bars(
        use_codes,
        lookback=lookback,
        fetch_fundamentals=False,
    )
    if len(stock_bars) < 2:
        return {
            "success": False,
            "error": f"有效日线不足（失败 {len(failures)}）",
            "failures": failures[:8],
        }

    codes_used = list(stock_bars.keys())
    as_of = None
    cal_days = set()
    items: List[Tuple[str, list]] = []
    for code in codes_used:
        bars = stock_bars.pop(code, None) or []
        if bars and as_of is None:
            as_of = str(bars[-1].get("date") or "") or None
        for bar in bars:
            if not isinstance(bar, dict):
                continue
            day = str(bar.get("date") or bar.get("trade_date") or "")[:10]
            if len(day) >= 10:
                cal_days.add(day)
        items.append((code, bars))
    x_mat, y_mat, feat_names, all_dates = assemble_oo_panels(
        items,
        horizon_days=horizon_days,
    )
    del items

    hold_n = int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS)
    train_idx, test_idx, split_meta = resolve_ridge_split(
        all_dates,
        holdout_trading_days=hold_n,
        label_horizon_days=int(horizon_days or 1),
        calendar_dates=sorted(cal_days),
    )
    ys_te = [float(y_mat[i]) for i in test_idx]

    research_model, research_report = fit_oo_ridge_matrix(
        x_mat,
        y_mat,
        feat_names,
        row_idx=train_idx,
        horizon_days=horizon_days,
        ridge_lambda=ridge_lambda,
        min_samples=min_samples,
        fitted_as_of=as_of,
        label_demean=bool(label_demean),
    )
    oos: Dict[str, Any] = {
        "n_train": len(train_idx),
        "n_test": len(test_idx),
        "holdout_trading_days": split_meta.get("holdout_trading_days") or hold_n,
        "fit_end": split_meta.get("fit_end"),
        "eval_start": split_meta.get("eval_start"),
        "label_horizon_days": int(horizon_days or 1),
    }
    if research_model is not None and ys_te:
        preds_te = predict_oo_matrix(research_model, x_mat, feat_names, test_idx)
        oos["ic"] = _oo_ic(preds_te, ys_te)
        oos["sign_hit"] = _oo_sign_hit(preds_te, ys_te)
        oos["n_valid"] = sum(1 for p in preds_te if p is not None)
        try:
            from core.research.daily_cs_ic import attach_daily_cs_ic

            metas_te = [{"date": str(all_dates[i])[:10]} for i in test_idx]
            attach_daily_cs_ic(oos, preds_te, ys_te, metas_te)
        except Exception:  # noqa: BLE001
            logger.debug("oo attach_daily_cs_ic failed", exc_info=True)

    # 全样本 → 草稿 / 执行口径
    model, report = fit_oo_ridge_matrix(
        x_mat,
        y_mat,
        feat_names,
        horizon_days=horizon_days,
        ridge_lambda=ridge_lambda,
        min_samples=min_samples,
        fitted_as_of=as_of,
        label_demean=bool(label_demean),
    )
    if model is None:
        return {
            "success": False,
            "error": (report or {}).get("error") or "拟合失败",
            "report": report,
            "oos": oos,
            "stock_count": len(codes_used),
        }

    out: Dict[str, Any] = {
        "success": True,
        "task": "fit_return_score_model",
        "promote_ready": False,
        "model": model.to_dict(),
        "sample_count": model.sample_count,
        "stock_count": len(codes_used),
        "codes": codes_used,
        "failures": failures[:8],
        "oos": oos,
        "ols": {
            "r_squared": report.get("r_squared"),
            "solver": report.get("solver"),
            "ridge_lambda": report.get("ridge_lambda"),
            "excluded_features": report.get("excluded_features"),
        },
        "note": "已拟合因子系数模型 + Holdout OOS；save_draft 后可人审启用研究/执行。",
        "label_demean": bool(label_demean),
        "y_demeaned": bool(label_demean),
    }
    if research_model is not None:
        out["research_model"] = research_model.to_dict()
        if isinstance(research_report, dict):
            out["research_ols"] = {
                "r_squared": research_report.get("r_squared"),
                "ridge_lambda": research_report.get("ridge_lambda"),
                "y_demeaned": research_report.get("y_demeaned"),
                "y_label_mean": research_report.get("y_label_mean"),
            }
    attach_holdout_meta(out, split_meta)
    if save_draft:
        saved = save_return_model_draft(
            model,
            meta={
                "lookback": lookback,
                "horizon_days": horizon_days,
                "codes": codes_used,
                "r_squared": report.get("r_squared"),
                "ridge_lambda": ridge_lambda,
                "holdout_trading_days": oos.get("holdout_trading_days"),
                "label_demean": bool(label_demean),
                "y_demeaned": bool((report or {}).get("y_demeaned")),
                "y_label_mean": (report or {}).get("y_label_mean"),
            },
            oos=oos,
        )
        out["draft"] = saved
    return out
