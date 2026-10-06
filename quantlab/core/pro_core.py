"""专业核心三轨（DC / FM / RK）共享工具。

见 docs/archive/pro-core-strengthen.md。不写 OMS；只加深路径内可信度。
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 财务可用日覆盖软/硬阈值（与 maturity/DQ 同口径）
ANN_MISSING_SOFT_MAX = 0.35
ANN_MISSING_HARD_MAX = 0.55
REAL_MULTI_SOFT_MIN = 0.5


def assess_pit_depth(
    *,
    sample_status: Optional[dict] = None,
    fundamentals_history: Optional[dict] = None,
) -> Dict[str, Any]:
    """DC0 · 财务 PIT 深度快照（覆盖 + ann_missing 同口径）。"""
    ss = sample_status or {}
    fund = fundamentals_history or ss.get("fundamentals_history") or {}
    try:
        ann_ratio = fund.get("ann_missing_code_ratio")
        ann_ratio_f = float(ann_ratio) if ann_ratio is not None else None
    except (TypeError, ValueError):
        ann_ratio_f = None
    try:
        real_cov = fund.get("real_multi_coverage")
        real_cov_f = float(real_cov) if real_cov is not None else None
    except (TypeError, ValueError):
        real_cov_f = None

    soft_ok = True
    hard_ok = True
    notes: List[str] = []
    if ann_ratio_f is not None:
        if ann_ratio_f > ANN_MISSING_HARD_MAX:
            hard_ok = False
            soft_ok = False
            notes.append(f"ann_missing_code_ratio={ann_ratio_f} 超过硬阈值 {ANN_MISSING_HARD_MAX}")
        elif ann_ratio_f > ANN_MISSING_SOFT_MAX:
            soft_ok = False
            notes.append(f"ann_missing_code_ratio={ann_ratio_f} 超过软阈值 {ANN_MISSING_SOFT_MAX}")
    else:
        soft_ok = False
        notes.append("ann_missing_code_ratio 缺失")

    if real_cov_f is not None and real_cov_f < REAL_MULTI_SOFT_MIN:
        soft_ok = False
        notes.append(f"real_multi_coverage={real_cov_f} < {REAL_MULTI_SOFT_MIN}")

    top = fund.get("ann_missing_top") or fund.get("ann_missing_codes") or []
    if isinstance(top, dict):
        top_codes = list(top.keys())[:20]
    elif isinstance(top, list):
        top_codes = [
            str(x.get("stock_code") or x.get("code") or x).strip()
            for x in top[:20]
            if x
        ]
        top_codes = [c for c in top_codes if c]
    else:
        top_codes = []

    return {
        "ok": hard_ok,
        "soft_ok": soft_ok,
        "track": "DC0",
        "ann_missing_code_ratio": ann_ratio_f,
        "real_multi_coverage": real_cov_f,
        "ann_missing_soft_max": ANN_MISSING_SOFT_MAX,
        "ann_missing_hard_max": ANN_MISSING_HARD_MAX,
        "real_multi_soft_min": REAL_MULTI_SOFT_MIN,
        "ann_missing_top_codes": top_codes,
        "notes": notes,
        "honest_label": (
            "pit_clean"
            if soft_ok and hard_ok
            else ("pit_soft_debt" if hard_ok else "pit_hard_debt")
        ),
    }


def filter_halted_bars(
    bars: Iterable[dict],
    *,
    drop_zero_volume: bool = True,
    drop_keyword_halt: bool = True,
) -> Tuple[List[dict], Dict[str, Any]]:
    """DC1 · 过滤疑似停牌/不可交易 bar（零量或关键词）。"""
    kept: List[dict] = []
    dropped = 0
    reasons: Dict[str, int] = {}
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        why = None
        if drop_zero_volume:
            vol = b.get("volume")
            if vol is None:
                vol = b.get("vol")
            try:
                if vol is not None and float(vol) <= 0:
                    why = "zero_volume"
            except (TypeError, ValueError):
                pass
        if why is None and drop_keyword_halt:
            blob = " ".join(
                str(b.get(k) or "")
                for k in ("status", "trade_status", "remark", "name", "stock_name")
            )
            from core.market.calendar import halt_hint

            if halt_hint(blob).get("possible_halt"):
                why = "halt_keyword"
        if why:
            dropped += 1
            reasons[why] = reasons.get(why, 0) + 1
            continue
        kept.append(b)
    audit = {
        "track": "DC1",
        "input_n": dropped + len(kept),
        "kept_n": len(kept),
        "dropped_n": dropped,
        "drop_reasons": reasons,
        "note": "最小停牌过滤：零量 + 关键词；非完整停牌主数据",
    }
    return kept, audit


def assert_adjust_policy_consistent(
    *,
    requested: Optional[str],
    observed: Optional[str],
    allow_missing_observed: bool = True,
) -> Dict[str, Any]:
    """DC2 · 复权策略一致性；混用则 ok=False。"""
    from core.data.facade import normalize_adjust_policy

    req = normalize_adjust_policy(requested or "qfq")
    obs_raw = (observed or "").strip().lower() if observed else ""
    if not obs_raw:
        return {
            "ok": bool(allow_missing_observed),
            "track": "DC2",
            "requested": req,
            "observed": None,
            "consistent": bool(allow_missing_observed),
            "error": None if allow_missing_observed else "observed_adjust_missing",
        }
    obs = normalize_adjust_policy(obs_raw)
    ok = obs == req
    return {
        "ok": ok,
        "track": "DC2",
        "requested": req,
        "observed": obs,
        "consistent": ok,
        "error": None if ok else f"adjust_mismatch requested={req} observed={obs}",
    }


def ingest_nudge_payload(
    *,
    codes: Optional[List[str]] = None,
    sample_status: Optional[dict] = None,
    limit: int = 30,
) -> Dict[str, Any]:
    """DC3 · 从 ann_missing TopN 生成 ingest 催办载荷。"""
    pit = assess_pit_depth(sample_status=sample_status)
    top = list(codes or pit.get("ann_missing_top_codes") or [])
    top = [str(c).strip() for c in top if str(c).strip()][: max(1, int(limit or 30))]
    return {
        "ok": True,
        "track": "DC3",
        "codes": top,
        "count": len(top),
        "action": "ingest_real_fundamentals_history",
        "hint": "平台「催办 ingest」或 CLI sample_ops_run.py ingest-history",
        "pit_depth": pit,
    }


def regime_position_scale(*, regime: Optional[dict] = None) -> Dict[str, Any]:
    """RK1 · 将 regime 评估映射为仓位上限缩放（与 vol_scale 相乘）。

    P3：``regime.apply_position_scale=false`` 时固定 scale=1（只记标签，不缩仓）。
    """
    try:
        from core.signal.config import load_signal_config

        rcfg = (load_signal_config() or {}).get("regime") or {}
        if rcfg.get("apply_position_scale") is False:
            label = str(
                (regime or {}).get("label")
                or (regime or {}).get("regime")
                or (regime or {}).get("state")
                or ""
            ).lower()
            return {
                "ok": True,
                "track": "RK1",
                "scale": 1.0,
                "label": label or None,
                "source": "apply_position_scale=false",
            }
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in pro_core.py", exc_info=True)
        pass
    r = regime or {}
    label = str(r.get("label") or r.get("regime") or r.get("state") or "").lower()
    adj = r.get("adjustments") if isinstance(r.get("adjustments"), dict) else {}
    scale = 1.0
    source = "default"
    if adj.get("position_scale") is not None:
        try:
            scale = max(0.2, min(1.0, float(adj["position_scale"])))
            source = "adjustments.position_scale"
        except (TypeError, ValueError):
            scale = 1.0
    elif label in ("weak", "weak_trend", "chop", "high_vol", "risk_off", "bear"):
        scale = 0.8
        source = f"label:{label}"
    elif label in ("strong", "strong_trend", "bull", "risk_on"):
        scale = 1.0
        source = f"label:{label}"
    elif label == "neutral":
        scale = 0.9
        source = f"label:{label}"
    return {
        "ok": True,
        "track": "RK1",
        "scale": scale,
        "label": label or None,
        "source": source,
    }


def style_soft_caps_from_exposure(
    exposure: Optional[dict],
    *,
    max_style_pct: float = 40.0,
) -> Dict[str, Any]:
    """RK0 · 从暴露矩阵提取风格超限软约束提示。"""
    ex = exposure or {}
    styles = ex.get("styles") or ex.get("style") or ex.get("by_style") or {}
    if not isinstance(styles, dict):
        styles = {}
    over: List[Dict[str, Any]] = []
    for name, val in styles.items():
        try:
            pct = float(val if not isinstance(val, dict) else val.get("pct") or val.get("weight_pct") or 0)
        except (TypeError, ValueError):
            continue
        if pct > float(max_style_pct):
            over.append({"style": str(name), "pct": pct, "max_pct": float(max_style_pct)})
    return {
        "ok": not over,
        "track": "RK0",
        "max_style_pct": float(max_style_pct),
        "over_limit": over,
        "note": "软约束：超限写入 optimize 告警，由预算缩量路径消化",
    }
