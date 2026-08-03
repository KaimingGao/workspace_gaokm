"""观察名单研究摘要：评分/倾向/超额/量比/估值/同业/硬拒绝（不改账本）。

列表页必须轻量：跳过 fundamentals / peer / 独立指数重拉；
超额优先用 score 内已有 factors；超时不阻塞关池。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from datetime import datetime
from typing import Any, Dict, List, Optional

STANCE_SHORT = {
    "buy_light": "轻仓",
    "probe": "关注",
    "wait": "观望",
    "avoid": "观望",
    "insufficient": "—",
}

# 列表页：单票与整批上限（秒）；超时后不等待残余线程
_INSIGHT_STOCK_TIMEOUT = 12.0
_INSIGHT_BATCH_TIMEOUT = 90.0
_INSIGHT_MAX_WORKERS = 6
# 与观察池常见规模对齐（watching 上限约 80）；勿默认砍到 30 导致尾部无分
_INSIGHT_DEFAULT_LIMIT = 80
_INSIGHT_HARD_CAP = 80


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _days_since(iso: Optional[str]) -> Optional[int]:
    raw = str(iso or "").strip()
    if not raw:
        return None
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw)
        else:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d")
        return max(0, (datetime.now() - dt).days)
    except Exception:
        return None


def _blank(code: str, *, added_at: Optional[str] = None, error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "stock_code": code,
        "ok": False,
        "score": None,
        "stance_code": None,
        "stance_short": "—",
        "stance_label": None,
        "hard_reject": False,
        "reject_reason": None,
        "excess_return_pct": None,
        "excess_label": None,
        "volume": None,
        "volume_ratio": None,
        "pe": None,
        "pb": None,
        # 与交易执行持仓分同源：score_stock 读 cluster_scoring
        "cluster_mode": None,
        "weight_source": None,
        "cluster_label": None,
        "cluster_version": None,
        "score_global": None,
        "score_cluster": None,
        "delta_vs_global": None,
        "min_score": None,
        "below_min_score": False,
        "score_formula": None,
        "score_reasons": None,
        "added_at": added_at,
        "days_watched": _days_since(added_at),
        "peer_rank": None,
        "peer_count": None,
        "peer_line": None,
        "error": error,
    }


def _spot_valuation_map(codes: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    """从本地 A 股现货缓存取 PE/PB（不触发远端拉取）。"""
    out: Dict[str, Dict[str, Optional[float]]] = {}
    want = {str(c).zfill(6) for c in codes if str(c).isdigit() and len(str(c).strip()) <= 6}
    if not want:
        return out
    try:
        from core.ports.market import load_disk_spot, spot_row_get, spot_to_float

        # 估值快照可稍旧；空着不如用几天前的现货缓存
        rows = load_disk_spot(max_age_hours=24 * 14) or []
    except Exception:
        return out
    for row in rows:
        c = str(spot_row_get(row, "code") or "").zfill(6)
        if c not in want:
            continue
        out[c] = {
            "pe": spot_to_float(spot_row_get(row, "pe")),
            "pb": spot_to_float(spot_row_get(row, "pb")),
        }
    return out


def _insight_one(
    code: str,
    *,
    added_at: Optional[str] = None,
    valuation: Optional[Dict[str, Optional[float]]] = None,
    paper_ctx: Optional[dict] = None,
) -> Dict[str, Any]:
    """单票轻量摘要：与交易执行同源 ``score_stock``（含分组权）；不二次打指数/基本面/同业。"""
    out = _blank(code, added_at=added_at)
    try:
        from core.signal.score_stock import score_stock
        from core.stance import compute_buy_stance
        from core.ports.market import query_quote

        # cluster_mode=None → 读 signal_config.cluster_scoring（active 时用组权，同持仓表）
        scored = score_stock(code, horizon_days=3, skip_fundamentals=True)
        quote = (scored or {}).get("quote") or {}
        if not quote:
            quote = query_quote(code)
        item = (scored or {}).get("signal_item") or {}
        out["score"] = _f(item.get("score"))
        out["hard_reject"] = bool(item.get("hard_reject"))
        out["reject_reason"] = (item.get("reject_reason") or None) if out["hard_reject"] else None
        out["cluster_mode"] = item.get("cluster_mode") or scored.get("cluster_mode")
        out["weight_source"] = item.get("weight_source")
        out["cluster_label"] = item.get("cluster_label")
        out["cluster_version"] = item.get("cluster_version")
        out["score_global"] = _f(item.get("score_global"))
        out["score_cluster"] = _f(item.get("score_cluster"))
        out["delta_vs_global"] = _f(item.get("delta_vs_global"))
        # 选股门槛仅标注，不抹掉分数
        try:
            from core.signal.score_display import annotate_score_gate

            gate = annotate_score_gate(out["score"], paper=paper_ctx)
            out["min_score"] = gate["min_score"]
            out["below_min_score"] = gate["below_min_score"]
        except Exception:
            out["min_score"] = None
            out["below_min_score"] = False
        reasons = item.get("reasons") or []
        out["score_reasons"] = list(reasons) if isinstance(reasons, list) else []
        # 公式权向量：active 组权时用映射权
        weights = None
        src = str(item.get("weight_source") or "")
        if src.startswith("cluster:"):
            try:
                from core.signal.cluster_live import lookup_code_weights

                mapped = lookup_code_weights(code) or {}
                if isinstance(mapped.get("weights"), dict):
                    weights = mapped["weights"]
            except Exception:
                weights = None
        try:
            from services.paper_helpers import _build_score_formula

            out["score_formula"] = _build_score_formula(
                {
                    "sub_scores": item.get("sub_scores"),
                    "factor_contrib": item.get("factor_contrib"),
                    "weights": weights,
                }
            ) or None
        except Exception:
            out["score_formula"] = None
        out["volume"] = quote.get("volume")
        factors = item.get("factors") or {}
        out["volume_ratio"] = _f(factors.get("volume_ratio") or factors.get("turnover_ratio"))
        pe = _f(factors.get("value_pe") or factors.get("pe"))
        pb = _f(factors.get("value_pb") or factors.get("pb"))
        if pe is None and valuation:
            pe = _f(valuation.get("pe"))
        if pb is None and valuation:
            pb = _f(valuation.get("pb"))
        out["pe"] = round(pe, 1) if pe is not None else None
        out["pb"] = round(pb, 2) if pb is not None else None

        stance = compute_buy_stance(quote=quote, signal_item=item)
        code_st = stance.get("stance_code")
        out["stance_code"] = code_st
        out["stance_label"] = stance.get("stance_label")
        out["stance_short"] = STANCE_SHORT.get(str(code_st or ""), "—")

        excess = _f(factors.get("excess_return_pct"))
        if excess is not None:
            out["excess_return_pct"] = excess
            if excess >= 2:
                out["excess_label"] = "强"
            elif excess <= -2:
                out["excess_label"] = "弱"
            else:
                out["excess_label"] = "平"
        else:
            rs = _f((item.get("sub_scores") or {}).get("relative_strength"))
            if rs is not None:
                out["excess_label"] = "RS" + str(int(round(rs)))

        out["ok"] = bool((scored or {}).get("success")) or quote.get("success") is True
        if not out["ok"] and (scored or {}).get("error"):
            out["error"] = scored.get("error")
    except Exception as e:
        out["error"] = str(e)
    return out


def build_watching_insights(
    codes: List[str],
    *,
    added_at_by_code: Optional[Dict[str, str]] = None,
    limit: int = _INSIGHT_DEFAULT_LIMIT,
) -> Dict[str, Any]:
    added = added_at_by_code or {}
    cleaned = [
        str(c).strip() for c in (codes or []) if str(c).strip()
    ][: max(1, min(int(limit or _INSIGHT_DEFAULT_LIMIT), _INSIGHT_HARD_CAP))]

    if not cleaned:
        return {
            "ok": True,
            "count": 0,
            "items": [],
            "note": "观察摘要为空",
        }

    # paper 门槛只读一次，避免每票 load_paper
    paper_ctx = None
    try:
        from core.paths import PAPER_PATH
        from core.paper import load_paper
        import os

        if os.path.isfile(PAPER_PATH):
            paper_ctx = load_paper(PAPER_PATH)
    except Exception:
        paper_ctx = None

    valuation_by = _spot_valuation_map(cleaned)
    items_by_code: Dict[str, Dict[str, Any]] = {}
    workers = min(_INSIGHT_MAX_WORKERS, len(cleaned))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {}
        for c in cleaned:
            key = str(c).zfill(6) if str(c).isdigit() else str(c)
            futures[
                pool.submit(
                    _insight_one,
                    c,
                    added_at=added.get(c) or added.get(str(c)),
                    valuation=valuation_by.get(key),
                    paper_ctx=paper_ctx,
                )
            ] = c
        try:
            for fut in as_completed(futures, timeout=_INSIGHT_BATCH_TIMEOUT):
                code = futures[fut]
                try:
                    items_by_code[code] = fut.result(timeout=_INSIGHT_STOCK_TIMEOUT)
                except Exception as e:
                    items_by_code[code] = _blank(
                        code,
                        added_at=added.get(code),
                        error=f"超时或失败: {e}",
                    )
        except FuturesTimeout:
            for fut, code in futures.items():
                if code in items_by_code:
                    continue
                if fut.done():
                    try:
                        items_by_code[code] = fut.result(timeout=0)
                    except Exception as e:
                        items_by_code[code] = _blank(
                            code,
                            added_at=added.get(code),
                            error=f"超时或失败: {e}",
                        )
                else:
                    items_by_code[code] = _blank(
                        code,
                        added_at=added.get(code),
                        error="整批超时",
                    )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    items = [
        items_by_code.get(c)
        or _blank(c, added_at=added.get(c), error="未完成")
        for c in cleaned
    ]
    truncated = max(0, len([str(c).strip() for c in (codes or []) if str(c).strip()]) - len(cleaned))
    note = "轻量观察摘要（跳过基本面/同业/独立指数重拉）；不改 stance 主契约。"
    if truncated:
        note += f" 本次仅返回前 {len(cleaned)} 只（截断 {truncated}）。"
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "truncated": truncated,
        "note": note,
    }
