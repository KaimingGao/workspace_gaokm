"""策略调仓：9:30 用 rank=w_oo·ŷ_oo + w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1) 排序，按 200/500 股下单。

规则（live 与历史回测共用）：
  - 持有周期 = T 开盘 → T+1 开盘
  - ŷ_oo = open[T]→open[T+1]（现网 predicted_score，第二步换标签）
  - ŷ_oc = open[T]→close[T]（现网 y_tau）
  - ranking = w_oo·ŷ_oo + w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1)；ŷ 为净收益，落盘为百分点
    w_co 默认 0（不叠隔夜）；缺 ŷ_co 则退回 ŷ_oc
  - 已持仓且 ranking < 0 → 清仓（T+1 可卖部分）
  - ranking > rank入场 的票按分数取 Top-K：建仓或加仓
  - ranking > rank强 → 500 股，否则 200 股
  - 不留现金地板：现金不够该手则跳过（强档买不下先退基础手数）；live 另受持仓市值上限约束
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RANK_ENTER = 0.012
DEFAULT_RANK_STRONG = 0.012
DEFAULT_CASH_FLOOR = 0.0
DEFAULT_HOLDINGS_MV_CAP = 150_000.0
DEFAULT_INITIAL_CASH = 1_000_000.0
DEFAULT_Y_ON_ALPHA = 0.0
Y_ON_ALPHA_MAX = 10.0
LOT_BASE = 200
LOT_STRONG = 500


def _display_name(code: str, *cands: Any) -> str:
    """持仓/打分行里的中文名；代码冒充名走 a_code_name。"""
    c = str(code or "").strip()
    fallback = ""
    for raw in cands:
        s = str(raw or "").strip()
        if s and s != c:
            fallback = s
            break
    try:
        from core.t0.intraday import resolve_stock_name

        nm = str(resolve_stock_name(c, fallback=fallback) or "").strip()
        if nm and nm != c:
            return nm
    except Exception:  # noqa: BLE001
        logger.debug("resolve rank_lots stock name failed %s", c, exc_info=True)
    return fallback or c


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
    y_oo: Optional[float],
    y_oc: Optional[float],
    *,
    w_oo: float = 0.5,
    w_oc: float = 0.5,
    y_on_alpha: float = DEFAULT_Y_ON_ALPHA,
    y_on: Optional[float] = None,
    w_co: Optional[float] = None,
    y_co: Optional[float] = None,
) -> Optional[float]:
    """w_oo·ŷ_oo + w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1)；落盘百分点 → 净收益（÷100）。"""
    from core.signal.yhat_windows import fuse_pct, oc_with_co

    wc = float(w_co) if w_co is not None else float(y_on_alpha or 0.0)
    co = y_co if y_co is not None else y_on
    right = oc_with_co(y_oc, co, wc)
    fused = fuse_pct(y_oo, right, w_left=w_oo, w_right=w_oc)
    if fused is None:
        return None
    return float(fused) / 100.0


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
    """旧现金地板超过账户规模时，按净值 20% 保留现金。

    产品默认地板已为 0；本函数只给显式传入的旧配置做缩放。
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
    # 产品不再留现金地板（含 paper.json 里旧 50 万）。买到现金不够为止。
    floor_cfg = 0.0
    floor = 0.0
    try:
        mv_cap = float(pm.get("holdings_mv_cap", DEFAULT_HOLDINGS_MV_CAP))
    except (TypeError, ValueError):
        mv_cap = DEFAULT_HOLDINGS_MV_CAP
    if mv_cap != mv_cap:
        mv_cap = DEFAULT_HOLDINGS_MV_CAP
    mv_cap = max(0.0, min(mv_cap, 1.0e8))
    try:
        from core.watching.store import WATCHING_MAX_SIZE

        pool_cap = int(WATCHING_MAX_SIZE)
    except Exception:  # noqa: BLE001
        logger.debug("WATCHING_MAX_SIZE import failed", exc_info=True)
        pool_cap = 200
    if top_k is not None:
        k = max(1, min(int(top_k), pool_cap))
    else:
        k = pool_cap
    return {
        "rank_enter": enter,
        "rank_strong": strong,
        "cash_floor": floor,
        "cash_floor_configured": floor_cfg,
        "cash_floor_scaled": abs(float(floor) - float(floor_cfg)) > 1e-6,
        "holdings_mv_cap": mv_cap,
        "top_k": k,
        "fusion_w_oo": float(pm.get("fusion_w_oo") or 0.5),
        "fusion_w_oc": float(pm.get("fusion_w_oc") or 0.5),
        "fusion_w_pc": float(pm.get("fusion_w_pc") or 0.5),
        "fusion_w_trade": float(pm.get("fusion_w_oo") or pm.get("fusion_w_trade") or 0.5),
        "fusion_w_nowcast": float(pm.get("fusion_w_oc") or pm.get("fusion_w_nowcast") or 0.5),
        "fusion_w_co": float(pm.get("fusion_w_co") if pm.get("fusion_w_co") is not None else (pm.get("y_on_alpha") or 0.0)),
        "y_on_alpha": float(pm.get("fusion_w_co") if pm.get("fusion_w_co") is not None else (pm.get("y_on_alpha") or 0.0)),
        "lot_base": LOT_BASE,
        "lot_strong": LOT_STRONG,
    }


def ranking_pct_of(item: Optional[dict], cfg: Optional[dict] = None) -> Optional[float]:
    """调仓 ranking 百分点 = w_oo·ŷ_oo + w_oc·(ŷ_oc ∘ w_co·ŷ_co)。

    有 ŷ_oo/ŷ_oc 一律现算，不信落盘 ``ranking`` / ``y_fuse``（旧戳可能是 ŷ_trade）。
    两头都抽不出时才回退显式 ranking，再回退 y_fuse。
    """
    from core.signal.yhat_windows import fusion_w_co_from_cfg, fusion_weights_from_cfg, ranking_pct

    if not isinstance(item, dict):
        return None
    w_oo, w_oc = fusion_weights_from_cfg(cfg)
    w_co = fusion_w_co_from_cfg(cfg)
    fused = ranking_pct(item, w_oo=w_oo, w_oc=w_oc, w_co=w_co)
    if fused is not None:
        return fused
    direct = _f(item.get("ranking") or item.get("ranking_pct"))
    if direct is not None:
        return direct
    return _f(item.get("y_fuse"))


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
    ranking_pct: Optional[float],
    y_oc: Optional[float],
    rs: Optional[float],
    **extra: Any,
) -> Dict[str, Any]:
    from core.paper.rebalance.path_matrix import scores_from_rebalance_item
    from core.signal.yhat_windows import pick_y_co, residual_pct

    sc = scores_from_rebalance_item(item)
    y_oo = sc.get("y_oo")
    oc = y_oc if y_oc is not None else sc.get("y_oc")
    y_co = sc.get("y_co")
    if y_co is None:
        y_co = pick_y_co(item)
    if y_co is None:
        y_co = y_on_of(item)
    ytau = y_tau_of(item)
    y_τc = sc.get("y_τc")
    ranking = ranking_pct if ranking_pct is not None else sc.get("ranking")
    residual = sc.get("residual")
    if residual is None:
        residual = residual_pct(item)
    out: Dict[str, Any] = {
        "y_oo": y_oo,
        "y_oc": oc,
        "y_co": y_co,
        "y_on": y_co,
        "y_τc": y_τc,
        "ranking": ranking,
        "y_fuse": ranking,
        "ranking_score": rs,
        "residual": residual,
        "y_tau": ytau,
        "predicted_score_tau": ytau,
        "predicted_score": y_oo,
        "predicted_score_eod": y_oo,
        "predicted_score_on": y_co,
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
    """一天的卖/买计划。价格用于估算现金是否够买该手；成交仍由调用方落地。"""
    from core.paper.tplus1 import clip_sell_shares

    rank_enter = coerce_rank_threshold(
        cfg.get("rank_enter"), DEFAULT_RANK_ENTER
    )
    rank_strong = coerce_rank_threshold(
        cfg.get("rank_strong"), DEFAULT_RANK_STRONG
    )
    cash_floor = float(cfg.get("cash_floor") or 0.0)
    try:
        mv_cap = float(cfg.get("holdings_mv_cap") or 0.0)
    except (TypeError, ValueError):
        mv_cap = 0.0
    if mv_cap != mv_cap:
        mv_cap = 0.0
    mv_cap = max(0.0, mv_cap)
    top_k = max(1, int(cfg.get("top_k") or 15))
    lot_base, lot_strong_sh = _lot_sizes_from_cfg(cfg)
    from core.signal.yhat_windows import fusion_w_co_from_cfg, fusion_weights_from_cfg, pick_y_oc

    w_oo, w_oc = fusion_weights_from_cfg(cfg)
    w_co = fusion_w_co_from_cfg(cfg)
    t0_blocks = cfg.get("t0_sell_blocks")
    if not isinstance(t0_blocks, dict):
        try:
            from core.t0.intraday import load_rebalance_t0_sell_blocks

            t0_blocks = load_rebalance_t0_sell_blocks(session_date=as_of)
        except Exception:  # noqa: BLE001
            logger.debug("load_rebalance_t0_sell_blocks failed", exc_info=True)
            t0_blocks = {}

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
        rp = ranking_pct_of(item, cfg)
        yoc = pick_y_oc(item)
        rs = None if rp is None else float(rp) / 100.0
        name = _display_name(code, item.get("stock_name"), h.get("stock_name"))
        held_sh = float(h.get("shares") or 0)
        if item.get("hard_reject") or (rs is not None and float(rs) < 0.0):
            t0_reason = str(t0_blocks.get(code) or "").strip()
            if t0_reason:
                skips.append(
                    {
                        "stock_code": code,
                        "stock_name": name,
                        "side": "sell",
                        "action": ACTION_SKIP,
                        "reason": t0_reason,
                        **_debug_scores(item, rp, yoc, rs),
                    }
                )
                holds.append(
                    {
                        "stock_code": code,
                        "stock_name": name,
                        "shares": held_sh,
                        "action": ACTION_HOLD,
                        "reason": t0_reason,
                        **_debug_scores(item, rp, yoc, rs),
                    }
                )
                continue
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
                        **_debug_scores(item, rp, yoc, rs),
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
                    **_debug_scores(item, rp, yoc, rs),
                }
            )
        else:
            holds.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "shares": held_sh,
                    "action": ACTION_HOLD,
                    "reason": "ranking≥0% 持有",
                    **_debug_scores(item, rp, yoc, rs),
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
                    "stock_name": _display_name(code, item.get("stock_name")),
                    "action": ACTION_SKIP,
                    "reason": "OOS 失败组 · 禁止新开/加仓",
                }
            )
            continue
        if item.get("hard_reject"):
            continue
        rp = ranking_pct_of(item, cfg)
        yoc = pick_y_oc(item)
        rs = None if rp is None else float(rp) / 100.0
        if rs is None or float(rs) <= float(rank_enter):
            continue
        cand.append((float(rs), item, rp, yoc))
    cand.sort(key=lambda t: (-t[0], str(t[1].get("stock_code") or "")))
    cand = cand[:top_k]

    held_codes = {
        str(h.get("stock_code") or "").strip()
        for h in held_rows
        if str(h.get("stock_code") or "").strip()
    }
    sold_codes = {str(s.get("stock_code") or "") for s in sells}
    remain_sh: Dict[str, float] = {}
    for h in held_rows:
        code = str(h.get("stock_code") or "").strip()
        remain_sh[code] = float(h.get("shares") or 0)
    for s in sells:
        code = str(s.get("stock_code") or "").strip()
        if code:
            remain_sh[code] = max(0.0, float(remain_sh.get(code) or 0) - float(s.get("shares") or 0))
    mv_sim = 0.0
    for code, sh in remain_sh.items():
        px = _f(prices.get(code))
        if px is not None and px > 0 and sh > 0:
            mv_sim += float(sh) * float(px)
    buys: List[dict] = []
    rank_n = len(cand)
    for i, (rs, item, rp, yoc) in enumerate(cand, start=1):
        code = str(item.get("stock_code") or "").strip()
        if not code:
            continue
        dbg = _debug_scores(item, rp, yoc, rs, rank_i=i, rank_n=rank_n)
        px = _f(prices.get(code))
        if px is None or px <= 0:
            skips.append(
                {
                    "stock_code": code,
                    "stock_name": _display_name(code, item.get("stock_name")),
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
        need = float(lots) * float(px)
        if cash_sim - need < cash_floor - 1e-6 and lots > lot_base:
            lots = lot_base
            lot_kind = "base"
            need = float(lots) * float(px)
        dbg["lot_kind"] = lot_kind
        if cash_sim - need < cash_floor - 1e-6:
            skip_reason = (
                f"现金将低于地板 {cash_floor:.0f}"
                if cash_floor > 0
                else "现金不足"
            )
            skips.append(
                {
                    "stock_code": code,
                    "stock_name": _display_name(code, item.get("stock_name")),
                    "side": "buy",
                    "shares": float(lots),
                    "price": float(px),
                    "amount": round(need, 2),
                    "action": ACTION_SKIP,
                    "reason": skip_reason,
                    **dbg,
                }
            )
            continue
        if mv_cap > 0 and mv_sim + need > mv_cap + 1e-6:
            skips.append(
                {
                    "stock_code": code,
                    "stock_name": _display_name(code, item.get("stock_name")),
                    "side": "buy",
                    "action": ACTION_SKIP,
                    "reason": f"持仓市值将超过上限 {mv_cap:.0f}",
                    **dbg,
                }
            )
            continue
        is_add = code in held_codes and code not in sold_codes
        act = ACTION_ADD if is_add else ACTION_OPEN
        cash_sim -= need
        mv_sim += need
        remain_sh[code] = float(remain_sh.get(code) or 0) + float(lots)
        buys.append(
            {
                "side": "buy",
                "stock_code": code,
                "stock_name": _display_name(code, item.get("stock_name")),
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
        "holdings_mv_cap": mv_cap,
        "holdings_mv_after_plan": round(mv_sim, 2),
        "fusion_w_oo": w_oo,
        "fusion_w_oc": w_oc,
        "fusion_w_co": w_co,
        "top_k": top_k,
    }


__all__ = [
    "ACTION_ADD",
    "ACTION_EXIT",
    "ACTION_HOLD",
    "ACTION_OPEN",
    "ACTION_SKIP",
    "DEFAULT_CASH_FLOOR",
    "DEFAULT_HOLDINGS_MV_CAP",
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
    "ranking_pct_of",
    "ranking_score",
    "y_on_of",
    "y_tau_of",
]
