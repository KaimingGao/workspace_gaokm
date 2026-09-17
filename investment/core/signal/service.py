"""SignalService / ResearchSignalService（SS encapsulate C）。

委托现有 ``score_stock`` / ``rank_cross_section`` / ``rank_cluster_pools``，
不重写 dual_score / scorer。上层经本 Service 取信封；历史 dict 走 ``as_dict()``。
"""


import logging

logger = logging.getLogger(__name__)
import threading
from typing import Any, Dict, List, Optional

from core.signal.types import BookResult, ScoreResult

_DEFAULT: Optional["SignalService"] = None
_RESEARCH: Optional["ResearchSignalService"] = None

_METRICS: Dict[str, int] = {
    "score_ok": 0,
    "score_fail": 0,
    "score_production_ok": 0,
    "score_heuristic": 0,
    "rank_cs_ok": 0,
    "rank_cs_fail": 0,
    "rank_cluster_ok": 0,
    "rank_cluster_fail": 0,
}
_METRICS_LOCK = threading.Lock()


def _bump_metric(key: str, n: int = 1) -> None:
    with _METRICS_LOCK:
        _METRICS[key] = int(_METRICS.get(key) or 0) + int(n)


def metrics_snapshot() -> Dict[str, int]:
    with _METRICS_LOCK:
        return dict(_METRICS)


def reset_metrics() -> None:
    with _METRICS_LOCK:
        for k in list(_METRICS.keys()):
            _METRICS[k] = 0


class SignalService:
    """生产默认打分口：组信封、ŷ 标尺门禁；读数仍经 DataService。"""

    production: bool = True

    def score_one(self, stock_code: str, **kw: Any) -> ScoreResult:
        from core.signal.score_stock import score_stock

        if self.production:
            kw.setdefault("bypass_quality_gate", False)
        raw = score_stock(stock_code, **kw)
        result = ScoreResult.from_score_stock(raw if isinstance(raw, dict) else {})
        if result.success:
            _bump_metric("score_ok")
        else:
            _bump_metric("score_fail")
        if result.production_ok:
            _bump_metric("score_production_ok")
        if result.scale == "heuristic_0_100":
            _bump_metric("score_heuristic")
        return result

    def rank_cross_section(
        self,
        codes: Optional[List[str]] = None,
        **kw: Any,
    ) -> BookResult:
        from core.signal.cross_section import rank_cross_section

        raw = rank_cross_section(codes, **kw)
        book = BookResult.from_rank(
            raw if isinstance(raw, dict) else {},
            kind="cross_section",
        )
        _bump_metric("rank_cs_ok" if book.success else "rank_cs_fail")
        return book

    def rank_cluster_pools(
        self,
        codes: Optional[List[str]] = None,
        **kw: Any,
    ) -> BookResult:
        from core.signal.cluster.rank import rank_cluster_pools

        raw = rank_cluster_pools(codes, **kw)
        book = BookResult.from_rank(
            raw if isinstance(raw, dict) else {},
            kind="cluster_pools",
        )
        _bump_metric("rank_cluster_ok" if book.success else "rank_cluster_fail")
        return book

    def book_fields(
        self,
        item: Optional[dict],
        *,
        rank_cfg: Optional[dict] = None,
        paper: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """仅双层 ŷ 透传字段（不整包翻写 signal_item，避免污染观察/持仓摘要）。

        ``paper`` / ``rank_cfg``：数据中心与持仓共用 rank_lots 权（fusion_w_oo/oc/co）。
        """
        if not isinstance(item, dict) or not item:
            return {}
        try:
            from core.signal.dual_score import dual_score_book_fields

            return dict(dual_score_book_fields(item, rank_cfg=rank_cfg, paper=paper) or {})
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in service.py", exc_info=True)
            return {}

    def annotate_item(self, item: Optional[dict]) -> Dict[str, Any]:
        """对齐双层 ŷ 后返回完整 item 副本（持仓打包用）。观察摘要请用 ``book_fields``。"""
        packed = dict(item or {}) if isinstance(item, dict) else {}
        if not packed:
            return packed
        try:
            from core.signal.dual_score import align_trade_score_fields

            packed.update(self.book_fields(packed))
            # 保留 score_one 的 PIT dual_score_window，勿用无行情时钟覆盖
            align_trade_score_fields(packed, write_score=False, refresh_window=False)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in service.py", exc_info=True)
            pass
        return packed

    def pack_holding_row(
        self,
        item: Optional[dict],
        *,
        cluster_mode: Any = None,
        rank_cfg: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """持仓表列行：heuristic 不进表列 score；ŷ 走 decision_score。

        ``rank_cfg`` 为 rank_lots 权（fusion_w_oo/oc/co）；缺则默认 0.5/0.5/0。
        表列 ranking 由 ŷ_oo/ŷ_oc 现算，不用 dual_score 缺口抬升的 ŷ_trade。
        """
        from core.signal.dual_score import (
            decision_score_for_item,
            is_heuristic_score_scale,
        )

        packed = self.annotate_item(item)
        d_sc = decision_score_for_item(packed)
        try:
            heu = is_heuristic_score_scale(packed)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in service.py", exc_info=True)
            heu = str(packed.get("return_model_source") or "") == "oos_failed_heuristic"
        if heu:
            table_score = packed.get("score_cluster")
            if table_score is None:
                table_score = packed.get("score_global")
            if table_score is None:
                table_score = d_sc
        else:
            table_score = (
                d_sc
                if d_sc is not None
                else packed.get("score", packed.get("predicted_score"))
            )
        out: Dict[str, Any] = {
            "stock_code": packed.get("stock_code"),
            "stock_name": packed.get("stock_name"),
            "score": table_score,
            "decision_score": d_sc if d_sc is not None else packed.get("decision_score"),
            "predicted_score": packed.get("predicted_score", packed.get("score"))
            if not heu
            else None,
            "sub_scores": packed.get("sub_scores"),
            "factor_contrib": packed.get("factor_contrib"),
            "reasons": packed.get("reasons") or packed.get("score_reasons"),
            "hard_reject": packed.get("hard_reject"),
            "reject_reason": packed.get("reject_reason"),
            "weight_source": packed.get("weight_source"),
            "cluster_label": packed.get("cluster_label"),
            "cluster_mode": packed.get("cluster_mode") or cluster_mode,
            "cluster_version": packed.get("cluster_version"),
            "score_global": packed.get("score_global"),
            "score_cluster": packed.get("score_cluster"),
            "return_model_source": packed.get("return_model_source"),
            "return_model": packed.get("return_model"),
            "score_scale": packed.get("score_scale")
            or ("heuristic_0_100" if heu else None),
            "heuristic_score": packed.get("heuristic_score")
            or (packed.get("score") if heu else None),
            "score_track": packed.get("score_track"),
            "factor_coefficients": packed.get("factor_coefficients"),
            "score_formula": packed.get("score_formula"),
            "score_formula_terms": packed.get("score_formula_terms"),
            "below_min_score": bool(packed.get("below_min_score")),
        }
        for k, v in packed.items():
            if (
                k.startswith("predicted_score")
                or k.startswith("score_")
                or k.startswith("nowcast_")
                or k.startswith("dual_score_")
                or k.startswith("y_")
                or k in (
                    "gap_pct",
                    "event_prior",
                    "as_of_tau",
                    "features_tau",
                    "formula_terms_tau",
                    "factor_coefficients_tau",
                    "realized_t1_to_tau",
                    "decision_score",
                    "heuristic_score",
                    "score_track",
                    "eod_trust",
                    "eod_feature_as_of",
                    "open_t",
                    "open_t_source",
                    "factor_anomaly",
                    "ranking",
                    "fusion_w_oo",
                    "fusion_w_oc",
                    "fusion_w_co",
                    "features_on",
                    "formula_terms_on",
                    "y_spec_on",
                )
            ):
                out[k] = v
        try:
            from core.paper.rebalance.path_matrix import get_path_matrix_cfg
            from core.signal.yhat_windows import stamp_window_scores

            cfg = rank_cfg if isinstance(rank_cfg, dict) else get_path_matrix_cfg()
            stamped = stamp_window_scores(packed, cfg)
            for k in ("y_oo", "y_oc", "y_co", "y_τc", "ranking"):
                if stamped.get(k) is not None:
                    out[k] = stamped[k]
            fa = packed.get("factor_anomaly") or out.get("factor_anomaly")
            if isinstance(fa, dict) and fa.get("fatal_tau"):
                out["y_oc"] = None
                out["y_co"] = None
                out["ranking"] = None
                out["predicted_score_tau"] = None
            if cfg.get("fusion_w_oo") is not None:
                out["fusion_w_oo"] = cfg.get("fusion_w_oo")
            if cfg.get("fusion_w_oc") is not None:
                out["fusion_w_oc"] = cfg.get("fusion_w_oc")
            if cfg.get("fusion_w_co") is not None:
                out["fusion_w_co"] = cfg.get("fusion_w_co")
        except Exception:  # noqa: BLE001
            logger.debug("stamp holding ranking failed", exc_info=True)
        sr = ScoreResult.from_score_stock(
            {"success": True, "stock_code": packed.get("stock_code"), "signal_item": packed}
        )
        out["production_ok"] = sr.production_ok
        if sr.gate_reason:
            out.setdefault("gate_reason", sr.gate_reason)
        return out


class ResearchSignalService(SignalService):
    """研究默认：可绕过日线质量门禁；启发式仍标 ``production_ok=False``。"""

    production: bool = False

    def score_one(self, stock_code: str, **kw: Any) -> ScoreResult:
        kw.setdefault("bypass_quality_gate", True)
        return super().score_one(stock_code, **kw)

    def rank_cross_section(
        self,
        codes: Optional[List[str]] = None,
        **kw: Any,
    ) -> BookResult:
        kw.setdefault("bypass_quality_gate", True)
        return super().rank_cross_section(codes, **kw)


def get_default_signal_service() -> SignalService:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = SignalService()
    return _DEFAULT


def get_research_signal_service() -> ResearchSignalService:
    global _RESEARCH
    if _RESEARCH is None:
        _RESEARCH = ResearchSignalService()
    return _RESEARCH


def set_default_signal_service(svc: Optional[SignalService]) -> None:
    """测试注入生产默认 Service；svc=None 时一并清空 research。"""
    global _DEFAULT, _RESEARCH
    _DEFAULT = svc
    if svc is None:
        _RESEARCH = None


def set_research_signal_service(svc: Optional[ResearchSignalService]) -> None:
    global _RESEARCH
    _RESEARCH = svc
