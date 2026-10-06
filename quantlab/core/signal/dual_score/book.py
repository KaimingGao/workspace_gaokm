"""双层 ŷ：持仓/簿行字段打包（SignalService 出口）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from core.signal.dual_score.resolve import (
    align_trade_score_fields,
    get_dual_score_cfg,
)
from core.signal.dual_score.tau import (
    ensure_formula_terms_tau,
    features_tau_fill_diag,
    format_tau_formula_string,
    rem_factor_coefficients_public,
)
from core.signal.dual_score.co import ensure_formula_terms_co


def dual_score_book_fields(
    item: Optional[dict],
    *,
    rank_cfg: Optional[dict] = None,
    paper: Optional[dict] = None,
) -> Dict[str, Any]:
    """簿/观察行透传字段。

    ``dual_score_fusion`` / ``dual_score_weights`` 一律用当前配置，
    避免簿内旧戳（如 f1 / 旧 w_*）误导 tip。
    旧簿无 ``formula_terms_tau`` 时现场补全组成表。
    返回前对齐 ŷ_trade（修 eod_next 塌成 EOD 的旧 blend）。
    ŷ_τc 仍透传给 tip（含旧簿 y_r 反几何）；数据中心 / 持仓主表不再列。
    """
    if not isinstance(item, dict):
        return {}
    work = dict(item)
    try:
        # 调用方多为刚算完的 signal_item；保留 PIT window，勿时钟误刷
        align_trade_score_fields(work, write_score=False, refresh_window=False)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        pass
    try:
        from core.signal.y_state import stamp_y_state

        stamp_y_state(work)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        pass
    try:
        cfg = get_dual_score_cfg()
        live_fusion = cfg.get("fusion_mode")
        book_w = work.get("dual_score_weights")
        win = work.get("dual_score_window")
        if isinstance(book_w, dict) and (
            book_w.get("w_oo") is not None or book_w.get("w_eod") is not None
        ):
            w_oo = book_w.get("w_oo")
            if w_oo is None:
                w_oo = book_w.get("w_eod")
            live_w = {
                "w_oo": w_oo,
                "w_eod": w_oo,
                "w_tau": book_w.get("w_tau"),
                "w_mode": book_w.get("w_mode") or cfg.get("w_mode") or "fixed",
                "w_note": book_w.get("w_note"),
                "mode": "blend",
                "tau_available": book_w.get("tau_available"),
                "window": book_w.get("window") or win,
            }
        else:
            w_oo = cfg.get("w_oo")
            if w_oo is None:
                w_oo = cfg.get("w_eod")
            live_w = {
                "w_oo": w_oo,
                "w_eod": w_oo,
                "w_tau": cfg.get("w_tau"),
                "w_mode": cfg.get("w_mode") or "fixed",
                "mode": "blend",
                "window": win,
            }
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        live_fusion = "blend"
        live_w = {
            "w_oo": 0.5,
            "w_eod": 0.5,
            "w_tau": 0.5,
            "w_mode": "fixed",
            "mode": "blend",
        }
    formula_terms_tau = ensure_formula_terms_tau(work)
    formula_terms_co = ensure_formula_terms_co(work)
    formula_terms_r = work.get("formula_terms_r") or work.get("score_formula_terms_r")
    if not (isinstance(formula_terms_r, dict) and formula_terms_r.get("terms")):
        try:
            from core.research.tc_ridge import explain_tau_prediction, load_tau_model

            feats_r = work.get("features_tau") if isinstance(work.get("features_tau"), dict) else {}
            if not feats_r:
                feats_r = (
                    work.get("features_path")
                    if isinstance(work.get("features_path"), dict)
                    else {}
                )
            formula_terms_r = explain_tau_prediction(feats_r, model_doc=load_tau_model())
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            formula_terms_r = None
    score_formula_tau = work.get("score_formula_tau") or format_tau_formula_string(
        formula_terms_tau
    )
    coefs_tau = work.get("factor_coefficients_tau")
    if not isinstance(coefs_tau, dict) or not coefs_tau:
        coefs_tau = rem_factor_coefficients_public()
    fill = work.get("features_tau_fill")
    if not isinstance(fill, dict):
        fill = features_tau_fill_diag(work.get("features_tau"))
    try:
        from core.signal.yhat_windows import pick_y_τc, write_y_τc

        ytc = pick_y_τc(work)
        if ytc is not None:
            write_y_τc(work, ytc)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
    try:
        from core.signal.yhat_windows import stamp_window_scores
        from core.paper.rebalance.path_matrix import get_path_matrix_cfg

        cfg = rank_cfg if isinstance(rank_cfg, dict) else get_path_matrix_cfg(paper=paper)
        stamped = stamp_window_scores(work, cfg)
        if stamped.get("ranking") is not None:
            work["ranking"] = stamped.get("ranking")
        if stamped.get("y_oo") is not None:
            work["y_oo"] = stamped.get("y_oo")
        if stamped.get("y_co") is not None:
            work["y_co"] = stamped.get("y_co")
        work["fusion_w_oo"] = cfg.get("fusion_w_oo")
        work["fusion_w_oc"] = cfg.get("fusion_w_oc")
        work["fusion_w_co"] = cfg.get("fusion_w_co")
        fa = work.get("factor_anomaly")
        if isinstance(fa, dict) and fa.get("fatal_tau"):
            work["y_τc"] = None
            work["predicted_score_τc"] = None
            work["y_τc_ridge"] = None
            work["score_rem"] = None
            work["predicted_score_rem"] = None
            work["y_co"] = None
            work["ranking"] = None
    except Exception:  # noqa: BLE001
        logger.debug("stamp ranking in book_fields failed", exc_info=True)
    # 权重优先簿内已算（含 theme/variance）；缺则用当前配置
    return {
        "predicted_score_eod": work.get("predicted_score_eod", work.get("predicted_score")),
        "predicted_score_oo": work.get("predicted_score_oo", work.get("y_oo")),
        "predicted_score_eod_rem": work.get("predicted_score_eod_rem"),
        "y_tau": work.get("y_τc"),
        "predicted_score_tau_delta": work.get("predicted_score_tau_delta"),
        "predicted_score_tau_cascade": work.get("predicted_score_tau_cascade"),
        "predicted_score_blend": work.get("predicted_score_blend"),
        "predicted_score_blend_tau_cc": work.get("predicted_score_blend_tau_cc"),
        "predicted_score_blend_vs": work.get("predicted_score_blend_vs"),
        "decision_score": work.get("decision_score"),
        "realized_t1_to_tau": work.get("realized_t1_to_tau"),
        "score_rem": work.get("score_rem"),
        "predicted_score_rem": work.get("predicted_score_rem"),
        "as_of_tau": work.get("as_of_tau") or work.get("rem_tau"),
        "y_spec_tau": work.get("y_spec_tau"),
        "features_tau": work.get("features_tau"),
        "features_tau_fill": fill,
        "formula_terms_tau": formula_terms_tau,
        "score_formula_terms_tau": formula_terms_tau,
        "score_formula_tau": score_formula_tau,
        "factor_coefficients_tau": coefs_tau or None,
        "gap_pct": work.get("gap_pct"),
        "event_prior": work.get("event_prior"),
        "dual_score_fusion": live_fusion or "blend",
        "dual_score_weights": live_w,
        "dual_score_window": work.get("dual_score_window"),
        "eod_feature_as_of": work.get("eod_feature_as_of"),
        "trade_day": work.get("trade_day"),
        "open_t": work.get("open_t"),
        "open_t_source": work.get("open_t_source"),
        "factor_anomaly": work.get("factor_anomaly"),
        "dual_score_head": work.get("dual_score_head"),
        "dual_score_single_head": bool(work.get("dual_score_single_head")),
        "y_mu": work.get("y_mu"),
        "y_sigma": work.get("y_sigma"),
        "y_disagree": work.get("y_disagree"),
        "y_check": work.get("y_check"),
        "y_sign_conflict": bool(work.get("y_sign_conflict")),
        "eod_trust": work.get("eod_trust"),
        "y_tau_to_close": work.get("y_tau_to_close"),
        "y_tau_to_close_src": work.get("y_tau_to_close_src"),
        "y_state": work.get("y_state"),
        "y_τc": work.get("y_τc"),
        "predicted_score_τc": work.get("predicted_score_τc"),
        "y_r": work.get("y_r"),
        "y_r_hat": work.get("y_r_hat"),
        "predicted_score_r": work.get("predicted_score_r"),
        "y_spec_τc": work.get("y_spec_τc"),
        "y_spec_r": work.get("y_spec_r"),
        "formula_terms_r": formula_terms_r
        or work.get("formula_terms_r")
        or work.get("score_formula_terms_r"),
        "score_formula_terms_r": formula_terms_r
        or work.get("score_formula_terms_r")
        or work.get("formula_terms_r"),
        "predicted_score_on": work.get("predicted_score_on")
        if work.get("predicted_score_on") is not None
        else work.get("y_co"),
        "y_spec_co": work.get("y_spec_co") or work.get("y_spec_on"),
        "features_co": work.get("features_co") or work.get("features_on"),
        "formula_terms_co": formula_terms_co
        or work.get("formula_terms_co")
        or work.get("score_formula_terms_co")
        or work.get("formula_terms_on")
        or work.get("score_formula_terms_on"),
        "score_formula_terms_co": formula_terms_co
        or work.get("score_formula_terms_co")
        or work.get("formula_terms_co")
        or work.get("score_formula_terms_on")
        or work.get("formula_terms_on"),
        "score_formula_co": work.get("score_formula_co"),
        "co_y_spec": work.get("co_y_spec"),
        "dual_score_co_head": work.get("dual_score_co_head"),
        "ranking": work.get("ranking"),
        "y_oo": work.get("y_oo"),
        "y_co": work.get("y_co"),
        "fusion_w_oo": work.get("fusion_w_oo"),
        "fusion_w_oc": work.get("fusion_w_oc"),
        "fusion_w_co": work.get("fusion_w_co"),
    }

