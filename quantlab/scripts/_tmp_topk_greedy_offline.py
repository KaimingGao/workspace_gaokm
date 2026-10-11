"""离线临时验证：入场闸 → 按 ranking 排序开/加 → 先花光再砍尾喂头。

不改产品调仓、不写 paper / last_*.json。纯内存合成场景。

入场（均须过，决定「可持 / 可买」）：
  - ranking > θ（默认 0.5%）
  - y_oo > 0（缺分不拦）
  - y_τc > 0（缺分不拦）
  - **唯一 TopK**：y_oo_rank < oo_rank_max（默认 38，对齐现网 top50%≈n·0.5；缺 rank 不拦；≤0=关）
    不另按 ranking score 截前 K。

路径（相对现网 rank_lots 的差异点）：
  - 过入场即可开/加；按 **oo_rank 升序**扫，**每只每天只加一笔**（≤add_max）
  - 该笔现金不够时，按 oo_rank 从差到好砍尾，凑够后再加这一笔（不加第二笔）
  - 未过入场 / hard_reject / 掉档：清仓

用法（在 quantlab/ 下）::

    .venv/bin/python scripts/_tmp_topk_greedy_offline.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple  # Tuple: sort key


LOT_MIN = 100
DEFAULT_RANK_ENTER = 0.005  # 默认展示 0.5 → 净收益 0.5%
DEFAULT_OO_RANK_MAX = 38  # 唯一 TopK：须 y_oo_rank < 此值（对齐现网 oo_rank_top_frac=0.5 · n≈76）
DEFAULT_ADD_MAX = 20_000.0
DEFAULT_STOCK_CAP = 50_000.0
# 产品启用分档时常见勾选（与 predictability_tiers.DEFAULT_LIVE_ALLOWED 一致）
PRODUCT_ALLOWED_TIERS: Tuple[str, ...] = ("A",)


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


def shares_from_amount(amount: float, price: float, lot: int = LOT_MIN) -> int:
    if price <= 0 or amount <= 0:
        return 0
    raw = int(float(amount) / float(price) / float(lot)) * int(lot)
    return max(0, raw)


@dataclass
class PlanResult:
    sells: List[dict] = field(default_factory=list)
    buys: List[dict] = field(default_factory=list)
    holds: List[dict] = field(default_factory=list)
    skips: List[dict] = field(default_factory=list)
    candidate: List[str] = field(default_factory=list)
    cash_after: float = 0.0
    holdings_after: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


def _norm_tiers(raw: Optional[Sequence[str]]) -> Optional[List[str]]:
    """None/空 → 分档闸关闭；否则大写去重保序。"""
    if raw is None:
        return None
    out: List[str] = []
    seen = set()
    for t in raw:
        u = str(t or "").strip().upper()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out or None


def plan_topk_greedy_day(
    *,
    scored: Sequence[dict],
    holdings: Sequence[dict],
    cash: float,
    prices: Dict[str, float],
    rank_enter: float = DEFAULT_RANK_ENTER,
    oo_rank_max: Optional[int] = DEFAULT_OO_RANK_MAX,
    add_max: float = DEFAULT_ADD_MAX,
    stock_cap: float = DEFAULT_STOCK_CAP,
    lot_min: int = LOT_MIN,
    allowed_tiers: Optional[Sequence[str]] = None,
) -> PlanResult:
    """入场闸 → 按 oo_rank 扫一轮开/加（每只一天一笔）→ 不够则砍尾凑这一笔。

    过入场即可买/加。唯一 TopK 是 ``oo_rank_max``（y_oo_rank < max）；None/≤0=关；缺 rank 不拦。
    买序/砍尾均按 oo_rank 升序（小更好）；缺 oo_rank 时回退 ranking 降序。
    无「循环加到单票帽」；无减仓、涨超不加；掉档/未过入场才清仓。
    """
    out = PlanResult()
    cash_sim = float(cash or 0.0)
    theta = float(rank_enter)
    add_max = float(add_max)
    stock_cap = float(stock_cap)
    allow = _norm_tiers(allowed_tiers)
    try:
        oo_max = int(oo_rank_max) if oo_rank_max is not None else 0
    except (TypeError, ValueError):
        oo_max = 0
    if oo_max <= 0:
        oo_max = 0

    item_by: Dict[str, dict] = {}
    for it in scored or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if code:
            item_by[code] = dict(it)

    held_sh: Dict[str, float] = {}
    for h in holdings or []:
        if not isinstance(h, dict):
            continue
        code = str(h.get("stock_code") or "").strip()
        sh = float(h.get("shares") or 0)
        if code and sh > 0:
            held_sh[code] = sh
            if code not in item_by:
                item_by[code] = {"stock_code": code}

    def _tier(code: str) -> Optional[str]:
        it = item_by.get(code) or {}
        for key in ("predictability_tier", "fit_tier", "tier"):
            raw = it.get(key)
            if raw in (None, ""):
                continue
            return str(raw).strip().upper()
        return None

    def _tier_ok(code: str) -> bool:
        if allow is None:
            return True
        t = _tier(code)
        return t is not None and t in allow

    def _pick_y(code: str, *keys: str) -> Optional[float]:
        it = item_by.get(code) or {}
        for key in keys:
            v = _f(it.get(key))
            if v is not None:
                return float(v)
        return None

    def _y_oo(code: str) -> Optional[float]:
        return _pick_y(
            code,
            "y_oo",
            "predicted_score",
            "predicted_score_eod",
            "score_eod",
        )

    def _y_τc(code: str) -> Optional[float]:
        return _pick_y(
            code,
            "y_τc",
            "y_tau",
            "predicted_score_tau",
            "score_rem",
        )

    def _ranking(code: str) -> Optional[float]:
        it = item_by.get(code) or {}
        return _f(it.get("ranking"))

    def _sort_key(code: str) -> Tuple[float, float, str]:
        """oo_rank 升序（名次小更好）；缺 oo_rank 回退 -ranking；再按代码。"""
        rk = _oo_rank_of(code)
        rs = _ranking(code)
        primary = float(rk) if rk is not None else 1e18
        secondary = -float(rs) if rs is not None else 1e18
        return (primary, secondary, code)

    def _pos_gt0_skip(val: Optional[float], label: str) -> Optional[str]:
        """勾选闸：须 y>0；缺分不拦（对齐 rank_lots）。"""
        if val is None:
            return None
        if float(val) <= 0:
            return f"{label}={float(val):.3f}≤0 未过入场"
        return None

    def _oo_rank_of(code: str) -> Optional[float]:
        it = item_by.get(code) or {}
        for key in ("y_oo_rank", "y_oo_rank_hat", "predicted_score_oo_rank"):
            v = _f(it.get(key))
            if v is not None:
                return float(v)
        return None

    def _oo_rank_skip(code: str) -> Optional[str]:
        if oo_max <= 0:
            return None
        rank = _oo_rank_of(code)
        if rank is None:
            return None
        if float(rank) >= float(oo_max):
            return f"oo_rank={int(rank)}≥{oo_max} 未过入场"
        return None

    def _px(code: str) -> Optional[float]:
        v = _f(prices.get(code))
        if v is None or v <= 0:
            return None
        return float(v)

    def _mv(code: str, sh: float) -> float:
        px = _px(code)
        if px is None or sh <= 0:
            return 0.0
        return float(sh) * float(px)

    # 0) 观察池分档：持仓掉档 → 强制清仓（先于名单外逻辑，理由单独标明）
    tier_label = "".join(allow) if allow else ""
    force_exit = []
    if allow is not None:
        out.notes.append(f"分档闸=开 allowed={allow}")
        for code in list(held_sh.keys()):
            if _tier_ok(code):
                continue
            force_exit.append(code)
            sh = float(held_sh.get(code) or 0)
            px = _px(code)
            why = f"可预测性非{tier_label}档 清仓"
            if sh <= 0:
                continue
            if px is None:
                out.skips.append(
                    {"stock_code": code, "side": "sell", "reason": f"{why}·无报价"}
                )
                continue
            cash_sim += sh * px
            out.sells.append(
                {
                    "stock_code": code,
                    "shares": sh,
                    "price": px,
                    "amount": round(sh * px, 2),
                    "reason": why,
                    "predictability_tier": _tier(code),
                    "y_oo_rank": (item_by.get(code) or {}).get("y_oo_rank"),
                }
            )
            held_sh[code] = 0.0
        out.notes.append(f"分档掉档清仓={force_exit}")
    else:
        out.notes.append("分档闸=关（不过滤）")

    # 1–2) 入场池：过入场即可买/加；唯一 TopK = oo_rank
    oo_note = f" ∧ oo_rank<{oo_max}(TopK)" if oo_max > 0 else " ∧ oo_rank闸关"
    out.notes.append(
        f"入场闸=ranking>θ ∧ y_oo>0 ∧ y_τc>0{oo_note}；"
        "过入场按 oo_rank 一天一笔；不够则砍尾凑这一笔"
    )
    enter_fail: Dict[str, str] = {}
    pool: List[str] = []
    for code, it in item_by.items():
        if allow is not None and not _tier_ok(code):
            why = f"分档{_tier(code) or '缺档'}∉{allow}"
            enter_fail[code] = why
            out.skips.append(
                {
                    "stock_code": code,
                    "reason": why,
                    "predictability_tier": _tier(code),
                }
            )
            continue
        if it.get("hard_reject"):
            why = "hard_reject"
            enter_fail[code] = why
            out.skips.append({"stock_code": code, "reason": why})
            continue
        rs = _ranking(code)
        if rs is None:
            why = "ranking 缺失"
            enter_fail[code] = why
            out.skips.append({"stock_code": code, "reason": why})
            continue
        if float(rs) <= theta:
            why = f"ranking={float(rs)*100:.3f}%≤θ={theta*100:g}%"
            enter_fail[code] = why
            out.skips.append({"stock_code": code, "reason": why})
            continue
        skip_oo = _pos_gt0_skip(_y_oo(code), "y_oo")
        if skip_oo:
            enter_fail[code] = skip_oo
            out.skips.append({"stock_code": code, "reason": skip_oo})
            continue
        skip_τc = _pos_gt0_skip(_y_τc(code), "y_τc")
        if skip_τc:
            enter_fail[code] = skip_τc
            out.skips.append({"stock_code": code, "reason": skip_τc})
            continue
        skip_or = _oo_rank_skip(code)
        if skip_or:
            enter_fail[code] = skip_or
            out.skips.append({"stock_code": code, "reason": skip_or})
            continue
        pool.append(code)
    pool.sort(key=_sort_key)
    cand = list(pool)
    cand_set = set(cand)
    out.candidate = list(cand)
    out.notes.append(f"|eligible开加|={len(cand)}（按 oo_rank 升序）")

    # 3) 仅未过入场 → 清仓；过入场续持（稍后按序开/加）
    for code in list(held_sh.keys()):
        sh = float(held_sh.get(code) or 0)
        if sh <= 0:
            continue
        if code in cand_set:
            continue
        px = _px(code)
        why = enter_fail.get(code) or "未过入场清仓"
        if px is None:
            out.skips.append(
                {"stock_code": code, "side": "sell", "reason": f"{why}·无报价"}
            )
            continue
        cash_sim += sh * px
        out.sells.append(
            {
                "stock_code": code,
                "shares": sh,
                "price": px,
                "amount": round(sh * px, 2),
                "reason": f"{why} 清仓",
                "predictability_tier": _tier(code),
                "y_oo_rank": (item_by.get(code) or {}).get("y_oo_rank"),
            }
        )
        held_sh[code] = 0.0

    def _target_lots(code: str) -> int:
        """当天这一笔目标手数（受 add_max / 单票帽约束，不受现金）。"""
        sh = float(held_sh.get(code) or 0)
        px = _px(code)
        if px is None:
            return 0
        mv = _mv(code, sh)
        if mv >= stock_cap - 1e-6:
            return 0
        room = stock_cap - mv
        budget = min(add_max, room)
        lots = shares_from_amount(budget, px, lot_min)
        if lots <= 0:
            return 0
        if _mv(code, sh + lots) > stock_cap + 1e-6:
            return 0
        return int(lots)

    def _min_lot_cost(code: str) -> Optional[float]:
        px = _px(code)
        if px is None:
            return None
        return float(lot_min) * float(px)

    def _tail_to_cut(head: str) -> Optional[str]:
        """持仓里 oo_rank 差于队头的最差票（名次更大；缺 rank/并列按代码）。"""
        tails: List[str] = []
        for code, sh in held_sh.items():
            if float(sh or 0) <= 0 or code == head:
                continue
            if _sort_key(code) <= _sort_key(head):
                continue
            tails.append(code)
        if not tails:
            return None
        tails.sort(key=_sort_key, reverse=True)
        return tails[0]

    def _try_buy_once(code: str, reason: str) -> bool:
        """当天对该票只加这一笔；现金不够则失败（由调用方砍尾后再试）。"""
        nonlocal cash_sim
        if code not in cand_set or code in bought_today:
            return False
        lots = _target_lots(code)
        px = _px(code)
        if px is None:
            out.skips.append({"stock_code": code, "side": "buy", "reason": "无报价"})
            return False
        if lots <= 0:
            return False
        need = float(lots) * float(px)
        if cash_sim + 1e-6 < need:
            return False
        cash_sim -= need
        held_sh[code] = float(held_sh.get(code) or 0) + float(lots)
        bought_today.add(code)
        out.buys.append(
            {
                "stock_code": code,
                "shares": float(lots),
                "price": px,
                "amount": round(need, 2),
                "reason": reason,
                "y_oo_rank": (item_by.get(code) or {}).get("y_oo_rank"),
                "mv_after": round(_mv(code, held_sh[code]), 2),
            }
        )
        return True

    def _cut_tails_until_afford(head: str) -> bool:
        """砍差于 head 的持仓，直到够付 head 当天那一笔；无可砍则 False。"""
        nonlocal cash_sim
        guard = 0
        while guard < 200:
            guard += 1
            lots = _target_lots(head)
            px = _px(head)
            if px is None or lots <= 0:
                return False
            need = float(lots) * float(px)
            if cash_sim + 1e-6 >= need:
                return True
            tail_code = _tail_to_cut(head)
            if tail_code is None:
                return False
            sh = float(held_sh[tail_code])
            tpx = _px(tail_code)
            if tpx is None:
                out.skips.append(
                    {"stock_code": tail_code, "side": "sell", "reason": "砍尾·无报价"}
                )
                return False
            cash_sim += sh * tpx
            out.sells.append(
                {
                    "stock_code": tail_code,
                    "shares": sh,
                    "price": tpx,
                    "amount": round(sh * tpx, 2),
                    "reason": "现金不足·队尾清仓回流队头",
                    "y_oo_rank": (item_by.get(tail_code) or {}).get("y_oo_rank"),
                }
            )
            held_sh[tail_code] = 0.0
            out.holds = [h for h in out.holds if h.get("stock_code") != tail_code]
        return False

    # 4) 按 oo_rank 扫一轮：每只一天只加一笔；不够则砍尾凑这一笔
    bought_today: set = set()
    for code in cand:
        px = _px(code)
        if px is None:
            out.skips.append({"stock_code": code, "side": "buy", "reason": "无报价"})
            continue
        mv = _mv(code, float(held_sh.get(code) or 0))
        if mv >= stock_cap - 1e-6:
            out.holds.append(
                {
                    "stock_code": code,
                    "reason": "已达/超上界不加",
                    "mv": round(mv, 2),
                    "y_oo_rank": (item_by.get(code) or {}).get("y_oo_rank"),
                }
            )
            continue
        if _target_lots(code) <= 0:
            continue
        if _try_buy_once(code, "正向加仓"):
            continue
        min_cost = _min_lot_cost(code)
        if min_cost is not None and cash_sim + 1e-6 < min_cost:
            if not _cut_tails_until_afford(code):
                out.notes.append(f"{code} 现金不足且无可砍尾，停扫")
                break
            if _try_buy_once(code, "砍尾回流加仓"):
                continue
            out.notes.append(f"{code} 砍尾后仍不够一手，停扫")
            break
        # 现金够一手但不够整笔目标：先砍尾凑满目标；凑不满则跳过该票继续
        if _cut_tails_until_afford(code) and _try_buy_once(code, "砍尾回流加仓"):
            continue
        out.skips.append(
            {
                "stock_code": code,
                "side": "buy",
                "reason": "现金不足凑不满当天一笔",
            }
        )

    # hold 快照：过入场未动仓
    for code in list(held_sh.keys()):
        sh = float(held_sh.get(code) or 0)
        if sh <= 0:
            continue
        if any(b.get("stock_code") == code for b in out.buys):
            continue
        if any(s.get("stock_code") == code for s in out.sells):
            continue
        if any(h.get("stock_code") == code for h in out.holds):
            continue
        out.holds.append(
            {
                "stock_code": code,
                "shares": sh,
                "mv": round(_mv(code, sh), 2),
                "reason": "过入场续持",
                "y_oo_rank": (item_by.get(code) or {}).get("y_oo_rank"),
            }
        )

    out.cash_after = round(cash_sim, 2)
    out.holdings_after = {
        c: float(sh)
        for c, sh in held_sh.items()
        if float(sh or 0) > 0
    }
    final_mv = {
        c: round(_mv(c, sh), 2) for c, sh in out.holdings_after.items()
    }
    out.notes.append(f"最终持仓数={len(final_mv)}")
    out.notes.append(f"最终持仓市值={final_mv}")
    return out


def _holdings_rows(h: Dict[str, float]) -> List[dict]:
    return [{"stock_code": k, "shares": v} for k, v in sorted(h.items())]


def _print_plan(title: str, plan: PlanResult) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)
    print("eligible:", plan.candidate)
    for n in plan.notes:
        print(" ·", n)
    print(f"cash_after={plan.cash_after}")
    print("SELLS:", json.dumps(plan.sells, ensure_ascii=False, indent=2))
    print("BUYS:", json.dumps(plan.buys, ensure_ascii=False, indent=2))
    print("HOLDS:", json.dumps(plan.holds, ensure_ascii=False, indent=2))


def _with_tier(rows: List[dict], tier: str = "A") -> List[dict]:
    out = []
    for r in rows:
        row = {
            "y_oo": 0.5,
            "y_τc": 0.5,
            "predictability_tier": tier,
            **r,
        }
        out.append(row)
    return out


def scenario_outside_cleared() -> None:
    """未过入场才清仓；过入场均可开/加（可被砍尾喂头卖掉）。"""
    scored = _with_tier(
        [
            {"stock_code": "A", "ranking": 0.02},
            {"stock_code": "B", "ranking": 0.015},
            {"stock_code": "Z", "ranking": 0.01},  # 过入场，排序靠后
            {"stock_code": "W", "ranking": 0.001},  # 未过入场
        ]
    )
    prices = {"A": 10.0, "B": 10.0, "Z": 10.0, "W": 10.0}
    holdings = {"Z": 1000.0, "W": 1000.0}
    cash = 50_000.0
    plan = plan_topk_greedy_day(
        scored=scored,
        holdings=_holdings_rows(holdings),
        cash=cash,
        prices=prices,
        add_max=20_000,
        stock_cap=50_000,
        rank_enter=0.005,
        allowed_tiers=None,
    )
    _print_plan("S1 过入场可开加 + 未过入场清仓", plan)
    assert plan.candidate == ["A", "B", "Z"]
    assert any(s["stock_code"] == "W" for s in plan.sells), "W 未过入场应清仓"
    h2 = plan.holdings_after
    assert "W" not in h2
    # 一天一笔：A 只加 add_max=2 万，不循环加到单票帽
    assert abs(h2.get("A", 0) * 10.0 - 20_000.0) < 1e-6, h2
    assert sum(1 for b in plan.buys if b.get("stock_code") == "A") == 1
    print("OK S1", h2, "cash", plan.cash_after)


def scenario_cap_no_trim() -> None:
    """已超上界不加不减。"""
    scored = _with_tier(
        [
            {"stock_code": "A", "ranking": 0.02, "y_oo_rank": 1},
            {"stock_code": "B", "ranking": 0.02, "y_oo_rank": 2},
        ]
    )
    prices = {"A": 10.0, "B": 10.0}
    # A 已 6 万 > 5 万上界
    holdings = {"A": 6000.0}
    cash = 100_000.0
    plan = plan_topk_greedy_day(
        scored=scored,
        holdings=_holdings_rows(holdings),
        cash=cash,
        prices=prices,
        add_max=20_000,
        stock_cap=50_000,
        rank_enter=0.005,
        allowed_tiers=None,
    )
    _print_plan("S2 涨超上界不加不减", plan)
    assert not any(b["stock_code"] == "A" for b in plan.buys), "A 不应再加"
    assert not any(s["stock_code"] == "A" for s in plan.sells), "A 不应被减"
    assert any(b["stock_code"] == "B" for b in plan.buys), "B 应加仓"
    print("OK S2")


def scenario_spend_then_cut_tail() -> None:
    """现金紧：先给队头花，再砍队尾回流，最终持仓更靠前。"""
    scored = _with_tier(
        [
            {"stock_code": "A", "ranking": 0.02, "y_oo_rank": 1},
            {"stock_code": "B", "ranking": 0.02, "y_oo_rank": 2},
            {"stock_code": "C", "ranking": 0.02, "y_oo_rank": 3},
            {"stock_code": "D", "ranking": 0.02, "y_oo_rank": 4},
            {"stock_code": "E", "ranking": 0.02, "y_oo_rank": 5},
        ]
    )
    prices = {x: 10.0 for x in "ABCDE"}
    # 已持有队尾 D/E，占着现金；另有少量现金
    holdings = {"D": 2000.0, "E": 2000.0}  # 各 2 万
    cash = 25_000.0
    plan = plan_topk_greedy_day(
        scored=scored,
        holdings=_holdings_rows(holdings),
        cash=cash,
        prices=prices,
        add_max=20_000,
        stock_cap=50_000,
        rank_enter=0.005,
        allowed_tiers=None,
    )
    _print_plan("S3 先花光再砍尾回流", plan)
    h2 = plan.holdings_after
    print("final holdings", h2, "cash", plan.cash_after)
    assert "A" in h2, "队头 A 应持有"
    assert "E" not in h2, "队尾 E 应被清仓回流"
    assert "D" not in h2, "队尾 D 应被清仓回流"
    # 一天一笔：A 仅一笔 2 万，不堆到 5 万帽
    assert abs(h2.get("A", 0) * 10.0 - 20_000.0) < 1e-6, h2
    assert sum(1 for b in plan.buys if b.get("stock_code") == "A") == 1
    print("OK S3")


def scenario_filter_enter() -> None:
    """门槛过滤后只剩过入场票；现金优先喂队头。"""
    scored = _with_tier(
        [
            {"stock_code": "A", "ranking": 0.01, "y_oo_rank": 1},
            {"stock_code": "B", "ranking": 0.001, "y_oo_rank": 2},  # 不过 0.5%
            {"stock_code": "C", "ranking": 0.01, "y_oo_rank": 3},
        ]
    )
    prices = {"A": 10.0, "B": 10.0, "C": 10.0}
    plan = plan_topk_greedy_day(
        scored=scored,
        holdings=[],
        cash=30_000.0,
        prices=prices,
        add_max=20_000,
        stock_cap=50_000,
        rank_enter=0.005,
        allowed_tiers=None,
    )
    _print_plan("S4 门槛过滤 + 优先队头", plan)
    assert plan.candidate == ["A", "C"]
    assert "B" not in plan.candidate
    h2 = plan.holdings_after
    # 一天一笔：A 先加 2 万；剩 1 万不够 C 的整笔且无可砍尾 → 只留 A
    assert list(h2.keys()) == ["A"], h2
    assert abs(h2.get("A", 0) * 10.0 - 20_000.0) < 1e-6, h2
    print("OK S4", h2)


def scenario_y_sign_gates() -> None:
    """y_oo>0 / y_τc>0：符号闸挡住否则 ranking 够格的票。"""
    scored = [
        {"stock_code": "A", "ranking": 0.02, "y_oo": 0.5, "y_τc": 0.5, "y_oo_rank": 1},
        {"stock_code": "B", "ranking": 0.03, "y_oo": -0.1, "y_τc": 0.5, "y_oo_rank": 2},
        {"stock_code": "C", "ranking": 0.025, "y_oo": 0.5, "y_τc": -0.1, "y_oo_rank": 3},
    ]
    plan = plan_topk_greedy_day(
        scored=scored,
        holdings=[],
        cash=50_000.0,
        prices={"A": 10.0, "B": 10.0, "C": 10.0},
        rank_enter=0.005,
        oo_rank_max=30,
        allowed_tiers=None,
    )
    _print_plan("S5 y_oo/y_τc 符号闸", plan)
    assert plan.candidate == ["A"], plan.candidate
    assert "B" not in plan.holdings_after and "C" not in plan.holdings_after
    print("OK S5")


def scenario_eligible_all_can_add() -> None:
    """现金充裕：过入场且过 oo_rank 的票均可加（不另截 ranking 前 K）。"""
    scored = _with_tier(
        [
            {"stock_code": "A", "ranking": 0.03, "y_oo_rank": 1},
            {"stock_code": "B", "ranking": 0.02, "y_oo_rank": 2},
            {"stock_code": "Z", "ranking": 0.01, "y_oo_rank": 3},
        ]
    )
    plan = plan_topk_greedy_day(
        scored=scored,
        holdings=_holdings_rows({"Z": 1000.0}),
        cash=200_000.0,
        prices={"A": 10.0, "B": 10.0, "Z": 10.0},
        oo_rank_max=38,
        add_max=20_000,
        stock_cap=50_000,
        rank_enter=0.005,
        allowed_tiers=None,
    )
    _print_plan("S1b 过入场均可加（TopK=oo_rank）", plan)
    assert plan.candidate == ["A", "B", "Z"]
    assert "Z" in plan.holdings_after
    assert any(b.get("stock_code") == "Z" for b in plan.buys), plan.buys
    print("OK S1b", plan.holdings_after)


def scenario_oo_rank_max() -> None:
    """oo_rank < 30：名次过差不进入场池。"""
    scored = [
        {"stock_code": "A", "ranking": 0.02, "y_oo": 0.5, "y_τc": 0.5, "y_oo_rank": 5},
        {"stock_code": "B", "ranking": 0.03, "y_oo": 0.5, "y_τc": 0.5, "y_oo_rank": 30},
        {"stock_code": "C", "ranking": 0.025, "y_oo": 0.5, "y_τc": 0.5, "y_oo_rank": 40},
    ]
    plan = plan_topk_greedy_day(
        scored=scored,
        holdings=[],
        cash=50_000.0,
        prices={"A": 10.0, "B": 10.0, "C": 10.0},
        rank_enter=0.005,
        oo_rank_max=30,
        allowed_tiers=None,
    )
    _print_plan("S5b oo_rank<30", plan)
    assert plan.candidate == ["A"], plan.candidate
    assert any("oo_rank=30" in str(s.get("reason") or "") for s in plan.skips)
    assert any("oo_rank=40" in str(s.get("reason") or "") for s in plan.skips)
    print("OK S5b")


def scenario_predictability_tiers() -> None:
    """观察池分档：只开 A；B/C 档不能新开；持仓掉出 A → 强制清仓。"""
    scored = [
        {
            "stock_code": "A1",
            "ranking": 0.02,
            "y_oo": 0.5,
            "y_τc": 0.5,
            "predictability_tier": "A",
        },
        {
            "stock_code": "B1",
            "ranking": 0.03,  # ranking 更好
            "y_oo": 0.5,
            "y_τc": 0.5,
            "predictability_tier": "B",  # 但不在允许档
        },
        {
            "stock_code": "C1",
            "ranking": 0.02,
            "y_oo": 0.5,
            "y_τc": 0.5,
            "predictability_tier": "C",
        },
        {
            "stock_code": "A2",
            "ranking": 0.015,
            "y_oo": 0.5,
            "y_τc": 0.5,
            "predictability_tier": "A",
        },
    ]
    prices = {"A1": 10.0, "B1": 10.0, "C1": 10.0, "A2": 10.0}
    # 旧持仓掉到 B 档 → 应强制清仓
    holdings = {"B1": 2000.0, "C1": 1000.0}
    plan = plan_topk_greedy_day(
        scored=scored,
        holdings=_holdings_rows(holdings),
        cash=40_000.0,
        prices=prices,
        add_max=20_000,
        stock_cap=50_000,
        rank_enter=0.005,
        allowed_tiers=PRODUCT_ALLOWED_TIERS,  # ("A",)
    )
    _print_plan("S6 观察池分档闸（仅 A）", plan)
    assert plan.candidate == ["A1", "A2"], plan.candidate  # ranking 0.02 > 0.015
    assert "B1" not in plan.candidate and "C1" not in plan.candidate
    assert any(
        s["stock_code"] == "B1" and "可预测性" in str(s.get("reason") or "")
        for s in plan.sells
    ), plan.sells
    assert any(
        s["stock_code"] == "C1" and "可预测性" in str(s.get("reason") or "")
        for s in plan.sells
    ), plan.sells
    h2 = plan.holdings_after
    assert "B1" not in h2 and "C1" not in h2
    assert "A1" in h2
    # 掉档清仓回笼的钱按序一笔加 A1/A2，不买回 B1；一天一笔不到单票帽
    assert not any(b["stock_code"] == "B1" for b in plan.buys)
    assert sum(1 for b in plan.buys if b.get("stock_code") == "A1") == 1
    assert abs(h2.get("A1", 0) * 10.0 - 20_000.0) < 1e-6, h2
    print("OK S6", h2)


def main() -> None:
    scenario_outside_cleared()
    scenario_eligible_all_can_add()
    scenario_cap_no_trim()
    scenario_spend_then_cut_tail()
    scenario_filter_enter()
    scenario_y_sign_gates()
    scenario_oo_rank_max()
    scenario_predictability_tiers()
    print("\n全部合成场景通过（离线临时，未落库）。")


if __name__ == "__main__":
    main()
