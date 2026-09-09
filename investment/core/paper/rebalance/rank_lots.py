"""策略调仓：9:30 用 y_fuse / y_on 排序，按 200/500 股下单。

规则（live 与历史回测共用）：
  - y_fuse = 加权(y_trade, y_nowcast)，表示预期今日收益（百分点）
  - y_on 表示预期隔夜收益（百分点）
  - ranking = (1 + y_fuse/100) × (1 + α × y_on/100) − 1；缺 y_on 视为 0
  - 展示为百分数（×100）。α 默认 0：隔夜项为 0，清仓等价于 y_fuse < 0
  - 已持仓且 ranking < 0 → 清仓（T+1 可卖部分）
  - ranking_score > rank入场 的票按分数取 Top-K：建仓或加仓
  - ranking_score > rank强 → 500 股，否则 200 股
  - 现金低于 cash_floor（默认 50 万）禁止买入；地板超过账户规模时按净值 20% 缩放
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RANK_ENTER = 0.012
DEFAULT_RANK_STRONG = 0.012
DEFAULT_CASH_FLOOR = 500_000.0
DEFAULT_INITIAL_CASH = 1_000_000.0
DEFAULT_Y_ON_ALPHA = 0.0
Y_ON_ALPHA_MAX = 10.0
LOT_BASE = 200
LOT_STRONG = 500

ACTION_OPEN = "open"
ACTION_ADD = "add"
ACTION_EXIT = "exit"
ACTION_HOLD = "hold"
ACTION_SKIP = "skip"


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def coerce_rank_threshold(raw: Any, default: float) -> float:
    """毛收益乘数 1.01/1.02、旧 0.20 → 净收益 0.01/0.02（展示 1%/2%）。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(default)
    if v != v:
        v = float(default)
    if v >= 0.5:
        if abs(v - 1.0) < 1e-9:
            return 0.01
        if abs(v - 1.002) < 1e-6:
            return 0.02
        return max(0.0, v - 1.0)
    if abs(v - 0.20) < 1e-6:
        return 0.02
    return v


def clamp_y_on_alpha(raw: Any, default: float = DEFAULT_Y_ON_ALPHA) -> float:
    v = _f(raw)
    if v is None:
        return float(default)
    return max(0.0, min(float(v), Y_ON_ALPHA_MAX))


def clamp_fusion_weight(raw: Any, default: float = 0.5) -> float:
    v = _f(raw)
    if v is None:
        return max(0.0, min(1.0, float(default)))
    return max(0.0, min(1.0, float(v)))


def ranking_score(
    y_fuse: Optional[float],
    y_on: Optional[float],
    *,
    y_on_alpha: float = DEFAULT_Y_ON_ALPHA,
) -> Optional[float]:
    """y_fuse、y_on 均为收益百分点；缺 y_on 视为 0。返回净收益（小数，展示×100 为百分数）。

    ranking = (1 + y_fuse/100) × (1 + α × y_on/100) − 1；α 默认 0（隔夜不参与）。
    """
    yf = _f(y_fuse)
    if yf is None:
        return None
    yo = _f(y_on)
    if yo is None:
        yo = 0.0
    alpha = clamp_y_on_alpha(y_on_alpha)
    return (1.0 + float(yf) / 100.0) * (1.0 + alpha * float(yo) / 100.0) - 1.0


def _normalize_lot(n: Any, default: int) -> int:
    try:
        v = max(100, int(n))
    except (TypeError, ValueError):
        v = int(default)
    return (v // 100) * 100


def lot_shares_for_rank(
    score: Optional[float],
    rank_strong: float,
    *,
    lot_base: int = LOT_BASE,
    lot_strong: int = LOT_STRONG,
) -> int:
    if score is None:
        return 0
    base = _normalize_lot(lot_base, LOT_BASE)
    strong = max(base, _normalize_lot(lot_strong, LOT_STRONG))
    if float(score) > float(rank_strong):
        return strong
    return base


def _lot_sizes_from_cfg(cfg: Optional[dict]) -> Tuple[int, int]:
    base = LOT_BASE
    strong = LOT_STRONG
    if isinstance(cfg, dict):
        if cfg.get("lot_base") is not None:
            base = cfg.get("lot_base")
        if cfg.get("lot_strong") is not None:
            strong = cfg.get("lot_strong")
    base_n = _normalize_lot(base, LOT_BASE)
    strong_n = max(base_n, _normalize_lot(strong, LOT_STRONG))
    return base_n, strong_n


def account_ref_equity(paper: Optional[dict]) -> float:
    """账户规模：initial_cash、现金+成本市值、现金 三者取大。"""
    if not isinstance(paper, dict):
        return 0.0
    initial = _f(paper.get("initial_cash")) or 0.0
    cash = _f(paper.get("cash")) or 0.0
    hv = 0.0
    for h in paper.get("holdings") or []:
        if not isinstance(h, dict):
            continue
        sh = _f(h.get("shares")) or 0.0
        px = _f(h.get("cost")) or _f(h.get("price")) or 0.0
        hv += sh * px
    return max(initial, cash + hv, cash)


def scale_cash_floor_to_account(
    floor: float,
    paper: Optional[dict] = None,
) -> float:
    """配置地板超过账户规模时，按净值 20% 保留现金（对齐回测 10万/50万）。

    1M 账本 + 50 万地板保持原值；~20 万纸面账本不再被 50 万地板永久拦买。
    """
    try:
        out = float(floor)
    except (TypeError, ValueError):
        out = DEFAULT_CASH_FLOOR
    if out != out:
        out = DEFAULT_CASH_FLOOR
    out = max(0.0, min(out, 1.0e8))
    ref = account_ref_equity(paper)
    if ref <= 0.0 or out <= ref + 1e-6:
        return out
    try:
        from core.paper.rebalance.cash_reserve import DEFAULT_MIN_CASH_PCT

        ratio = float(DEFAULT_MIN_CASH_PCT)
    except Exception:  # noqa: BLE001
        logger.debug("DEFAULT_MIN_CASH_PCT import failed", exc_info=True)
        ratio = 0.20
    return max(0.0, ref * ratio)


def get_rank_lot_cfg(
    paper: Optional[dict] = None,
    *,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    from core.paper.rebalance.path_matrix import get_path_matrix_cfg

    pm = get_path_matrix_cfg(paper=paper)
    try:
        enter = coerce_rank_threshold(pm.get("rank_enter", DEFAULT_RANK_ENTER), DEFAULT_RANK_ENTER)
    except (TypeError, ValueError):
        enter = DEFAULT_RANK_ENTER
    try:
        strong = coerce_rank_threshold(pm.get("rank_strong", DEFAULT_RANK_STRONG), DEFAULT_RANK_STRONG)
    except (TypeError, ValueError):
        strong = DEFAULT_RANK_STRONG
    enter = max(0.0, min(enter, 10.0))
    strong = max(0.0, min(strong, 10.0))
    if strong < enter:
        strong = enter
    try:
        floor_cfg = float(pm.get("cash_floor", DEFAULT_CASH_FLOOR))
    except (TypeError, ValueError):
        floor_cfg = DEFAULT_CASH_FLOOR
    floor_cfg = max(0.0, min(floor_cfg, 1.0e8))
    floor = scale_cash_floor_to_account(floor_cfg, paper)
    rules = (paper or {}).get("rules") if isinstance(paper, dict) else {}
    max_pos = 15
    if isinstance(rules, dict) and rules.get("max_positions") is not None:
        try:
            max_pos = max(1, int(rules.get("max_positions") or 15))
        except (TypeError, ValueError):
            max_pos = 15
    k = max(1, int(top_k)) if top_k is not None else max_pos
    try:
        from core.watching.store import WATCHING_MAX_SIZE

        k = min(k, int(WATCHING_MAX_SIZE))
    except Exception:  # noqa: BLE001
        k = min(k, 200)
    return {
        "rank_enter": enter,
        "rank_strong": strong,
        "cash_floor": floor,
        "cash_floor_configured": floor_cfg,
        "cash_floor_scaled": abs(float(floor) - float(floor_cfg)) > 1e-6,
        "top_k": k,
        "fusion_w_trade": float(pm.get("fusion_w_trade") or 0.5),
        "fusion_w_nowcast": float(pm.get("fusion_w_nowcast") or 0.5),
        "y_on_alpha": clamp_y_on_alpha(pm.get("y_on_alpha")),
        "lot_base": LOT_BASE,
        "lot_strong": LOT_STRONG,
    }


def y_fuse_of(item: Optional[dict], cfg: Optional[dict] = None) -> Optional[float]:
    from core.paper.rebalance.path_matrix import (
        fuse_trade_nowcast,
        get_path_matrix_cfg,
        scores_from_rebalance_item,
    )

    if not isinstance(item, dict):
        return None
    direct = _f(item.get("y_fuse") or item.get("y_fusion"))
    if direct is not None:
        return direct
    sc = scores_from_rebalance_item(item)
    rules = get_path_matrix_cfg(cfg)
    return fuse_trade_nowcast(
        sc.get("y_trade"),
        sc.get("y_nowcast"),
        w_trade=float(rules.get("fusion_w_trade") or 0.5),
        w_nowcast=float(rules.get("fusion_w_nowcast") or 0.5),
    )


def _heads(item: Optional[dict]) -> Tuple[Optional[float], Optional[float]]:
    from core.paper.rebalance.path_matrix import scores_from_rebalance_item

    if not isinstance(item, dict):
        return None, None
    sc = scores_from_rebalance_item(item)
    return sc.get("y_trade"), sc.get("y_nowcast")


def y_tau_of(item: Optional[dict]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    if item.get("y_tau") is not None:
        return _f(item.get("y_tau"))
    if item.get("predicted_score_tau") is not None:
        return _f(item.get("predicted_score_tau"))
    return _f(item.get("score_rem"))


def _debug_scores(
    item: Optional[dict],
    yf: Optional[float],
    yo: Optional[float],
    rs: Optional[float],
    **extra: Any,
) -> Dict[str, Any]:
    yt, yn = _heads(item)
    ytau = y_tau_of(item)
    out: Dict[str, Any] = {
        "y_fuse": yf,
        "y_on": yo,
        "ranking_score": rs,
        "y_trade": yt,
        "y_nowcast": yn,
        "y_tau": ytau,
        "predicted_score_tau": ytau,
    }
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def y_on_of(item: Optional[dict]) -> Optional[float]:
    from core.paper.rebalance.path_matrix import scores_from_rebalance_item

    if not isinstance(item, dict):
        return None
    sc = scores_from_rebalance_item(item)
    yo = sc.get("y_on")
    if yo is not None:
        return yo
    return _f(item.get("y_on") or item.get("predicted_score_on"))


def _is_oos_failed(item: Optional[dict]) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("oos_failed") or item.get("oos_blocked") or item.get("oos_sleeve"):
        return True
    try:
        from core.signal.rebalance_tracks import OOS_FAIL, resolve_oos_status

        return resolve_oos_status(item) == OOS_FAIL
    except Exception:  # noqa: BLE001
        logger.debug("resolve_oos_status failed", exc_info=True)
        src = str(item.get("return_model_source") or "")
        return src.startswith("oos_failed")


def plan_rank_lot_day(
    *,
    scored: Sequence[dict],
    holdings: Sequence[dict],
    cash: float,
    prices: Dict[str, float],
    cfg: dict,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """一天的卖/买计划。价格用于估算买入手数是否破现金地板；成交仍由调用方落地。"""
    from core.paper.tplus1 import clip_sell_shares

    rank_enter = coerce_rank_threshold(
        cfg.get("rank_enter"), DEFAULT_RANK_ENTER
    )
    rank_strong = coerce_rank_threshold(
        cfg.get("rank_strong"), DEFAULT_RANK_STRONG
    )
    cash_floor = float(cfg.get("cash_floor") or 0.0)
    top_k = max(1, int(cfg.get("top_k") or 15))
    lot_base, lot_strong_sh = _lot_sizes_from_cfg(cfg)
    y_on_alpha = clamp_y_on_alpha(cfg.get("y_on_alpha"))

    item_by: Dict[str, dict] = {}
    for it in scored or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if code:
            item_by[code] = it

    held_rows: List[dict] = []
    for h in holdings or []:
        if not isinstance(h, dict):
            continue
        code = str(h.get("stock_code") or "").strip()
        sh = _f(h.get("shares")) or 0.0
        if not code or sh <= 0:
            continue
        held_rows.append(h)
        if code not in item_by:
            item_by[code] = {"stock_code": code}

    sells: List[dict] = []
    skips: List[dict] = []
    holds: List[dict] = []
    cash_sim = float(cash or 0.0)

    for h in held_rows:
        code = str(h.get("stock_code") or "").strip()
        item = item_by.get(code) or {}
        yf = y_fuse_of(item, cfg)
        yo = y_on_of(item)
        rs = ranking_score(yf, yo, y_on_alpha=y_on_alpha)
        name = str(item.get("stock_name") or h.get("stock_name") or code)
        held_sh = float(h.get("shares") or 0)
        if item.get("hard_reject") or (rs is not None and float(rs) < 0.0):
            sell_sh, t1 = clip_sell_shares(h, held_sh, as_of=as_of)
            sell_sh = int(float(sell_sh or 0) // 100) * 100
            if sell_sh <= 0:
                skips.append(
                    {
                        "stock_code": code,
                        "stock_name": name,
                        "side": "sell",
                        "action": ACTION_SKIP,
                        "reason": t1.get("reason") or "T+1 不可卖 / 不足一手",
                        **_debug_scores(item, yf, yo, rs, y_on_alpha=y_on_alpha),
                    }
                )
                continue
            px = _f(prices.get(code))
            if px is not None and px > 0:
                cash_sim += float(sell_sh) * float(px)
            reason = (
                "hard_reject 清仓"
                if item.get("hard_reject")
                else f"ranking={rs * 100.0:.3f}%<0% 清仓"
            )
            sells.append(
                {
                    "side": "sell",
                    "stock_code": code,
                    "stock_name": name,
                    "shares": float(sell_sh),
                    "action": ACTION_EXIT,
                    "matrix_action": ACTION_EXIT,
                    "reason": reason,
                    **_debug_scores(item, yf, yo, rs, y_on_alpha=y_on_alpha),
                }
            )
        else:
            holds.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "action": ACTION_HOLD,
                    "reason": "ranking≥0% 持有",
                    "y_fuse": yf,
                    "y_on": yo,
                    "ranking_score": rs,
                    "y_on_alpha": y_on_alpha,
                }
            )

    cand: List[Tuple[float, dict, Optional[float], Optional[float]]] = []
    for code, item in item_by.items():
        if _is_oos_failed(item):
            if any(str(h.get("stock_code")) == code for h in held_rows):
                continue
            skips.append(
                {
                    "stock_code": code,
                    "stock_name": item.get("stock_name") or code,
                    "action": ACTION_SKIP,
                    "reason": "OOS 失败组 · 禁止新开/加仓",
                }
            )
            continue
        if item.get("hard_reject"):
            continue
        yf = y_fuse_of(item, cfg)
        yo = y_on_of(item)
        rs = ranking_score(yf, yo, y_on_alpha=y_on_alpha)
        if rs is None or float(rs) <= float(rank_enter):
            continue
        cand.append((float(rs), item, yf, yo))
    cand.sort(key=lambda t: (-t[0], str(t[1].get("stock_code") or "")))
    cand = cand[:top_k]

    held_codes = {
        str(h.get("stock_code") or "").strip()
        for h in held_rows
        if str(h.get("stock_code") or "").strip()
    }
    sold_codes = {str(s.get("stock_code") or "") for s in sells}
    buys: List[dict] = []
    rank_n = len(cand)
    for i, (rs, item, yf, yo) in enumerate(cand, start=1):
        code = str(item.get("stock_code") or "").strip()
        if not code:
            continue
        dbg = _debug_scores(item, yf, yo, rs, rank_i=i, rank_n=rank_n, y_on_alpha=y_on_alpha)
        px = _f(prices.get(code))
        if px is None or px <= 0:
            skips.append(
                {
                    "stock_code": code,
                    "stock_name": item.get("stock_name") or code,
                    "side": "buy",
                    "action": ACTION_SKIP,
                    "reason": "无有效报价",
                    **dbg,
                }
            )
            continue
        lots = lot_shares_for_rank(
            rs, rank_strong, lot_base=lot_base, lot_strong=lot_strong_sh
        )
        lot_kind = "strong" if lots == lot_strong_sh else "base"
        dbg["lot_kind"] = lot_kind
        need = float(lots) * float(px)
        if cash_sim - need < cash_floor - 1e-6:
            skips.append(
                {
                    "stock_code": code,
                    "stock_name": item.get("stock_name") or code,
                    "side": "buy",
                    "action": ACTION_SKIP,
                    "reason": f"现金将低于地板 {cash_floor:.0f}",
                    **dbg,
                }
            )
            continue
        is_add = code in held_codes and code not in sold_codes
        act = ACTION_ADD if is_add else ACTION_OPEN
        cash_sim -= need
        buys.append(
            {
                "side": "buy",
                "stock_code": code,
                "stock_name": item.get("stock_name") or code,
                "shares": float(lots),
                "action": act,
                "matrix_action": act,
                "reason": (
                    f"ranking={rs * 100.0:.3f}%>{rank_strong * 100.0:g}% 买{lots}股"
                    if lots == lot_strong_sh
                    else f"ranking={rs * 100.0:.3f}%>入场{rank_enter * 100.0:g}% 买{lots}股"
                ),
                **dbg,
            }
        )

    return {
        "sells": sells,
        "buys": buys,
        "holds": holds,
        "skips": skips,
        "cash_after_plan": round(cash_sim, 2),
        "rank_enter": rank_enter,
        "rank_strong": rank_strong,
        "cash_floor": cash_floor,
        "y_on_alpha": y_on_alpha,
        "top_k": top_k,
    }


__all__ = [
    "ACTION_ADD",
    "ACTION_EXIT",
    "ACTION_HOLD",
    "ACTION_OPEN",
    "ACTION_SKIP",
    "DEFAULT_CASH_FLOOR",
    "DEFAULT_INITIAL_CASH",
    "DEFAULT_RANK_ENTER",
    "DEFAULT_RANK_STRONG",
    "DEFAULT_Y_ON_ALPHA",
    "LOT_BASE",
    "Y_ON_ALPHA_MAX",
    "clamp_fusion_weight",
    "clamp_y_on_alpha",
    "LOT_STRONG",
    "account_ref_equity",
    "coerce_rank_threshold",
    "get_rank_lot_cfg",
    "scale_cash_floor_to_account",
    "lot_shares_for_rank",
    "plan_rank_lot_day",
    "ranking_score",
    "y_fuse_of",
    "y_on_of",
    "y_tau_of",
]
