"""因子系数（收益分）模型产物：研究草稿 / live 生效（不写 signal_config.weights）。"""


import logging

logger = logging.getLogger(__name__)
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from core.io_atomic import atomic_write_json
from core.paths import (
    LIVE_DIR,
    QUANT_REPORTS_DIR,
    RETURN_SCORE_MODEL_ACTIVE_PATH,
    RETURN_SCORE_MODEL_DRAFT_PATH,
)
from core.signal.return_score import ReturnScoreModel, clamp_rank_mode


def _ensure_dirs() -> None:
    os.makedirs(QUANT_REPORTS_DIR, exist_ok=True)
    os.makedirs(LIVE_DIR, exist_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_return_model_draft(
    model: ReturnScoreModel,
    *,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """写入研究草稿 ``data/reports/last_return_score_model.json``。"""
    _ensure_dirs()
    payload = {
        "version": 1,
        "kind": "return_score_model",
        "role": "draft",
        "saved_at": _utc_now(),
        "model": model.to_dict(),
        "meta": dict(meta or {}),
        "note": "研究草稿（因子系数产物）；promote 后才影响 scoring.rank_mode=predicted_score 的横截面。",
    }
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
) -> Tuple[Optional[ReturnScoreModel], Dict[str, Any]]:
    """优先读 live 生效产物，否则研究草稿。"""
    paths = []
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
) -> Dict[str, Any]:
    """草稿 → ``data/live/return_score_model_active.json``（人审；不改 weights）。"""
    raw = load_return_model_payload(RETURN_SCORE_MODEL_DRAFT_PATH)
    if not raw or not (raw.get("model") or {}).get("coefficients"):
        return {
            "success": False,
            "error": "无可用草稿模型，请先拟合并保存草稿",
            "draft_path": RETURN_SCORE_MODEL_DRAFT_PATH,
        }
    # FM0 · 系数里若含 proxy 因子非零则拦截（force 经 note 含 force= 时放行）
    force = "force=1" in str(note or "") or "force:true" in str(note or "").lower()
    try:
        from core.signal.factor_health import guard_weights_for_promote

        coefs = (raw.get("model") or {}).get("coefficients") or {}
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
        pass
    _ensure_dirs()
    promoted_at = _utc_now()
    payload = {
        "version": int(raw.get("version") or 1),
        "kind": "return_score_model",
        "role": "active",
        "promoted_at": promoted_at,
        "source_saved_at": raw.get("saved_at"),
        "model": raw.get("model"),
        "meta": {
            **(raw.get("meta") or {}),
            "promote_note": str(note or ""),
        },
        "note": "live 收益排序模型；仅当 signal_config.scoring.rank_mode=predicted_score 时启用。",
    }
    atomic_write_json(RETURN_SCORE_MODEL_ACTIVE_PATH, payload)
    return {
        "success": True,
        "path": RETURN_SCORE_MODEL_ACTIVE_PATH,
        "promoted_at": promoted_at,
        "sample_count": (payload.get("model") or {}).get("sample_count"),
        "note": "已晋升；请将 scoring.rank_mode 设为 predicted_score 后横截面才按 ŷ 排序。",
    }


def return_model_status() -> Dict[str, Any]:
    draft = load_return_model_payload(RETURN_SCORE_MODEL_DRAFT_PATH)
    active = load_return_model_payload(RETURN_SCORE_MODEL_ACTIVE_PATH)
    from core.signal.config import load_signal_config

    cfg = load_signal_config()
    scoring = cfg.get("scoring") or {}
    return {
        "success": True,
        "rank_mode": clamp_rank_mode(scoring.get("rank_mode")),
        "draft": {
            "exists": bool(draft),
            "path": RETURN_SCORE_MODEL_DRAFT_PATH,
            "saved_at": (draft or {}).get("saved_at"),
            "sample_count": ((draft or {}).get("model") or {}).get("sample_count"),
        },
        "active": {
            "exists": bool(active),
            "path": RETURN_SCORE_MODEL_ACTIVE_PATH,
            "promoted_at": (active or {}).get("promoted_at"),
            "sample_count": ((active or {}).get("model") or {}).get("sample_count"),
        },
        "note": "产物与 weights 分离；rank_mode 在 signal_config.scoring。",
    }


def fit_watching_return_model(
    *,
    codes: Optional[list] = None,
    lookback: int = 120,
    horizon_days: int = 3,
    ridge_lambda: float = 0.0,
    watching_limit: int = 12,
    min_samples: int = 24,
    save_draft: bool = True,
) -> Dict[str, Any]:
    """研究池堆叠面板拟合收益模型，可选落草稿。"""
    from core.research.panel import collect_subscore_forward_panel
    from core.research.portfolio_bars import load_portfolio_stock_bars
    from core.signal.return_score import fit_return_model_from_panel
    from core.watching.store import read_watching

    if codes:
        use_codes = [str(c).strip() for c in codes if str(c).strip()]
    else:
        uni = read_watching()
        use_codes = list(uni.get("watchlist") or [])
    limit = max(3, min(int(watching_limit or 12), 40))
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

    all_xs = []
    all_ys = []
    for code, bars in stock_bars.items():
        xs, ys, _dates = collect_subscore_forward_panel(
            bars,
            horizon_days=horizon_days,
            stock_code=code,
            pit_fundamentals=False,
        )
        all_xs.extend(xs)
        all_ys.extend(ys)

    as_of = None
    for bars in stock_bars.values():
        if bars:
            as_of = str(bars[-1].get("date") or "") or None
            break

    model, report = fit_return_model_from_panel(
        all_xs,
        all_ys,
        horizon_days=horizon_days,
        ridge_lambda=ridge_lambda,
        fitted_as_of=as_of,
        min_samples=min_samples,
    )
    if model is None:
        return {
            "success": False,
            "error": (report or {}).get("error") or "拟合失败",
            "report": report,
            "stock_count": len(stock_bars),
        }

    out: Dict[str, Any] = {
        "success": True,
        "task": "fit_return_score_model",
        "promote_ready": False,
        "model": model.to_dict(),
        "sample_count": model.sample_count,
        "stock_count": len(stock_bars),
        "codes": list(stock_bars.keys()),
        "failures": failures[:8],
        "ols": {
            "r_squared": report.get("r_squared"),
            "solver": report.get("solver"),
            "ridge_lambda": report.get("ridge_lambda"),
            "excluded_features": report.get("excluded_features"),
        },
        "note": "已拟合因子系数模型；save_draft 后可人审 promote 到 live。",
    }
    if save_draft:
        saved = save_return_model_draft(
            model,
            meta={
                "lookback": lookback,
                "horizon_days": horizon_days,
                "codes": list(stock_bars.keys()),
                "r_squared": report.get("r_squared"),
            },
        )
        out["draft"] = saved
    return out
