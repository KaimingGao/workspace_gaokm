"""T 日事件先验（ŷ 外）：开盘缺口 / 板块广度。

契约：
  ŷ = ReturnScoreModel(...)     ← 唯一生产排序轴
  E = build_event_prior(...)    ← 先验（不改 ŷ）
  action = policy(ŷ, E)         ← soft_hold / warn
"""

from typing import Any, Dict, List, Optional, Sequence

EVENT_PRIOR_REASON = "event_prior_theme_gap"

DEFAULT_EVENT_PRIOR = {
    "mode": "off",  # off | risk | gate
    "gap_trigger_pct": 2.0,
    "sector_breadth_min": 0.5,
    "soft_hold_on_theme": True,
    "warn_only": False,
    # R0p：ŷ_τ 头门控（需 live tau_ridge_model.json；可读旧 rem_ridge_model.json）
    "rem_gate_enabled": True,
    "rem_soft_hold_min": 0.25,
}


def normalize_event_prior_mode(raw: Any) -> str:
    m = str(raw or "off").strip().lower()
    if m in ("risk", "warn", "warning"):
        return "risk"
    if m in ("gate", "block", "scale", "soft_hold"):
        return "gate"
    return "off"


def get_event_prior_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            config = {}
    raw = dict(DEFAULT_EVENT_PRIOR)
    raw.update(dict((config or {}).get("event_prior") or {}))
    raw["mode"] = normalize_event_prior_mode(raw.get("mode"))
    try:
        raw["gap_trigger_pct"] = float(
            raw.get("gap_trigger_pct")
            if raw.get("gap_trigger_pct") is not None
            else 2.0
        )
    except (TypeError, ValueError):
        raw["gap_trigger_pct"] = 2.0
    try:
        raw["sector_breadth_min"] = float(
            raw.get("sector_breadth_min")
            if raw.get("sector_breadth_min") is not None
            else 0.5
        )
    except (TypeError, ValueError):
        raw["sector_breadth_min"] = 0.5
    raw["soft_hold_on_theme"] = bool(
        raw.get("soft_hold_on_theme")
        if raw.get("soft_hold_on_theme") is not None
        else True
    )
    raw["warn_only"] = bool(raw.get("warn_only", False))
    raw["rem_gate_enabled"] = bool(
        raw.get("rem_gate_enabled") if raw.get("rem_gate_enabled") is not None else True
    )
    try:
        raw["rem_soft_hold_min"] = float(
            raw.get("rem_soft_hold_min")
            if raw.get("rem_soft_hold_min") is not None
            else 0.25
        )
    except (TypeError, ValueError):
        raw["rem_soft_hold_min"] = 0.25
    return raw


def _parse_open_price(quote: Optional[dict]) -> Optional[float]:
    if not isinstance(quote, dict):
        return None
    for key in ("open_raw", "open"):
        v = quote.get(key)
        if v is None:
            continue
        try:
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).replace("元", "").replace(",", "").strip()
            return float(s)
        except (TypeError, ValueError):
            continue
    return None


def gap_pct_from_quote_bars(
    quote: Optional[dict],
    bars: Optional[Sequence[dict]] = None,
    *,
    prev_close: Optional[float] = None,
) -> Optional[float]:
    """开盘相对昨收缺口（%）。统一 ``resolve_open_t``，不用现价涨跌反推昨收。"""
    from core.signal.session_pit import resolve_minute_tau_trade_date, resolve_open_t

    got = resolve_open_t(
        quote,
        bars,
        trade_day=resolve_minute_tau_trade_date(quote, bars),
    )
    open_px = got.get("open")
    pc = None
    if prev_close is not None:
        try:
            pc = float(prev_close)
        except (TypeError, ValueError):
            pc = None
    if pc is None:
        pc = got.get("prev_close")
    if open_px is None or pc is None:
        return None
    try:
        o = float(open_px)
        p = float(pc)
    except (TypeError, ValueError):
        return None
    if o <= 0 or p <= 0:
        return None
    return round((o / p - 1.0) * 100.0, 4)


def sector_gap_breadth(
    gaps_by_code: Dict[str, Optional[float]],
    *,
    gap_trigger_pct: float = 2.0,
) -> Optional[float]:
    """截面正缺口占比：gap≥trigger 的票 / 有缺口数据的票。"""
    vals = [float(g) for g in (gaps_by_code or {}).values() if g is not None]
    if not vals:
        return None
    hit = sum(1 for g in vals if g >= float(gap_trigger_pct))
    return round(hit / len(vals), 4)


def build_event_prior(
    *,
    gap_pct: Optional[float] = None,
    sector_breadth: Optional[float] = None,
    rem_yhat: Optional[float] = None,
    config: Optional[dict] = None,
    stock_code: Optional[str] = None,
) -> Dict[str, Any]:
    """由开盘缺口 + 可选板块广度 / ŷ_τ 构建事件先验；不改 ŷ_oo。"""
    cfg = get_event_prior_cfg(config)
    mode = cfg["mode"]
    trigger = float(cfg["gap_trigger_pct"])
    breadth_min = float(cfg["sector_breadth_min"])

    gap_ok = gap_pct is not None and float(gap_pct) >= trigger
    breadth_ok = sector_breadth is not None and float(sector_breadth) >= breadth_min
    # 个股大缺口即可触发；若有板块广度则要求同向共振更强（二者皆可，或仅缺口）
    theme = bool(
        gap_ok
        and (sector_breadth is None or breadth_ok or float(gap_pct or 0) >= trigger * 1.5)
    )
    rem_ok = False
    if cfg.get("rem_gate_enabled") and rem_yhat is not None:
        try:
            rem_ok = float(rem_yhat) >= float(cfg.get("rem_soft_hold_min") or 0.25)
        except (TypeError, ValueError):
            rem_ok = False

    actions: List[Dict[str, Any]] = []
    warnings: List[str] = []
    active = False

    if mode == "off":
        return {
            "success": True,
            "role": "event_prior",
            "mode": mode,
            "stock_code": stock_code,
            "active": False,
            "theme": False,
            "gap_pct": gap_pct,
            "sector_breadth": sector_breadth,
            "rem_yhat": rem_yhat,
            "actions": [],
            "warnings": [],
            "note": "事件先验关闭",
            "predicted_score_unchanged": True,
        }

    if theme or rem_ok:
        active = True
        if cfg.get("soft_hold_on_theme"):
            actions.append(
                {
                    "type": "soft_hold",
                    "reason": EVENT_PRIOR_REASON if theme else "event_prior_rem",
                    "gap_pct": gap_pct,
                    "sector_breadth": sector_breadth,
                    "rem_yhat": rem_yhat,
                }
            )
        if theme:
            msg = (
                f"事件先验·开盘缺口 {float(gap_pct):+.2f}%"
                if gap_pct is not None
                else "事件先验·主题日"
            )
            if sector_breadth is not None:
                msg += f" · 板块广度 {float(sector_breadth):.0%}"
            warnings.append(msg)
        if rem_ok:
            warnings.append(f"事件先验·ŷ_τ={float(rem_yhat):+.2f}%")
        if mode == "risk" or cfg.get("warn_only"):
            actions = [a for a in actions if a.get("type") != "soft_hold"]
            active = bool(warnings)

    return {
        "success": True,
        "role": "event_prior",
        "mode": mode,
        "stock_code": stock_code,
        "active": active and bool(actions or warnings),
        "theme": theme,
        "gap_pct": gap_pct,
        "sector_breadth": sector_breadth,
        "rem_yhat": rem_yhat,
        "actions": actions,
        "warnings": warnings,
        "note": "事件先验 · 不进 ŷ / 非因子",
        "predicted_score_unchanged": True,
    }


def should_soft_hold_for_low_score(prior: Optional[dict]) -> bool:
    """调仓卖出：低 ŷ 触发卖出时，主题缺口 / ŷ_τ 闸改为 soft hold。"""
    if not isinstance(prior, dict) or not prior.get("active"):
        return False
    for act in prior.get("actions") or []:
        if act.get("type") == "soft_hold":
            return True
    return False


def compute_sector_gap_breadth_live(
    codes: Sequence[str],
    *,
    gap_trigger_pct: float = 2.0,
    quotes: Optional[Dict[str, dict]] = None,
    focus_code: Optional[str] = None,
    use_sector_peers: bool = True,
) -> Dict[str, Any]:
    """P1b：批量行情算开盘缺口广度。

    ``use_sector_peers=True`` 且提供 ``focus_code`` 时：优先同 ``sector_map`` 标签同伴；
    同伴不足 3 只则回退全 ``codes`` 宇宙。
    刷簿路径传 ``use_sector_peers=False`` + 预取 ``quotes``：整池共享一个 breadth，
    与 τ 训练面板按日广度同构，且只需一次 batch 行情。
    """
    from core.data.facade import batch_get_quotes

    uniq = [str(c).strip() for c in codes if str(c).strip()]
    peer_codes = list(uniq)
    sector_label = None
    if use_sector_peers and focus_code:
        try:
            from core.portfolio_optimize import _sector_for, load_sector_map

            sm = load_sector_map() or {}
            sector_label = _sector_for(str(focus_code).strip(), sm)
            peers = [
                c
                for c in uniq
                if _sector_for(c, sm) == sector_label
            ]
            if str(focus_code).strip() not in peers:
                peers.append(str(focus_code).strip())
            if len(peers) >= 3:
                peer_codes = peers
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            peer_codes = list(uniq)

    got = dict(quotes or {})
    missing = [c for c in peer_codes if c not in got]
    if missing:
        try:
            got.update(batch_get_quotes(missing) or {})
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            pass
    gaps: Dict[str, Optional[float]] = {}
    for c in peer_codes:
        gaps[c] = gap_pct_from_quote_bars(got.get(c))
    breadth = sector_gap_breadth(gaps, gap_trigger_pct=gap_trigger_pct)
    return {
        "success": True,
        "breadth": breadth,
        "gaps": gaps,
        "n": len([g for g in gaps.values() if g is not None]),
        "gap_trigger_pct": gap_trigger_pct,
        "sector": sector_label,
        "peer_codes": peer_codes,
        "universe_mode": "sector_peers"
        if sector_label and peer_codes != uniq
        else "codes_universe",
    }


def build_event_prior_from_quote(
    quote: Optional[dict],
    bars: Optional[Sequence[dict]] = None,
    *,
    sector_breadth: Optional[float] = None,
    rem_yhat: Optional[float] = None,
    config: Optional[dict] = None,
    stock_code: Optional[str] = None,
    prev_close: Optional[float] = None,
) -> Dict[str, Any]:
    gap = gap_pct_from_quote_bars(quote, bars, prev_close=prev_close)
    code = stock_code or (quote or {}).get("stock_code")
    return build_event_prior(
        gap_pct=gap,
        sector_breadth=sector_breadth,
        rem_yhat=rem_yhat,
        config=config,
        stock_code=str(code) if code else None,
    )
