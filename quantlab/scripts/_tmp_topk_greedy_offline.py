"""离线临时验证：观察池分档 → ranking 入场 → y_oo_rank TopK → 先花光再砍尾回流。

不改产品调仓、不写 paper / last_*.json。纯内存合成场景。

分档对齐现网 ``filter_codes_by_predictability_live``：
  - ``allowed_tiers`` 非空：只允许这些档进候选；持仓掉档 → 强制清仓
  - ``allowed_tiers`` 空/None：不过滤（等同 live 无 active）

用法（在 quantlab/ 下）::

    .venv/bin/python scripts/_tmp_topk_greedy_offline.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple  # Tuple: sort key


LOT_MIN = 100
DEFAULT_RANK_ENTER = 0.005  # 默认展示 0.5 → 净收益 0.5%
DEFAULT_TOP_K = 30
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
    top_k: int = DEFAULT_TOP_K,
    add_max: float = DEFAULT_ADD_MAX,
    stock_cap: float = DEFAULT_STOCK_CAP,
    lot_min: int = LOT_MIN,
    allowed_tiers: Optional[Sequence[str]] = None,
) -> PlanResult:
    """分档闸 → ranking 入场 → y_oo_rank TopK → 先花光再砍尾回流。

    无减仓、涨超不加；持仓掉出允许档强制清仓（对齐 watching_matrix）。
    """
    out = PlanResult()
    cash_sim = float(cash or 0.0)
    k = max(1, int(top_k))
    theta = float(rank_enter)
    add_max = float(add_max)
    stock_cap = float(stock_cap)
    allow = _norm_tiers(allowed_tiers)

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

    def _rank(code: str) -> Tuple[float, str]:
        it = item_by.get(code) or {}
        r = _f(it.get("y_oo_rank"))
        if r is None:
            return (1e18, code)
        return (float(r), code)

    def _ranking(code: str) -> Optional[float]:
        it = item_by.get(code) or {}
        return _f(it.get("ranking"))

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

    # 1–2) 分档允许 ∩ ranking 入场 → y_oo_rank 排序 → TopK
    pool: List[str] = []
    for code, it in item_by.items():
        if allow is not None and not _tier_ok(code):
            out.skips.append(
                {
                    "stock_code": code,
                    "reason": f"分档{_tier(code) or '缺档'}∉{allow}",
                    "predictability_tier": _tier(code),
                }
            )
            continue
        rs = _ranking(code)
        if rs is None:
            out.skips.append({"stock_code": code, "reason": "ranking 缺失"})
            continue
        if it.get("hard_reject"):
            out.skips.append({"stock_code": code, "reason": "hard_reject"})
            continue
        if _f(it.get("y_oo_rank")) is None:
            out.skips.append({"stock_code": code, "reason": "y_oo_rank 缺失"})
            continue
        if float(rs) <= theta:
            out.skips.append(
                {
                    "stock_code": code,
                    "reason": f"ranking={float(rs)*100:.3f}%≤θ={theta*100:g}%",
                }
            )
            continue
        pool.append(code)
    pool.sort(key=_rank)
    cand = pool[:k]
    cand_set = set(cand)
    out.candidate = list(cand)
    out.notes.append(f"|C|={len(cand)} / pool={len(pool)} / K={k}")

    # 3) 不在 C → 清仓（分档已清的跳过）
    for code in list(held_sh.keys()):
        if code in cand_set:
            continue
        sh = float(held_sh.get(code) or 0)
        px = _px(code)
        if sh <= 0:
            continue
        if px is None:
            out.skips.append(
                {"stock_code": code, "side": "sell", "reason": "名单外清仓·无报价"}
            )
            continue
        cash_sim += sh * px
        out.sells.append(
            {
                "stock_code": code,
                "shares": sh,
                "price": px,
                "amount": round(sh * px, 2),
                "reason": "不在候选队列清仓",
                "predictability_tier": _tier(code),
                "y_oo_rank": (item_by.get(code) or {}).get("y_oo_rank"),
            }
        )
        held_sh[code] = 0.0

    def _wants_add(code: str) -> bool:
        """市值未到上界，且 add_max/剩余空间至少够一手（不计现金）。"""
        sh = float(held_sh.get(code) or 0)
        px = _px(code)
        if px is None:
            return False
        mv = _mv(code, sh)
        if mv >= stock_cap - 1e-6:
            return False
        room = stock_cap - mv
        budget = min(add_max, room)
        lots = shares_from_amount(budget, px, lot_min)
        return lots > 0 and _mv(code, sh + lots) <= stock_cap + 1e-6

    def _head_wanting() -> Optional[str]:
        for code in cand:
            if _wants_add(code):
                return code
        return None

    def _min_lot_cost(code: str) -> Optional[float]:
        px = _px(code)
        if px is None:
            return None
        return float(lot_min) * float(px)

    def _try_buy(code: str, reason: str) -> bool:
        nonlocal cash_sim
        sh = float(held_sh.get(code) or 0)
        px = _px(code)
        if px is None:
            out.skips.append({"stock_code": code, "side": "buy", "reason": "无报价"})
            return False
        mv = _mv(code, sh)
        if mv >= stock_cap - 1e-6:
            return False
        room = stock_cap - mv
        budget = min(add_max, room, cash_sim)
        lots = shares_from_amount(budget, px, lot_min)
        if lots <= 0:
            return False
        if _mv(code, sh + lots) > stock_cap + 1e-6:
            return False
        need = lots * px
        if cash_sim + 1e-6 < need:
            return False
        cash_sim -= need
        held_sh[code] = sh + float(lots)
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

    # 4) 先花光：队头→队尾扫一轮（每只最多加一笔 add_max）
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
        if _try_buy(code, "队列正向加仓"):
            continue
        min_cost = _min_lot_cost(code)
        if min_cost is not None and cash_sim + 1e-6 < min_cost:
            out.notes.append(f"正向加仓在 {code} 前现金不足一手，停扫")
            break

    # 5) 再砍尾 + 回流：仅当队头还想加、但现金不够一手时才砍
    guard = 0
    while guard < 200:
        guard += 1
        head = _head_wanting()
        if head is None:
            break
        min_cost = _min_lot_cost(head)
        if min_cost is not None and cash_sim + 1e-6 >= min_cost:
            # 余钱先喂队头，绝不为此砍尾
            if _try_buy(head, "余钱继续加队头"):
                continue
            break
        head_i = cand.index(head)
        tail_code = None
        for code in reversed(cand[head_i + 1 :]):
            if float(held_sh.get(code) or 0) > 0:
                tail_code = code
                break
        if tail_code is None:
            out.notes.append("队头仍可加但无可砍尾持仓，停止置换")
            break
        sh = float(held_sh[tail_code])
        px = _px(tail_code)
        if px is None:
            out.skips.append(
                {"stock_code": tail_code, "side": "sell", "reason": "砍尾·无报价"}
            )
            break
        cash_sim += sh * px
        out.sells.append(
            {
                "stock_code": tail_code,
                "shares": sh,
                "price": px,
                "amount": round(sh * px, 2),
                "reason": "现金不足·队尾清仓回流队头",
                "y_oo_rank": (item_by.get(tail_code) or {}).get("y_oo_rank"),
            }
        )
        held_sh[tail_code] = 0.0
        _try_buy(head, f"砍尾({tail_code})回流加仓")

    # hold 快照
    for code in cand:
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
                "reason": "候选内续持",
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
    out.notes.append(f"最终持仓数={len(final_mv)} <K? {len(final_mv) < k}")
    out.notes.append(f"最终持仓市值={final_mv}")
    return out


def _holdings_rows(h: Dict[str, float]) -> List[dict]:
    return [{"stock_code": k, "shares": v} for k, v in sorted(h.items())]


def _print_plan(title: str, plan: PlanResult) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)
    print("C:", plan.candidate)
    for n in plan.notes:
        print(" ·", n)
    print(f"cash_after={plan.cash_after}")
    print("SELLS:", json.dumps(plan.sells, ensure_ascii=False, indent=2))
    print("BUYS:", json.dumps(plan.buys, ensure_ascii=False, indent=2))
    print("HOLDS:", json.dumps(plan.holds, ensure_ascii=False, indent=2))


def _with_tier(rows: List[dict], tier: str = "A") -> List[dict]:
    return [{**r, "predictability_tier": tier} for r in rows]


def scenario_outside_cleared() -> None:
    """不在 C 的旧持仓清仓；C 内按 y_oo_rank 加仓。"""
    scored = _with_tier(
        [
            {"stock_code": "A", "ranking": 0.01, "y_oo_rank": 1},
            {"stock_code": "B", "ranking": 0.01, "y_oo_rank": 2},
            {"stock_code": "Z", "ranking": 0.01, "y_oo_rank": 99},  # 进不了 TopK=2
        ]
    )
    prices = {"A": 10.0, "B": 10.0, "Z": 10.0}
    holdings = {"Z": 1000.0}  # 1万市值
    cash = 50_000.0
    plan = plan_topk_greedy_day(
        scored=scored,
        holdings=_holdings_rows(holdings),
        cash=cash,
        prices=prices,
        top_k=2,
        add_max=20_000,
        stock_cap=50_000,
        rank_enter=0.005,
        allowed_tiers=None,
    )
    _print_plan("S1 名单外清仓 + 队头加仓", plan)
    assert any(s["stock_code"] == "Z" for s in plan.sells), "Z 应清仓"
    assert plan.candidate == ["A", "B"]
    h2 = plan.holdings_after
    assert "Z" not in h2
    # 贪心：A 应优先加满到上界；余钱才留给 B
    assert h2.get("A", 0) * 10.0 >= 50_000.0 - 1e-6, h2
    assert "B" in h2
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
        top_k=2,
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
    # 5 只过门槛；K=5；现金只够明显偏向队头
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
        top_k=5,
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
    assert len(h2) < 5, "实际持仓应 < K"
    assert h2.get("A", 0) * 10.0 >= 50_000.0 - 1e-6, h2
    print("OK S3")


def scenario_filter_and_lt_k() -> None:
    """门槛过滤后候选不足 K；实际持仓 < K。"""
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
        top_k=30,
        add_max=20_000,
        stock_cap=50_000,
        rank_enter=0.005,
        allowed_tiers=None,
    )
    _print_plan("S4 门槛过滤 + 持仓 < K", plan)
    assert plan.candidate == ["A", "C"]
    assert "B" not in plan.candidate
    h2 = plan.holdings_after
    assert len(h2) < 30
    # 贪心回流后只留更靠前的 A（30k 现金全给 A）
    assert list(h2.keys()) == ["A"], h2
    print("OK S4", h2)


def scenario_predictability_tiers() -> None:
    """观察池分档：只开 A；B/C 档不能新开；持仓掉出 A → 强制清仓。"""
    scored = [
        {
            "stock_code": "A1",
            "ranking": 0.02,
            "y_oo_rank": 2,
            "predictability_tier": "A",
        },
        {
            "stock_code": "B1",
            "ranking": 0.03,  # ranking 更好
            "y_oo_rank": 1,  # 名次也更前
            "predictability_tier": "B",  # 但不在允许档
        },
        {
            "stock_code": "C1",
            "ranking": 0.02,
            "y_oo_rank": 3,
            "predictability_tier": "C",
        },
        {
            "stock_code": "A2",
            "ranking": 0.02,
            "y_oo_rank": 4,
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
        top_k=30,
        add_max=20_000,
        stock_cap=50_000,
        rank_enter=0.005,
        allowed_tiers=PRODUCT_ALLOWED_TIERS,  # ("A",)
    )
    _print_plan("S5 观察池分档闸（仅 A）", plan)
    assert plan.candidate == ["A1", "A2"], plan.candidate
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
    # 掉档清仓回笼的钱应喂给 A 档队头，而不是买回 B1
    assert not any(b["stock_code"] == "B1" for b in plan.buys)
    print("OK S5", h2)


def main() -> None:
    scenario_outside_cleared()
    scenario_cap_no_trim()
    scenario_spend_then_cut_tail()
    scenario_filter_and_lt_k()
    scenario_predictability_tiers()
    print("\n全部合成场景通过（离线临时，未落库）。")


if __name__ == "__main__":
    main()
