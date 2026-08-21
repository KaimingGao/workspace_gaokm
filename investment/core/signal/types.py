"""SignalService 返回信封（SS encapsulate A）。

ScoreResult / BookResult 提供属性访问；as_dict() 与历史 dict 契约对齐。
"""


import logging

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from core.signal.gate import (
    SCALE_UNKNOWN,
    allows_production_yhat,
    infer_score_scale,
)


def _opt_float(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@dataclass
class ScoreResult:
    """单票打分信封。``as_dict()`` 保持 ``score_stock`` 历史形状。"""

    stock_code: str
    success: bool
    ok: bool
    item: Dict[str, Any]
    raw: Dict[str, Any]
    predicted_score: Optional[float]
    predicted_score_tau: Optional[float]
    predicted_score_blend: Optional[float]
    rank_key: Optional[float]
    scale: str
    production_ok: bool
    gate_reason: Optional[str]
    cluster_mode: Optional[str] = None
    kind: str = "score"

    @classmethod
    def from_score_stock(cls, raw: Optional[Mapping[str, Any]]) -> "ScoreResult":
        pack = dict(raw or {})
        item = dict(pack.get("signal_item") or {})
        success = bool(pack.get("success"))
        scale = infer_score_scale(item) if item else SCALE_UNKNOWN
        quality_gated = bool(pack.get("quality_gate") or item.get("quality_gate"))
        prod_ok, reason = allows_production_yhat(
            item,
            fetch_ok=success,
            quality_gated=quality_gated,
        )
        predicted = _opt_float(item.get("predicted_score_eod"))
        if predicted is None:
            predicted = _opt_float(item.get("predicted_score"))
        tau = _opt_float(item.get("predicted_score_tau"))
        if tau is None:
            tau = _opt_float(item.get("score_rem"))
        blend = _opt_float(item.get("predicted_score_blend"))
        rank_key = None
        try:
            from core.signal.dual_score import rank_key_for_item

            rank_key = rank_key_for_item(item)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in types.py", exc_info=True)
            rank_key = blend if blend is not None else predicted
        code = str(
            pack.get("stock_code")
            or item.get("stock_code")
            or ""
        ).strip()
        mode = pack.get("cluster_mode")
        if mode is None:
            mode = item.get("cluster_mode")
        return cls(
            stock_code=code,
            success=success,
            ok=success,
            item=item,
            raw=pack,
            predicted_score=predicted,
            predicted_score_tau=tau,
            predicted_score_blend=blend,
            rank_key=_opt_float(rank_key),
            scale=scale,
            production_ok=prod_ok,
            gate_reason=reason or None,
            cluster_mode=str(mode) if mode is not None else None,
        )

    def as_dict(self) -> Dict[str, Any]:
        out = dict(self.raw or {})
        out.setdefault("kind", self.kind)
        out.setdefault("ok", self.ok)
        out.setdefault("success", self.success)
        out["stock_code"] = self.stock_code or out.get("stock_code")
        out["production_ok"] = self.production_ok
        out["scale"] = self.scale
        out["rank_key"] = self.rank_key
        if self.gate_reason is not None:
            out.setdefault("gate_reason", self.gate_reason)
        if self.cluster_mode is not None:
            out.setdefault("cluster_mode", self.cluster_mode)
        item = dict(self.item or {})
        if item:
            item.setdefault("score_scale", self.scale)
            item["production_ok"] = self.production_ok
            if self.gate_reason and not item.get("gate_reason"):
                item["gate_reason"] = self.gate_reason
            out["signal_item"] = item
        return out

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)


@dataclass
class BookResult:
    """横截面 / 分池簿信封。``as_dict()`` 保持 ``rank_*`` 历史形状。"""

    kind: str
    success: bool
    ok: bool
    ranking: List[dict]
    book: List[dict]
    scored_all: List[dict]
    production_ok: bool
    gate_reason: Optional[str]
    raw: Dict[str, Any] = field(default_factory=dict)
    heuristic_count: int = 0
    yhat_count: int = 0

    @classmethod
    def from_rank(
        cls,
        raw: Optional[Mapping[str, Any]],
        *,
        kind: str = "cross_section",
    ) -> "BookResult":
        pack = dict(raw or {})
        # 拷贝行再戳 production_ok，避免污染调用方共享簿 / ranking 引用
        ranking = [
            dict(x) if isinstance(x, dict) else x for x in (pack.get("ranking") or [])
        ]
        book_src = pack.get("book")
        if book_src is None and ranking:
            book = [dict(x) if isinstance(x, dict) else x for x in ranking]
        else:
            book = [
                dict(x) if isinstance(x, dict) else x for x in (book_src or [])
            ]
        scored = [
            dict(x) if isinstance(x, dict) else x
            for x in (pack.get("scored_all") or [])
        ]
        success = bool(pack.get("success"))
        rows = ranking or book
        yhat_n = 0
        heu_n = 0
        first_fail = ""
        for it in rows:
            if not isinstance(it, dict):
                continue
            ok, reason = allows_production_yhat(it, fetch_ok=True)
            it["production_ok"] = ok
            if not ok and reason and "gate_reason" not in it:
                it["gate_reason"] = reason
            if ok:
                yhat_n += 1
            else:
                if infer_score_scale(it) == "heuristic_0_100" or reason.startswith(
                    "score_scale:heuristic"
                ):
                    heu_n += 1
                if not first_fail:
                    first_fail = reason
        if not success:
            prod_ok, g_reason = False, str(pack.get("error") or "rank_failed")
        elif not rows:
            prod_ok, g_reason = True, ""
        elif heu_n > 0:
            prod_ok, g_reason = False, first_fail or "score_scale:heuristic_0_100"
        else:
            prod_ok, g_reason = True, ""
        return cls(
            kind=str(kind or "cross_section"),
            success=success,
            ok=success,
            ranking=ranking,
            book=book,
            scored_all=scored,
            production_ok=prod_ok,
            gate_reason=g_reason or None,
            raw=pack,
            heuristic_count=heu_n,
            yhat_count=yhat_n,
        )

    def as_dict(self) -> Dict[str, Any]:
        out = dict(self.raw or {})
        out.setdefault("kind", self.kind)
        out.setdefault("ok", self.ok)
        out.setdefault("success", self.success)
        out["production_ok"] = self.production_ok
        if self.gate_reason is not None:
            out.setdefault("gate_reason", self.gate_reason)
        # 始终用已戳门禁的副本，覆盖 raw 里未戳的同名列表
        out["ranking"] = list(self.ranking)
        if self.book:
            out["book"] = list(self.book)
        if self.scored_all:
            out.setdefault("scored_all", list(self.scored_all))
        return out

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)
