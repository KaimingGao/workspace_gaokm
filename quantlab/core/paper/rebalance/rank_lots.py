"""策略调仓：fill_clock～10:00 用 τc ranking 排序，按已保存金额换算股数下单。

规则（live 与历史回测共用）：
  - 持有周期 = T 开盘 → T+1 开盘
  - ŷ_oo = open[T]→open[T+1]（现网 predicted_score）
  - ŷ_τc = close[T]/price(τ)−1。τ=open 时 price(τ)=open，等于 close/open−1。时钟对齐后是 y_tau
  - ranking = w_oo·((ŷ_oo+1)/(1+rot)−1) + w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1)
    rot = price(τ)/open[T]−1；基准 τ→open[T+1]
    w_co 默认 1（叠隔夜）；缺 ŷ_co 则退回 ŷ_τc
  - 过入场（ranking>入场净收益）→ 开仓或加仓；y_oo>0 / y_τc>0 看原始百分点符号
  - 已持仓且未过入场、缺 ranking、或 hard_reject → 清仓（T+1 可卖部分）
    缺分不能假装过门槛续持；无「持」动作
  - ranking > rank强 → lot_strong_amount，否则 lot_base_amount（缺省 2 万 / 1 万）
    股数 = 金额/价 向下取整到一手；不够一手则买一手
  - 不留现金地板：现金不够该手则缩到整百（最少一手）；仍买不起才跳过。
    强档买不下先试入场金额，再缩。live 另受持仓市值上限约束
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RANK_ENTER = 0.001
DEFAULT_RANK_STRONG = 0.001
DEFAULT_CASH_FLOOR = 0.0
DEFAULT_HOLDINGS_MV_CAP = 150_000.0
DEFAULT_INITIAL_CASH = 1_000_000.0
DEFAULT_FUSION_W_CO = 1.0
FUSION_W_CO_MAX = 10.0
LOT_AMOUNT_BASE = 10_000.0
LOT_AMOUNT_STRONG = 20_000.0
LOT_AMOUNT_MIN = 1_000.0
LOT_AMOUNT_MAX = 1_000_000.0
LOT_MIN = 100


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
ACTION_REDUCE = "reduce"
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


def clamp_fusion_w_co(raw: Any, default: float = DEFAULT_FUSION_W_CO) -> float:
    v = _f(raw)
    if v is None:
        return float(default)
    return max(0.0, min(float(v), FUSION_W_CO_MAX))


def clamp_fusion_weight(raw: Any, default: float = 0.5) -> float:
    v = _f(raw)
    if v is None:
        return max(0.0, min(1.0, float(default)))
    return max(0.0, min(1.0, float(v)))


def _pos_enter_skip(val: Optional[float], enabled: bool, label: str) -> Optional[str]:
    """勾选则须 y>0；关=不看；缺分不拦。"""
    if not enabled:
        return None
    if val is None:
        return None
    if float(val) <= 0:
        return f"{label}={float(val):.3f}≤0 未过入场"
    return None


def _y_gt0_flags(cfg: Optional[dict]) -> Tuple[bool, bool]:
    """y_oo>0 / y_τc>0 闸。缺键为关。"""
    from core.t0.config import coerce_cfg_bool

    d = cfg if isinstance(cfg, dict) else {}
    return (
        coerce_cfg_bool(d.get("y_oo_gt0"), False),
        coerce_cfg_bool(d.get("y_τc_gt0"), False),
    )


def _rank_enter_skip(rs: Optional[float], thresh: float) -> Optional[str]:
    """ranking 入场：0=仍须 ranking>0；缺失则拦。rs 为净收益。"""
    th = float(thresh or 0.0)
    if rs is None:
        return "ranking 缺失"
    if th <= 0:
        if float(rs) <= 0:
            return "ranking≤0 未过入场"
        return None
    if float(rs) <= th:
        return f"ranking={float(rs) * 100.0:.3f}%<{th * 100.0:g}% 未过入场"
    return None


def _enter_profile_skip_reason(
    *,
    y_oo: Optional[float],
    y_τc: Optional[float],
    rs: Optional[float],
    rank_enter: float,
    y_oo_gt0: bool,
    y_τc_gt0: bool,
) -> Optional[str]:
    for skip in (
        _rank_enter_skip(rs, rank_enter),
        _pos_enter_skip(y_oo, y_oo_gt0, "y_oo"),
        _pos_enter_skip(y_τc, y_τc_gt0, "y_τc"),
    ):
        if skip is not None:
            return skip
    return None


def held_exit_reason(item: Optional[dict], rs: Optional[float]) -> Optional[str]:
    """已持仓硬清仓理由；None = 再看入场闸（未过则清仓）。

    入场缺 ranking 会拦；持仓缺 ranking 不能假装过门槛续持。
    """
    if isinstance(item, dict) and item.get("hard_reject"):
        why = str(item.get("reject_reason") or "").strip()
        return why if why else "hard_reject 清仓"
    if rs is None:
        return "ranking 缺失 清仓"
    if float(rs) < 0.0:
        return f"ranking={float(rs) * 100.0:.3f}%<0% 清仓"
    return None


def oo_rank_max_from_cfg(cfg: Optional[dict]) -> Optional[int]:
    """oo_rank 名次上限：须 y_oo_rank < 该值才过入场；None/≤0=关。"""
    d = cfg if isinstance(cfg, dict) else {}
    raw = d.get("oo_rank_max")
    if raw in (None, ""):
        return None
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return max(1, min(v, 10_000))


def _oo_rank_max_skip(item: Optional[dict], cfg: Optional[dict]) -> Optional[str]:
    """ŷ_oo_rank 截面名次闸：开则须 rank < oo_rank_max。缺分不拦。"""
    max_r = oo_rank_max_from_cfg(cfg)
    if max_r is None:
        return None
    rank = None
    if isinstance(item, dict):
        for key in ("y_oo_rank", "y_oo_rank_hat", "predicted_score_oo_rank"):
            raw = item.get(key)
            if raw in (None, ""):
                continue
            try:
                rank = float(raw)
                break
            except (TypeError, ValueError):
                continue
    if rank is None:
        return None
    if float(rank) >= float(max_r):
        return f"oo_rank={int(rank)}≥{int(max_r)} 未过入场"
    return None


def rank_lot_enter_skip_reason(
    item: Optional[dict],
    cfg: Optional[dict],
    *,
    rs: Optional[float],
) -> Optional[str]:
    """入场：启用中的门槛1 ∪ 门槛2。每档 ranking 过入场；可选 y_oo>0 / y_τc>0。

    ``y_enter_enabled`` / ``y_enter_alt_enabled`` 关则该档不参与 OR；两档都关则不开/不加。
    ``y_oo_gt0`` / ``y_τc_gt0`` 开则对应 ŷ 须 >0。缺键时门槛2 跟随门槛1。缺分不拦。
    过 OR 后可选 ``oo_rank_max``：须当日截面名次 y_oo_rank < 上限（缺分不拦）。
    """
    from core.paper.rebalance.path_matrix import scores_from_rebalance_item
    from core.signal.yhat_windows import pick_y_oo, pick_y_τc
    from core.t0.config import coerce_cfg_bool

    cfg_d = cfg if isinstance(cfg, dict) else {}
    sc = scores_from_rebalance_item(item, cfg_d)
    y_oo = pick_y_oo(sc) if isinstance(sc, dict) else None
    y_τc = pick_y_τc(sc) if isinstance(sc, dict) else None
    if y_oo is None and isinstance(item, dict):
        y_oo = pick_y_oo(item)
    if y_τc is None and isinstance(item, dict):
        y_τc = pick_y_τc(item)

    rank_enter = coerce_rank_threshold(cfg_d.get("rank_enter"), DEFAULT_RANK_ENTER)
    rank_enter = max(0.0, min(float(rank_enter), 10.0))

    gate1_on = coerce_cfg_bool(cfg_d.get("y_enter_enabled"), True)
    gate2_on = coerce_cfg_bool(cfg_d.get("y_enter_alt_enabled"), True)
    y_oo_gt0, y_τc_gt0 = _y_gt0_flags(cfg_d)

    def _pass_or_oo_rank() -> Optional[str]:
        return _oo_rank_max_skip(item, cfg_d)

    skip = None
    if gate1_on:
        skip = _enter_profile_skip_reason(
            y_oo=y_oo,
            y_τc=y_τc,
            rs=rs,
            rank_enter=rank_enter,
            y_oo_gt0=y_oo_gt0,
            y_τc_gt0=y_τc_gt0,
        )
        if skip is None:
            return _pass_or_oo_rank()

    skip_alt = None
    if gate2_on:
        skip_alt = _enter_profile_skip_reason(
            y_oo=y_oo,
            y_τc=y_τc,
            rs=rs,
            rank_enter=max(
                0.0,
                min(
                    coerce_rank_threshold(
                        cfg_d.get("rank_enter_alt")
                        if cfg_d.get("rank_enter_alt") not in (None, "")
                        else rank_enter,
                        rank_enter,
                    ),
                    10.0,
                ),
            ),
            y_oo_gt0=y_oo_gt0,
            y_τc_gt0=y_τc_gt0,
        )
        if skip_alt is None:
            return _pass_or_oo_rank()

    if not gate1_on and not gate2_on:
        return "门槛1/2 均未启用"
    if gate1_on and gate2_on:
        if skip_alt == skip:
            return skip
        return f"门槛1 {skip}；门槛2 {skip_alt}"
    if gate1_on:
        return skip
    return skip_alt


def ranking_score(
    y_oo: Optional[float],
    y_τc: Optional[float],
    *,
    w_oo: float = 0.5,
    w_τc: float = 0.5,
    w_co: float = 0.0,
    y_co: Optional[float] = None,
) -> Optional[float]:
    """开盘基准融合：右侧是 ŷ_τc，百分点 → 净收益（÷100）。决策 ranking 走 ranking_pct。"""
    from core.signal.yhat_windows import fuse_pct, oc_with_co

    wc = float(w_co or 0.0)
    co = y_co
    right = oc_with_co(y_τc, co, wc)
    fused = fuse_pct(y_oo, right, w_left=w_oo, w_right=w_τc)
    if fused is None:
        return None
    return float(fused) / 100.0


def _normalize_amount(n: Any, default: float) -> float:
    try:
        v = float(n)
    except (TypeError, ValueError):
        v = float(default)
    if v != v:
        v = float(default)
    v = max(float(LOT_AMOUNT_MIN), min(v, float(LOT_AMOUNT_MAX)))
    return float(int(round(v / 100.0)) * 100)


def lot_shares_for_rank(
    score: Optional[float],
    rank_strong: float,
    *,
    amount_base: float = LOT_AMOUNT_BASE,
    amount_strong: float = LOT_AMOUNT_STRONG,
    price: float = 0.0,
    lot: int = LOT_MIN,
) -> int:
    if score is None:
        return 0
    from core.paper.sizing import shares_from_amount

    base_a = _normalize_amount(amount_base, LOT_AMOUNT_BASE)
    strong_a = max(base_a, _normalize_amount(amount_strong, LOT_AMOUNT_STRONG))
    amt = strong_a if float(score) > float(rank_strong) else base_a
    return shares_from_amount(amt, price, lot)


def clip_lot_to_cash(
    lots: int,
    px: float,
    cash: float,
    cash_floor: float = 0.0,
    *,
    lot_min: int = LOT_MIN,
) -> int:
    """现金够则原手数；不够则缩到整百，最少一手。买不起一手返回 0。"""
    try:
        sh = int(lots or 0)
    except (TypeError, ValueError):
        sh = 0
    sh = (sh // 100) * 100
    try:
        price = float(px)
    except (TypeError, ValueError):
        price = 0.0
    if sh < int(lot_min) or price <= 0 or price != price:
        return 0
    try:
        floor = float(cash_floor or 0.0)
    except (TypeError, ValueError):
        floor = 0.0
    if floor != floor:
        floor = 0.0
    floor = max(0.0, floor)
    try:
        cash_f = float(cash or 0.0)
    except (TypeError, ValueError):
        cash_f = 0.0
    if cash_f != cash_f:
        cash_f = 0.0
    need = float(sh) * price
    if cash_f - need >= floor - 1e-6:
        return sh
    budget = cash_f - floor
    if budget <= 0:
        return 0
    max_sh = int(budget / price // 100) * 100
    if max_sh < int(lot_min):
        return 0
    return min(sh, max_sh)


def _lot_amounts_from_cfg(cfg: Optional[dict]) -> Tuple[float, float]:
    base = LOT_AMOUNT_BASE
    strong = LOT_AMOUNT_STRONG
    if isinstance(cfg, dict):
        if cfg.get("lot_base_amount") is not None:
            base = cfg.get("lot_base_amount")
        if cfg.get("lot_strong_amount") is not None:
            strong = cfg.get("lot_strong_amount")
    base_n = _normalize_amount(base, LOT_AMOUNT_BASE)
    strong_n = max(base_n, _normalize_amount(strong, LOT_AMOUNT_STRONG))
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
    y_oo_gt0, y_τc_gt0 = _y_gt0_flags(pm)
    # 产品不再留现金地板（含 paper.json 里旧 50 万）。买到现金不够为止。
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
    lot_base_amount, lot_strong_amount = _lot_amounts_from_cfg(pm)
    return {
        "rank_enter": enter,
        "rank_strong": strong,
        "rank_enter_alt": coerce_rank_threshold(
            pm.get("rank_enter_alt", enter), enter
        ),
        "cash_floor": floor,
        "cash_floor_configured": 0.0,
        "cash_floor_scaled": False,
        "holdings_mv_cap": mv_cap,
        "top_k": k,
        "fusion_w_oo": float(pm.get("fusion_w_oo") or 0.6),
        "fusion_w_oc": float(pm.get("fusion_w_oc") or 0.4),
        "fusion_w_pc": float(pm.get("fusion_w_pc") or 0.5),
        "fusion_w_co": float(pm.get("fusion_w_co") if pm.get("fusion_w_co") is not None else DEFAULT_FUSION_W_CO),
        "lot_base_amount": lot_base_amount,
        "lot_strong_amount": lot_strong_amount,
        "y_enter_enabled": bool(pm.get("y_enter_enabled", True)),
        "y_enter_alt_enabled": bool(pm.get("y_enter_alt_enabled", True)),
        "y_oo_gt0": bool(y_oo_gt0),
        "y_τc_gt0": bool(y_τc_gt0),
        "oo_rank_max": oo_rank_max_from_cfg(pm),
        "fill_clock": str(pm.get("fill_clock") or "09:30"),
        "live_fill_clock": str(pm.get("live_fill_clock") or "09:30"),
        "score_backend": str(pm.get("score_backend") or "ridge"),
    }


def _align_open_tc_to_fill(
    item: dict,
    *,
    rot: Optional[float],
) -> dict:
    """盘中成交但 ŷ_τc 仍是开盘 Z：把 open 标签几何映到 τ→close，并写入 rot。

    前缀已重算（``prefix_causal`` / 带前缀特征的 fallback）则不动。
    幂等：始终从 ``_y_τc_before_fill_align`` 映到当前 rot，避免 ranking_pct_of
    被持仓明细等路径二次调用时反复 remaining。
    """
    from core.signal.yhat_geom import remaining_at_tau
    from core.signal.yhat_windows import pick_y_τc, write_y_τc

    if not isinstance(item, dict):
        return item
    src = str(item.get("_score_source") or "")
    feats0 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    if src == "prefix_causal" or (
        src == "prefix_open_fallback"
        and feats0.get("ret_open_to_tau") is not None
    ):
        return item
    if rot is None or abs(float(rot)) < 1e-9:
        return item
    raw = item.get("_y_τc_before_fill_align")
    if raw is None:
        raw = pick_y_τc(item)
        if raw is None:
            return item
        item["_y_τc_before_fill_align"] = float(raw)
    else:
        try:
            raw = float(raw)
        except (TypeError, ValueError):
            return item
    prev_rot = item.get("_fill_rot_aligned")
    try:
        if prev_rot is not None and abs(float(prev_rot) - float(rot)) < 1e-9:
            return item
    except (TypeError, ValueError):
        pass
    y_rem = remaining_at_tau(raw, rot)
    if y_rem is None:
        return item
    write_y_τc(item, y_rem)
    feats = dict(feats0) if feats0 else {}
    feats["ret_open_to_tau"] = float(rot)
    item["features_tau"] = feats
    item["ret_open_to_tau"] = float(rot)
    item["_fill_rot_aligned"] = float(rot)
    return item


def ranking_pct_of(
    item: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
) -> Optional[float]:
    """调仓 ranking 百分点。

    τc（行上有 y_spec）：用成交价 rot 把 ŷ_oo 映到 τ→open[T+1]，ŷ_τc 已是 τ→close。
    缺 y_spec 的旧行：开盘基准融合再减 (price(τ)/open−1)。

    有 ŷ_oo/ŷ_oc 一律现算，不信落盘 ``ranking`` / ``y_fuse``（旧戳可能是 ŷ_trade）。
    两头都抽不出时才回退显式 ranking，再回退 y_fuse。
    """
    from core.signal.yhat_windows import (
        fusion_w_co_from_cfg,
        fusion_weights_from_cfg,
        ranking_pct,
        remaining_ranking_pct,
        ret_open_to_tau_pct,
        tau_model_is_open_to_close,
    )

    if not isinstance(item, dict):
        return None
    w_oo, w_τc = fusion_weights_from_cfg(cfg)
    w_co = fusion_w_co_from_cfg(cfg)
    is_oc = tau_model_is_open_to_close(item)
    o = _f(open_px)
    if o is None or o <= 0:
        o = _f(item.get("day_open"))
    p = _f(price_tau)
    if p is None or p <= 0:
        p = _f(item.get("rebalance_px"))
    if p is None or p <= 0:
        p = _f(item.get("price_tau"))
    rot = ret_open_to_tau_pct(o, p)
    work = item
    if not is_oc and rot is not None:
        work = _align_open_tc_to_fill(item, rot=rot)
    fused = ranking_pct(
        work, w_oo=w_oo, w_τc=w_τc, w_co=w_co, ret_open_to_tau=rot
    )
    if fused is None:
        fused = _f(work.get("ranking") or work.get("ranking_pct"))
    if fused is None:
        fused = _f(work.get("y_fuse"))
    if fused is None:
        return None
    return remaining_ranking_pct(fused, open_px=o, price_tau=p, is_oc_model=is_oc)


def y_tau_of(item: Optional[dict]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    for k in ("y_τc", "predicted_score_τc"):
        if item.get(k) is not None:
            return _f(item.get(k))
    return None


# 对照头透传键（不进 ranking）。旧 y_path / y_path_realized 只读，不落新行。
AUX_YHAT_KEYS = (
    "y_τ30",
    "y_t30",
    "predicted_score_t30",
    "y_t30_hat",
    "y_t30_realized",
    "t30_realized",
    "y_τ45",
    "y_t45",
    "predicted_score_t45",
    "y_t45_hat",
    "y_t45_realized",
    "t45_realized",
    "y_τ60",
    "y_t60",
    "predicted_score_t60",
    "y_t60_hat",
    "y_t60_realized",
    "t60_realized",
    "y_τ75",
    "y_t75",
    "predicted_score_t75",
    "y_t75_hat",
    "y_t75_realized",
    "t75_realized",
    "y_τ90",
    "y_t90",
    "predicted_score_t90",
    "y_t90_hat",
    "y_t90_realized",
    "t90_realized",
    "y_τw",
    "y_tw",
    # ŷ_oo_rank：当日截面 1..n 名次；不进 ranking/买序；可选 oo_rank_max 入场闸
    "y_oo_rank",
    "y_oo_rank_hat",
    "predicted_score_oo_rank",
    "y_oo_rank_score",
    "y_oo_rank_n",
    "y_oo_rank_realized",
    "oo_rank_realized",
)


def aux_yhat_fields(
    item: Optional[dict],
    *,
    include_tau_horizons: bool = True,
) -> Dict[str, Any]:
    """对照头透传：可选 y_τw / y_τ30… / y_oo_rank。不进 ranking 分数。ŷ_hl 已下线。"""
    if not isinstance(item, dict):
        return {}
    out: Dict[str, Any] = {}
    if include_tau_horizons:
        try:
            from core.research.horizon_ridge import pick_y_hat, pick_y_label
            from core.t0.close_band import blend_y_tw
        except Exception:  # noqa: BLE001
            logger.debug("aux yhat tau imports failed", exc_info=True)
        else:
            y30 = pick_y_hat("t30", item)
            y45 = pick_y_hat("t45", item)
            y60 = pick_y_hat("t60", item)
            y75 = pick_y_hat("t75", item)
            y90 = pick_y_hat("t90", item)
            if y30 is not None:
                out["y_τ30"] = y30
                out["y_t30"] = y30
                out["predicted_score_t30"] = y30
                out["y_t30_hat"] = y30
            if y45 is not None:
                out["y_τ45"] = y45
                out["y_t45"] = y45
                out["predicted_score_t45"] = y45
                out["y_t45_hat"] = y45
            if y60 is not None:
                out["y_τ60"] = y60
                out["y_t60"] = y60
                out["predicted_score_t60"] = y60
                out["y_t60_hat"] = y60
            if y75 is not None:
                out["y_τ75"] = y75
                out["y_t75"] = y75
                out["predicted_score_t75"] = y75
                out["y_t75_hat"] = y75
            if y90 is not None:
                out["y_τ90"] = y90
                out["y_t90"] = y90
                out["predicted_score_t90"] = y90
                out["y_t90_hat"] = y90
            r30 = pick_y_label("t30", item)
            r45 = pick_y_label("t45", item)
            r60 = pick_y_label("t60", item)
            r75 = pick_y_label("t75", item)
            r90 = pick_y_label("t90", item)
            if r30 is not None:
                out["y_t30_realized"] = r30
                out["t30_realized"] = r30
            if r45 is not None:
                out["y_t45_realized"] = r45
                out["t45_realized"] = r45
            if r60 is not None:
                out["y_t60_realized"] = r60
                out["t60_realized"] = r60
            if r75 is not None:
                out["y_t75_realized"] = r75
                out["t75_realized"] = r75
            if r90 is not None:
                out["y_t90_realized"] = r90
                out["t90_realized"] = r90
            yw = blend_y_tw(y30, y60, y90, y45, y75)
            if yw is None:
                yw = _f(item.get("y_τw"))
            if yw is None:
                yw = _f(item.get("y_tw"))
            if yw is not None:
                out["y_τw"] = yw
                out["y_tw"] = yw

    # ŷ_oo_rank：当日截面 1..n 名次（不进买序；与 τ 档无关，始终透传）
    try:
        from core.research.oo_rank_lambdarank import (
            pick_y_oo_rank_hat,
            pick_y_oo_rank_label,
        )

        y_or = pick_y_oo_rank_hat(item)
        if y_or is not None:
            out["y_oo_rank"] = y_or
            out["y_oo_rank_hat"] = y_or
            out["predicted_score_oo_rank"] = y_or
        score_raw = item.get("y_oo_rank_score")
        if score_raw is not None:
            try:
                out["y_oo_rank_score"] = float(score_raw)
            except (TypeError, ValueError):
                pass
        n_pool = item.get("y_oo_rank_n")
        if n_pool is not None:
            try:
                out["y_oo_rank_n"] = int(n_pool)
            except (TypeError, ValueError):
                pass
        r_or = pick_y_oo_rank_label(item)
        if r_or is not None:
            out["y_oo_rank_realized"] = r_or
            out["oo_rank_realized"] = r_or
    except Exception:  # noqa: BLE001
        logger.debug("aux oo_rank fields failed", exc_info=True)
    return out


# 调仓明细 / 成交 tip：ŷ 组成与窗口说明。不含 ŷ_τ30/60/90（回测会剥）。
_TIP_EXPLAIN_KEYS = (
    "score_formula_terms",
    "formula_terms",
    "formula_terms_tau",
    "score_formula_terms_tau",
    "formula_terms_co",
    "score_formula_terms_co",
    "formula_terms_path",
    "score_formula_terms_path",
    "y_spec_tau",
    "y_spec_co",
    "as_of_tau",
    "score_formula",
    "factor_coefficients",
    "factor_coefficients_tau",
    "fusion_w_oo",
    "fusion_w_oc",
    "fusion_w_co",
    "y_oo_source",
    "y_τc_source",
    "y_co_source",
)


_TIP_FORMULA_KEYS = frozenset(
    {
        "score_formula_terms",
        "formula_terms",
        "formula_terms_tau",
        "score_formula_terms_tau",
        "formula_terms_co",
        "score_formula_terms_co",
        "formula_terms_path",
        "score_formula_terms_path",
    }
)


# y_oo / y_τc / y_co 三列 tip 同一因子条数
_Y_HEAD_FACTOR_N = 24
_Y_HEAD_FORMULA_KEYS = frozenset(
    {
        "score_formula_terms",
        "formula_terms",
        "formula_terms_tau",
        "score_formula_terms_tau",
        "formula_terms_co",
        "score_formula_terms_co",
    }
)


# ŷ_τc tip：开盘 Z + 开盘已定义的分钟键，slim 时不得挤掉
_TAU_TIP_PIN = (
    "gap_pct",
    "theme_day",
    "gap_atr",
    "sector_gap_breadth",
    "gap_vs_sector",
    "yclose_loc",
    "mom3_pct",
    "tau_lag1",
    "tau_ma5",
    "ret_open_to_tau",
    "tau_elapsed_min",
    "loc_hl",
    "ret_last_15m",
    "sector_ret_to_tau",
)


def _slim_tip_formula(
    expl: Any,
    limit: int = 10,
    pin_keys: Optional[Sequence[str]] = None,
) -> Any:
    """压缩组成表，避免 sim_trades / data-score-detail 过长截断。"""
    if not isinstance(expl, dict):
        return expl
    terms = [t for t in (expl.get("terms") or []) if isinstance(t, dict)]
    if len(terms) <= limit:
        return expl
    ranked = sorted(terms, key=lambda t: -abs(float(t.get("contrib") or 0.0)))
    pin = {str(k).strip() for k in (pin_keys or []) if str(k).strip()}
    if pin:
        pin_terms = [t for t in ranked if str(t.get("key") or "") in pin]
        non_pin = [t for t in ranked if str(t.get("key") or "") not in pin]
        budget = max(0, int(limit) - len(pin_terms))
        kept = list(non_pin[:budget]) + pin_terms
        kept.sort(key=lambda t: -abs(float(t.get("contrib") or 0.0)))
    else:
        kept = ranked[: max(1, int(limit))]
    out = dict(expl)
    out["terms"] = kept
    return out


def tip_explain_fields(item: Optional[dict]) -> Dict[str, Any]:
    """成交/跳过/持有行上的 tip 组成；缺键不写。"""
    if not isinstance(item, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in _TIP_EXPLAIN_KEYS:
        v = item.get(k)
        if v is None:
            continue
        if k in ("formula_terms_tau", "score_formula_terms_tau"):
            v = _slim_tip_formula(v, limit=_Y_HEAD_FACTOR_N, pin_keys=_TAU_TIP_PIN)
        elif k in _Y_HEAD_FORMULA_KEYS:
            v = _slim_tip_formula(v, limit=_Y_HEAD_FACTOR_N)
        elif k in _TIP_FORMULA_KEYS:
            v = _slim_tip_formula(v)
        out[k] = v
    return out


def _debug_scores(
    item: Optional[dict],
    ranking_pct: Optional[float],
    rs: Optional[float],
    **extra: Any,
) -> Dict[str, Any]:
    from core.paper.rebalance.path_matrix import scores_from_rebalance_item
    from core.signal.yhat_windows import (
        pick_y_co,
        pick_y_τc,
        residual_pct,
    )

    sc = scores_from_rebalance_item(item)
    y_oo = sc.get("y_oo")
    y_τc = sc.get("y_τc")
    if y_τc is None:
        y_τc = pick_y_τc(item)
    y_co = sc.get("y_co")
    if y_co is None:
        y_co = pick_y_co(item)
    ranking = ranking_pct if ranking_pct is not None else sc.get("ranking")
    residual = sc.get("residual")
    if residual is None:
        residual = residual_pct(item)
    r_hat = _f((item or {}).get("r_hat")) if item else None
    if r_hat is None and item:
        r_hat = _f(item.get("remaining_oc"))
    if r_hat is None:
        r_hat = y_τc
    out: Dict[str, Any] = {
        "y_oo": y_oo,
        "y_co": y_co,
        "y_τc": y_τc,
        "ranking": ranking,
        "ranking_score": rs,
        "residual": residual,
        "r_hat": r_hat,
        "remaining_oc": r_hat,
        "predicted_score": y_oo,
        "predicted_score_eod": y_oo,
    }
    if y_co is not None:
        out["predicted_score_co"] = y_co
    if y_τc is not None:
        out["predicted_score_tau"] = y_τc
    out.update(aux_yhat_fields(item))
    out.update(tip_explain_fields(item))
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def _fusion_weight_fields(cfg: Optional[dict]) -> Dict[str, float]:
    """写入成交/持有行，供 tip 与 rank_lots 决策权同套。"""
    from core.signal.yhat_windows import fusion_w_co_from_cfg, fusion_weights_from_cfg

    w_oo, w_τc = fusion_weights_from_cfg(cfg)
    w_co = fusion_w_co_from_cfg(cfg)
    return {
        "fusion_w_oo": w_oo,
        "fusion_w_oc": w_τc,
        "fusion_w_co": w_co,
    }


def _try_held_sell(
    *,
    h: dict,
    code: str,
    name: str,
    item: dict,
    rp: Optional[float],
    rs: Optional[float],
    as_of: Optional[str],
    prices: Dict[str, float],
    t0_blocks: dict,
    target_sh: float,
    reason: str,
    fusion_w_oo: Optional[float] = None,
    fusion_w_oc: Optional[float] = None,
    fusion_w_co: Optional[float] = None,
) -> Tuple[Optional[dict], Optional[dict], float]:
    """T+1 / 做 T 拦卖。返回 (sell, skip, cash_delta)。可卖部分一律记清仓。"""
    from core.paper.tplus1 import clip_sell_shares

    dbg = _debug_scores(
        item,
        rp,
        rs,
        fusion_w_oo=fusion_w_oo,
        fusion_w_oc=fusion_w_oc,
        fusion_w_co=fusion_w_co,
    )
    t0_reason = str(t0_blocks.get(code) or "").strip()
    if t0_reason:
        skip = {
            "stock_code": code,
            "stock_name": name,
            "side": "sell",
            "action": ACTION_SKIP,
            "reason": t0_reason,
            **dbg,
        }
        return None, skip, 0.0
    sell_sh, t1 = clip_sell_shares(h, target_sh, as_of=as_of)
    sell_sh = int(float(sell_sh or 0) // 100) * 100
    if sell_sh <= 0:
        skip = {
            "stock_code": code,
            "stock_name": name,
            "side": "sell",
            "action": ACTION_SKIP,
            "reason": t1.get("reason") or "T+1 不可卖 / 不足一手",
            **dbg,
        }
        return None, skip, 0.0
    px = _f(prices.get(code))
    if px is None or px <= 0:
        skip = {
            "stock_code": code,
            "stock_name": name,
            "side": "sell",
            "shares": float(h.get("shares") or 0),
            "action": ACTION_SKIP,
            "reason": f"{reason} · 无有效报价未卖出",
            **dbg,
        }
        return None, skip, 0.0
    cash_delta = float(sell_sh) * float(px)
    sell = {
        "side": "sell",
        "stock_code": code,
        "stock_name": name,
        "shares": float(sell_sh),
        "action": ACTION_EXIT,
        "matrix_action": ACTION_EXIT,
        "reason": reason,
        **dbg,
    }
    return sell, None, cash_delta


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


def _open_px_of(
    item: Optional[dict],
    code: str,
    opens: Optional[Dict[str, float]],
) -> Optional[float]:
    if isinstance(opens, dict):
        v = _f(opens.get(code))
        if v is not None and v > 0:
            return v
    if isinstance(item, dict):
        v = _f(item.get("day_open"))
        if v is not None and v > 0:
            return v
    return None


def _price_tau_of(
    code: str,
    prices: Dict[str, float],
    sell_prices: Optional[Dict[str, float]],
    item: Optional[dict] = None,
) -> Optional[float]:
    v = _f(prices.get(code)) if isinstance(prices, dict) else None
    if v is not None and v > 0:
        return v
    if isinstance(sell_prices, dict):
        v = _f(sell_prices.get(code))
        if v is not None and v > 0:
            return v
    if isinstance(item, dict):
        v = _f(item.get("price_tau"))
        if v is not None and v > 0:
            return v
    return None


def plan_rank_lot_day(
    *,
    scored: Sequence[dict],
    holdings: Sequence[dict],
    cash: float,
    prices: Dict[str, float],
    cfg: dict,
    as_of: Optional[str] = None,
    sell_prices: Optional[Dict[str, float]] = None,
    opens: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """一天的卖/买计划。价格用于估算现金是否够买该手；成交仍由调用方落地。

    ``sell_prices`` 缺省等于 ``prices``。历史回测可只给清仓回退价、买入仍走严格钟。
    ``opens`` 为当日开盘；与 ``prices``（τ 成交价）一起把 ranking 写成
    τc 几何剩余。缺 y_spec 的旧行才减 (price(τ)/open−1)；缺开盘则不扣。
    """
    rank_enter = coerce_rank_threshold(
        cfg.get("rank_enter"), DEFAULT_RANK_ENTER
    )
    rank_strong = coerce_rank_threshold(
        cfg.get("rank_strong"), DEFAULT_RANK_STRONG
    )
    enter_cfg = cfg
    rank_strong_eff = rank_strong
    cash_floor = float(cfg.get("cash_floor") or 0.0)
    try:
        mv_cap = float(cfg.get("holdings_mv_cap") or 0.0)
    except (TypeError, ValueError):
        mv_cap = 0.0
    if mv_cap != mv_cap:
        mv_cap = 0.0
    mv_cap = max(0.0, mv_cap)
    top_k = max(1, int(cfg.get("top_k") or 15))
    amount_base, amount_strong = _lot_amounts_from_cfg(cfg)
    from core.paper.sizing import shares_from_amount
    px_buy = prices if isinstance(prices, dict) else {}
    px_sell = sell_prices if isinstance(sell_prices, dict) else px_buy

    def _rp_of(code: str, item: dict) -> Optional[float]:
        o = _open_px_of(item, code, opens)
        p = _price_tau_of(code, px_buy, px_sell, item)
        if o is not None:
            item["day_open"] = o
        if p is not None:
            item["rebalance_px"] = p
        return ranking_pct_of(item, cfg, open_px=o, price_tau=p)

    from core.signal.yhat_windows import fusion_w_co_from_cfg, fusion_weights_from_cfg

    w_oo, w_τc = fusion_weights_from_cfg(cfg)
    w_co = fusion_w_co_from_cfg(cfg)
    w_fields = _fusion_weight_fields(cfg)
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

    rp_by: Dict[str, Optional[float]] = {
        code: _rp_of(code, item) for code, item in item_by.items()
    }

    def _rs_enter(code: str, rp: Optional[float]) -> Optional[float]:
        _ = code
        return None if rp is None else float(rp) / 100.0

    def _rs_display(rp: Optional[float]) -> Optional[float]:
        return None if rp is None else float(rp) / 100.0

    sells: List[dict] = []
    skips: List[dict] = []
    cash_sim = float(cash or 0.0)

    for h in held_rows:
        code = str(h.get("stock_code") or "").strip()
        item = item_by.get(code) or {}
        rp = rp_by.get(code)
        rs = _rs_enter(code, rp)
        rs_disp = _rs_display(rp)
        name = _display_name(code, item.get("stock_name"), h.get("stock_name"))
        held_sh = float(h.get("shares") or 0)
        exit_why = held_exit_reason(item, rs)
        if not exit_why:
            skip_enter = rank_lot_enter_skip_reason(
                item, enter_cfg, rs=rs
            )
            if skip_enter:
                exit_why = f"{skip_enter} 清仓"
        if exit_why:
            sell, skip, d_cash = _try_held_sell(
                h=h,
                code=code,
                name=name,
                item=item,
                rp=rp,
                rs=rs_disp,
                as_of=as_of,
                prices=px_sell,
                t0_blocks=t0_blocks,
                target_sh=held_sh,
                reason=exit_why,
                fusion_w_oo=w_oo,
                fusion_w_oc=w_τc,
                fusion_w_co=w_co,
            )
            if sell:
                cash_sim += d_cash
                sells.append(sell)
            if skip:
                skips.append(skip)

    cand: List[Tuple[float, dict, Optional[float]]] = []
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
        rp = rp_by.get(code)
        rs = _rs_enter(code, rp)
        rs_disp = _rs_display(rp)
        dbg = _debug_scores(item, rp, rs_disp, **w_fields)
        if item.get("hard_reject"):
            skips.append(
                {
                    "stock_code": code,
                    "stock_name": _display_name(code, item.get("stock_name")),
                    "side": "buy",
                    "action": ACTION_SKIP,
                    "reason": "hard_reject",
                    **dbg,
                }
            )
            continue
        skip_enter = rank_lot_enter_skip_reason(
            item, enter_cfg, rs=rs
        )
        if skip_enter:
            skips.append(
                {
                    "stock_code": code,
                    "stock_name": _display_name(code, item.get("stock_name")),
                    "side": "buy",
                    "action": ACTION_SKIP,
                    "reason": skip_enter,
                    **dbg,
                }
            )
            continue
        if rs is None:
            continue
        cand.append((float(rs), item, rp))
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
        px = _f(px_buy.get(code))
        if px is None:
            px = _f(px_sell.get(code))
        if px is not None and px > 0 and sh > 0:
            mv_sim += float(sh) * float(px)
    buys: List[dict] = []
    rank_n = len(cand)
    for i, (rs, item, rp) in enumerate(cand, start=1):
        code = str(item.get("stock_code") or "").strip()
        if not code:
            continue
        dbg = _debug_scores(item, rp, _rs_display(rp), rank_i=i, rank_n=rank_n, **w_fields)
        px = _f(px_buy.get(code))
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
        is_strong = float(rs) > float(rank_strong_eff)
        amt = amount_strong if is_strong else amount_base
        lots = shares_from_amount(amt, px, LOT_MIN)
        lot_kind = "strong" if is_strong else "base"
        want = int(lots)
        need = float(lots) * float(px)
        if cash_sim - need < cash_floor - 1e-6 and is_strong:
            lots = shares_from_amount(amount_base, px, LOT_MIN)
            lot_kind = "base"
            need = float(lots) * float(px)
        clipped = clip_lot_to_cash(
            lots, px, cash_sim, cash_floor, lot_min=LOT_MIN
        )
        if clipped <= 0:
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
                    "shares": float(want),
                    "price": float(px),
                    "amount": round(float(want) * float(px), 2),
                    "action": ACTION_SKIP,
                    "reason": skip_reason,
                    **dbg,
                    "lot_kind": lot_kind,
                }
            )
            continue
        if clipped < lots:
            lots = clipped
            lot_kind = "clipped"
            need = float(lots) * float(px)
        dbg["lot_kind"] = lot_kind
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
                    (
                        f"ranking={rs * 100.0:.3f}%>{rank_strong * 100.0:g}% 买{lots}股"
                        if is_strong
                        else f"ranking={rs * 100.0:.3f}%>入场{rank_enter * 100.0:g}% 买{lots}股"
                    )
                    + (f"（现金不够{want}）" if lots < want else "")
                ),
                **dbg,
            }
        )

    y_oo_gt0, y_τc_gt0 = _y_gt0_flags(cfg)
    return {
        "sells": sells,
        "buys": buys,
        "holds": [],
        "skips": skips,
        "cash_after_plan": round(cash_sim, 2),
        "rank_enter": rank_enter,
        "rank_strong": rank_strong,
        "y_oo_gt0": bool(y_oo_gt0),
        "y_τc_gt0": bool(y_τc_gt0),
        "oo_rank_max": oo_rank_max_from_cfg(cfg),
        "cash_floor": cash_floor,
        "holdings_mv_cap": mv_cap,
        "holdings_mv_after_plan": round(mv_sim, 2),
        "fusion_w_oo": w_oo,
        "fusion_w_oc": w_τc,
        "fusion_w_co": w_co,
        "top_k": top_k,
    }


__all__ = [
    "ACTION_ADD",
    "ACTION_EXIT",
    "ACTION_HOLD",
    "ACTION_OPEN",
    "ACTION_REDUCE",
    "ACTION_SKIP",
    "DEFAULT_CASH_FLOOR",
    "DEFAULT_HOLDINGS_MV_CAP",
    "DEFAULT_INITIAL_CASH",
    "DEFAULT_RANK_ENTER",
    "DEFAULT_RANK_STRONG",
    "DEFAULT_FUSION_W_CO",
    "LOT_AMOUNT_BASE",
    "LOT_AMOUNT_MAX",
    "LOT_AMOUNT_MIN",
    "LOT_AMOUNT_STRONG",
    "LOT_MIN",
    "FUSION_W_CO_MAX",
    "account_ref_equity",
    "AUX_YHAT_KEYS",
    "aux_yhat_fields",
    "tip_explain_fields",
    "clip_lot_to_cash",
    "clamp_fusion_weight",
    "clamp_fusion_w_co",
    "coerce_rank_threshold",
    "get_rank_lot_cfg",
    "held_exit_reason",
    "scale_cash_floor_to_account",
    "lot_shares_for_rank",
    "plan_rank_lot_day",
    "ranking_pct_of",
    "rank_lot_enter_skip_reason",
    "oo_rank_max_from_cfg",
    "y_tau_of",
]
