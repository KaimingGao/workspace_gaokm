"""离线临时对比：现网 rank_lots vs 新贪心（按 oo_rank 一天一笔 + 不够砍尾）。

唯一 TopK = ``oo_rank_max``（默认 y_oo_rank < 38，对齐现网 top50%%）；买序/砍尾按 oo_rank；
每只每天只加一笔，不循环加到单票帽。
清仓：fuse<0 或 y_oo<0 或 y_τc<0，或砍尾喂首（与入场 θ 解耦）。
回测结束会打印边诊断：买/卖腿命中幅度、整笔持仓盈亏、分票贡献。
对齐最近一次 ``last_portfolio_backtest`` 请求参数；``persist_curve=False`` 不落盘。
宇宙仍走观察池分档（``use_predictability_tiers=True``）。

临时烟测默认：**不补今开实时报价**、**不挂基准补拉**（只吃本地日 K / 5m）。

用法（在 quantlab/ 下）::

    .venv/bin/python scripts/_tmp_compare_rebalance_bt.py
    .venv/bin/python scripts/_tmp_compare_rebalance_bt.py --skip-baseline  # 只用落盘现网指标
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Deque, List, Optional, Sequence, Tuple

sys.path.insert(0, ".")

from scripts._tmp_topk_greedy_offline import plan_topk_greedy_day  # noqa: E402

# 与 paper_replay.fuse_hit_metrics / _sign_hit_yf 对齐（百分点死区）
_HIT_YHAT_EPS = 1e-12


def _fnum(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return float(v)


def _mean(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    return float(sum(xs) / len(xs))


def _sign_hit(yhat: Optional[float], realized: Optional[float]) -> Optional[bool]:
    if yhat is None or realized is None:
        return None
    if abs(float(yhat)) < _HIT_YHAT_EPS:
        return None
    if abs(float(realized)) < 1e-12:
        return None
    return (float(yhat) > 0) == (float(realized) > 0)


def _trade_yhat(row: dict) -> Optional[float]:
    """与 fuse_hit_metrics 取 ŷ 顺序一致（只用于符号命中）。"""
    yf = _fnum(row.get("y_oo_fuse"))
    if yf is None:
        yf = _fnum(row.get("y_oo_fuse"))
    if yf is None:
        rs = _fnum(row.get("y_oo_fuse_score"))
        if rs is not None:
            yf = float(rs) * 100.0
    return yf


def _leg_edge_bucket(trades: Sequence[dict], *, side: Optional[str] = None) -> Dict[str, Any]:
    """成交腿：命中/未命中的 y_oo_fuse_realized（百分点）均值。"""
    hit_reals: List[float] = []
    miss_reals: List[float] = []
    all_reals: List[float] = []
    for r in trades or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("status") or "") in ("skipped", "held"):
            continue
        s = str(r.get("side") or "").strip().lower()
        if s not in ("buy", "sell"):
            continue
        if side is not None and s != side:
            continue
        yf = _trade_yhat(r)
        real = _fnum(r.get("y_oo_fuse_realized"))
        hit = _sign_hit(yf, real)
        if hit is None or real is None:
            continue
        all_reals.append(float(real))
        if hit:
            hit_reals.append(float(real))
        else:
            miss_reals.append(float(real))
    n = len(all_reals)
    n_hit = len(hit_reals)
    n_miss = len(miss_reals)
    return {
        "n": n,
        "n_hit": n_hit,
        "n_miss": n_miss,
        "hit_rate_pct": round(n_hit / n * 100.0, 1) if n else None,
        "mean_realized_pct": round(_mean(all_reals), 4) if all_reals else None,
        "mean_hit_realized_pct": round(_mean(hit_reals), 4) if hit_reals else None,
        "mean_miss_realized_pct": round(_mean(miss_reals), 4) if miss_reals else None,
        # 粗期望（等权腿）：p*E[r|hit]+(1-p)*E[r|miss]；r 为百分点
        "edge_pct": (
            round(
                (n_hit / n) * float(_mean(hit_reals) or 0.0)
                + (n_miss / n) * float(_mean(miss_reals) or 0.0),
                4,
            )
            if n and hit_reals and miss_reals
            else (round(float(_mean(all_reals)), 4) if all_reals else None)
        ),
    }


def _roundtrip_edge(trades: Sequence[dict]) -> Dict[str, Any]:
    """FIFO 撮合买→卖：整笔持仓收益%与持有天数（用成交价，未再扣费）。"""
    lots: Dict[str, Deque[Tuple[str, float, float]]] = defaultdict(deque)
    rets: List[float] = []
    hold_days: List[float] = []
    wins: List[float] = []
    losses: List[float] = []
    # 优先用账本 sell.pnl_pct（若有）
    ledger_pnls: List[float] = []

    def _day(r: dict) -> str:
        return str(r.get("as_of") or r.get("signal_date") or r.get("date") or "")[:10]

    filled = [
        r
        for r in (trades or [])
        if isinstance(r, dict)
        and str(r.get("status") or "") not in ("skipped", "held")
        and str(r.get("side") or "").lower() in ("buy", "sell")
    ]
    filled.sort(key=lambda r: (_day(r), 0 if str(r.get("side")).lower() == "buy" else 1))

    for r in filled:
        code = str(r.get("stock_code") or "").strip()
        if not code:
            continue
        side = str(r.get("side") or "").lower()
        px = _fnum(r.get("price"))
        sh = _fnum(r.get("shares"))
        if px is None or sh is None or px <= 0 or sh <= 0:
            continue
        day = _day(r)
        if side == "buy":
            lots[code].append((day, float(px), float(sh)))
            continue
        # sell
        pnl_ledger = _fnum(r.get("pnl_pct"))
        if pnl_ledger is None:
            pnl_ledger = _fnum(r.get("return_pct"))
        if pnl_ledger is not None:
            ledger_pnls.append(float(pnl_ledger))

        remain = float(sh)
        while remain > 1e-9 and lots[code]:
            bday, bpx, bsh = lots[code][0]
            take = min(remain, bsh)
            ret = (float(px) / float(bpx) - 1.0) * 100.0
            rets.append(ret)
            if ret >= 0:
                wins.append(ret)
            else:
                losses.append(ret)
            try:
                from datetime import date as _date

                d0 = _date.fromisoformat(bday[:10])
                d1 = _date.fromisoformat(day[:10])
                hold_days.append(float((d1 - d0).days))
            except ValueError:
                pass
            if take + 1e-9 >= bsh:
                lots[code].popleft()
            else:
                lots[code][0] = (bday, bpx, bsh - take)
            remain -= take

    src = ledger_pnls if ledger_pnls else rets
    n = len(src)
    n_win = sum(1 for x in src if x > 0)
    n_loss = sum(1 for x in src if x < 0)
    return {
        "n_roundtrip": n,
        "win_rate_pct": round(n_win / n * 100.0, 1) if n else None,
        "mean_pnl_pct": round(_mean(src), 4) if src else None,
        "mean_win_pct": round(_mean([x for x in src if x > 0]), 4) if n_win else None,
        "mean_loss_pct": round(_mean([x for x in src if x < 0]), 4) if n_loss else None,
        "mean_hold_days": round(_mean(hold_days), 2) if hold_days else None,
        "pnl_source": "ledger_pnl_pct" if ledger_pnls else "fifo_price",
    }


def _stock_contrib_edge(result: Dict[str, Any]) -> Dict[str, Any]:
    rows = result.get("stock_contrib") or []
    pnls = [_fnum(r.get("pnl")) for r in rows if isinstance(r, dict)]
    pnls_f = [float(p) for p in pnls if p is not None]
    if not pnls_f:
        return {"n_stocks": 0}
    pos = [p for p in pnls_f if p > 0]
    neg = [p for p in pnls_f if p < 0]
    return {
        "n_stocks": len(pnls_f),
        "n_pos": len(pos),
        "n_neg": len(neg),
        "sum_pnl": round(sum(pnls_f), 2),
        "sum_pos": round(sum(pos), 2) if pos else 0.0,
        "sum_neg": round(sum(neg), 2) if neg else 0.0,
        "mean_pnl": round(_mean(pnls_f), 2),
    }


def _edge_diagnostics(result: Dict[str, Any]) -> Dict[str, Any]:
    trades = result.get("sim_trades") or result.get("trades") or []
    return {
        "legs_all": _leg_edge_bucket(trades, side=None),
        "legs_buy": _leg_edge_bucket(trades, side="buy"),
        "legs_sell": _leg_edge_bucket(trades, side="sell"),
        "roundtrip": _roundtrip_edge(trades),
        "stock_contrib": _stock_contrib_edge(result),
    }


def _print_edge_diagnostics(label: str, result: Dict[str, Any]) -> None:
    d = _edge_diagnostics(result)
    print(f"\n----- 边诊断 · {label} -----")

    def _line(tag: str, b: Dict[str, Any]) -> None:
        if not b.get("n"):
            print(f"  {tag}: 无有效腿")
            return
        print(
            f"  {tag}: n={b['n']} 命中={b['n_hit']}/{b['n']} ({b['hit_rate_pct']}%) "
            f"E[r]={b['mean_realized_pct']} 命中均r={b['mean_hit_realized_pct']} "
            f"未中均r={b['mean_miss_realized_pct']} 粗期望={b['edge_pct']}（实现%点）"
        )

    _line("全部腿", d["legs_all"])
    _line("买腿", d["legs_buy"])
    _line("卖腿", d["legs_sell"])
    rt = d["roundtrip"]
    if rt.get("n_roundtrip"):
        print(
            f"  整笔持仓({rt['pnl_source']}): n={rt['n_roundtrip']} "
            f"胜率={rt['win_rate_pct']}% 均盈亏={rt['mean_pnl_pct']}% "
            f"赚均={rt['mean_win_pct']}% 亏均={rt['mean_loss_pct']}% "
            f"均持有天={rt['mean_hold_days']}"
        )
    else:
        print("  整笔持仓: 无撮合样本")
    sc = d["stock_contrib"]
    if sc.get("n_stocks"):
        print(
            f"  分票贡献: 票={sc['n_stocks']} 盈票={sc['n_pos']} 亏票={sc['n_neg']} "
            f"Σpnl={sc['sum_pnl']} Σ盈={sc['sum_pos']} Σ亏={sc['sum_neg']} "
            f"均pnl={sc['mean_pnl']}"
        )


def _load_last_request() -> Dict[str, Any]:
    path = Path("data/last_portfolio_backtest.json")
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    result = doc.get("result") if isinstance(doc, dict) else None
    if not isinstance(result, dict):
        return {}
    return dict(result.get("request") or {})


def _load_last_metrics() -> Dict[str, Any]:
    path = Path("data/last_portfolio_backtest.json")
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    result = doc.get("result") if isinstance(doc, dict) else None
    if not isinstance(result, dict):
        return {}
    return {
        "metrics": dict(result.get("metrics") or {}),
        "params": dict(result.get("params") or {}),
        "request": dict(result.get("request") or {}),
        "saved_at": doc.get("saved_at"),
    }


def _summarize(label: str, result: Dict[str, Any]) -> Dict[str, Any]:
    m = result.get("metrics") or {}
    p = result.get("params") or {}
    eq = result.get("equity_curve") or []
    holds_end = 0
    if eq:
        last = eq[-1] if isinstance(eq[-1], dict) else {}
        holds_end = int(last.get("n_holdings") or last.get("holdings") or 0)
    trades = result.get("sim_trades") or result.get("trades") or []
    n_buy = sum(1 for t in trades if isinstance(t, dict) and t.get("side") == "buy")
    n_sell = sum(1 for t in trades if isinstance(t, dict) and t.get("side") == "sell")
    return {
        "label": label,
        "ok": bool(result.get("success")),
        "error": result.get("error"),
        "total_return_pct": m.get("total_return_pct"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "hit_rate_pct": m.get("hit_rate_pct"),
        "hit_n": m.get("hit_n"),
        "hit_hits": m.get("hit_hits"),
        "day_count": m.get("day_count"),
        "rebalance_days": m.get("rebalance_days"),
        "trade_count": m.get("trade_count") or m.get("sim_trade_count"),
        "n_buy": n_buy,
        "n_sell": n_sell,
        "initial_cash": p.get("initial_cash"),
        "fuse_enter": p.get("fuse_enter"),
        "top_k": p.get("top_k"),
        "holdings_end": holds_end,
    }


def _print_table(rows: Sequence[Dict[str, Any]]) -> None:
    cols = [
        ("label", "方案"),
        ("total_return_pct", "收益%"),
        ("max_drawdown_pct", "回撤%"),
        ("hit_rate_pct", "命中%"),
        ("hit_hits", "命中"),
        ("hit_n", "命中分母"),
        ("trade_count", "成交笔"),
        ("day_count", "天数"),
        ("holdings_end", "末日持仓"),
    ]
    widths = []
    for key, title in cols:
        w = len(title)
        for r in rows:
            w = max(w, len(str(r.get(key) if r.get(key) is not None else "—")))
        widths.append(w)
    head = "  ".join(title.ljust(widths[i]) for i, (_, title) in enumerate(cols))
    print(head)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(
            "  ".join(
                str(r.get(key) if r.get(key) is not None else "—").ljust(widths[i])
                for i, (key, _) in enumerate(cols)
            )
        )


def _make_greedy_adapter(
    *,
    add_max: float,
    stock_cap: float,
    allowed_tiers: Optional[Sequence[str]],
    oo_rank_max: Optional[int] = 38,
    cut_tail: bool = True,
):
    from core.paper.rebalance import rank_lots as rl

    def _adapter(
        *,
        scored,
        holdings,
        cash,
        prices,
        cfg,
        as_of=None,
        sell_prices=None,
        opens=None,
    ):
        del as_of
        px_buy = prices if isinstance(prices, dict) else {}
        px_sell = sell_prices if isinstance(sell_prices, dict) else px_buy
        # 计划用买价；清仓估价优先卖价（与 rank_lots 一致）
        px_plan = dict(px_buy)
        for code, px in px_sell.items():
            if code not in px_plan and px:
                px_plan[code] = px

        enriched: List[dict] = []
        item_by: Dict[str, dict] = {}
        open_by: Dict[str, float] = {}
        tau_by: Dict[str, float] = {}
        for it in scored or []:
            if not isinstance(it, dict):
                continue
            code = str(it.get("stock_code") or "").strip()
            if not code:
                continue
            row = dict(it)
            o = None
            if isinstance(opens, dict):
                try:
                    o = float(opens.get(code)) if opens.get(code) is not None else None
                except (TypeError, ValueError):
                    o = None
            p = None
            try:
                raw = px_buy.get(code)
                p = float(raw) if raw is not None else None
            except (TypeError, ValueError):
                p = None
            if p is None or p <= 0:
                try:
                    raw = px_sell.get(code)
                    p = float(raw) if raw is not None else None
                except (TypeError, ValueError):
                    p = None
            if o is not None and o > 0:
                open_by[code] = float(o)
                row["day_open"] = float(o)
            if p is not None and p > 0:
                tau_by[code] = float(p)
                row["rebalance_px"] = float(p)
            # 计划器用净收益；成交腿用百分点（与 rank_lots / fuse_hit_metrics 同口径）
            rp = rl.y_oo_fuse_pct_of(row, cfg, open_px=o, price_tau=p)
            row["_y_oo_fuse_pct"] = rp
            if rp is not None:
                row["y_oo_fuse"] = float(rp) / 100.0
            if row.get("predictability_tier") in (None, "") and allowed_tiers:
                row["predictability_tier"] = str(list(allowed_tiers)[0])
            enriched.append(row)
            item_by[code] = row

        # 持仓缺分行也要能挂 day_open / fuse，否则清仓腿进不了命中率分母
        for h in holdings or []:
            if not isinstance(h, dict):
                continue
            code = str(h.get("stock_code") or "").strip()
            if not code or code in item_by:
                continue
            row = {"stock_code": code}
            if isinstance(opens, dict) and opens.get(code) is not None:
                try:
                    o = float(opens.get(code))
                    if o > 0:
                        open_by[code] = o
                        row["day_open"] = o
                except (TypeError, ValueError):
                    pass
            p = tau_by.get(code)
            if p is None:
                for src in (px_sell, px_buy):
                    try:
                        raw = src.get(code)
                        if raw is not None and float(raw) > 0:
                            p = float(raw)
                            break
                    except (TypeError, ValueError):
                        continue
            if p is not None:
                tau_by[code] = p
                row["rebalance_px"] = p
            rp = rl.y_oo_fuse_pct_of(row, cfg, open_px=row.get("day_open"), price_tau=p)
            row["_y_oo_fuse_pct"] = rp
            if rp is not None:
                row["y_oo_fuse"] = float(rp) / 100.0
            item_by[code] = row
            enriched.append(row)

        w_fields = rl._fusion_weight_fields(cfg)

        def _leg_scores(code: str) -> Dict[str, Any]:
            item = item_by.get(code) or {"stock_code": code}
            rp = item.get("_y_oo_fuse_pct")
            if rp is None:
                rp = rl.y_oo_fuse_pct_of(
                    item,
                    cfg,
                    open_px=open_by.get(code),
                    price_tau=tau_by.get(code),
                )
            rs = None if rp is None else float(rp) / 100.0
            dbg = rl._debug_scores(item, rp, rs, **w_fields)
            if code in open_by:
                dbg["day_open"] = open_by[code]
            if code in tau_by:
                dbg["rebalance_px"] = tau_by[code]
            return dbg

        # 新方案直接用净收益门槛，不走 coerce（避免 0.5 被收成 0）
        raw_enter = (cfg or {}).get("fuse_enter")
        try:
            enter = float(raw_enter)
        except (TypeError, ValueError):
            enter = 0.005
        if enter != enter:
            enter = 0.005
        if enter > 0.05:
            enter = enter / 100.0
        enter = max(0.0, float(enter))
        plan = plan_topk_greedy_day(
            scored=enriched,
            holdings=list(holdings or []),
            cash=float(cash or 0),
            prices=px_plan,
            fuse_enter=float(enter),
            oo_rank_max=oo_rank_max,
            add_max=float(add_max),
            stock_cap=float(stock_cap),
            allowed_tiers=allowed_tiers,
            cut_tail=bool(cut_tail),
        )
        sells = []
        for s in plan.sells:
            code = str(s.get("stock_code") or "")
            sells.append(
                {
                    **s,
                    **_leg_scores(code),
                    "side": "sell",
                    "action": rl.ACTION_EXIT,
                    "matrix_action": rl.ACTION_EXIT,
                }
            )
        buys = []
        for b in plan.buys:
            code = str(b.get("stock_code") or "")
            held = any(
                str(h.get("stock_code") or "") == code
                and float(h.get("shares") or 0) > 0
                for h in (holdings or [])
                if isinstance(h, dict)
            )
            sold = any(str(s.get("stock_code") or "") == code for s in sells)
            act = rl.ACTION_ADD if held and not sold else rl.ACTION_OPEN
            buys.append(
                {
                    **b,
                    **_leg_scores(code),
                    "side": "buy",
                    "action": act,
                    "matrix_action": act,
                }
            )
        return {
            "sells": sells,
            "buys": buys,
            "holds": list(plan.holds),
            "skips": list(plan.skips),
            "cash_after_plan": plan.cash_after,
            "fuse_enter": enter,
            "oo_rank_max": oo_rank_max,
            "greedy": True,
            "candidate": list(plan.candidate),
            "notes": list(plan.notes),
        }

    return _adapter


def _run_portfolio(**kwargs) -> Dict[str, Any]:
    """临时 TopK：跳过会话日补今开（batch_get_quotes）与基准缺票补拉。"""
    from quant.services.quant_service import QuantService
    import core.backtest.paper_replay as pr

    kwargs.setdefault("include_benchmark", False)
    kwargs.setdefault("persist_curve", False)

    orig_overlay = pr.overlay_session_day_bars

    def _no_session_overlay(stock_bars, **_kw):
        return stock_bars, None, 0

    pr.overlay_session_day_bars = _no_session_overlay  # type: ignore[assignment]
    try:
        return QuantService().run_portfolio_backtest(**kwargs)
    finally:
        pr.overlay_session_day_bars = orig_overlay


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-baseline", action="store_true", help="现网用落盘指标，不重跑")
    ap.add_argument(
        "--greedy-only",
        action="store_true",
        help="只跑新方案，不跑也不展示现网",
    )
    ap.add_argument("--lookback", type=int, default=20, help="回测交易日数（默认 20）")
    ap.add_argument(
        "--oo-rank-max",
        "--greedy-top-k",
        type=int,
        default=38,
        dest="oo_rank_max",
        help="唯一 TopK：须 y_oo_rank < 此值（默认 38≈现网 top50%%）；≤0 关闭（--greedy-top-k 同义）",
    )
    ap.add_argument("--add-max", type=float, default=20_000.0)
    ap.add_argument("--stock-cap", type=float, default=50_000.0)
    ap.add_argument(
        "--initial-cash",
        type=float,
        default=200_000.0,
        help="回测账户本金（元；默认 20 万）",
    )
    ap.add_argument(
        "--fuse-enter",
        type=float,
        default=None,
        help="现网入场门槛（净收益）；默认跟 last 请求",
    )
    ap.add_argument(
        "--greedy-fuse-enter",
        type=float,
        default=0.5,
        help="新方案 fuse 入场（默认 0.5 表示 0.5%%；也可传净收益 0.005）",
    )
    ap.add_argument(
        "--no-cut-tail",
        action="store_true",
        help="关闭砍尾喂首（现金不够则跳过，不卖队尾）",
    )
    args = ap.parse_args()

    req = _load_last_request()
    last = _load_last_metrics()

    def _as_net_enter(raw: float, *, default: float = 0.005) -> float:
        """UI 常写 0.5 表示 0.5%%；避开 coerce 把 0.5 收成 0。"""
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return float(default)
        if v != v:
            return float(default)
        # 已是净收益量级（≤5%%）
        if abs(v) <= 0.05 + 1e-12:
            return max(0.0, float(v))
        # 展示百分点：0.5 → 0.005；5 → 0.05
        if abs(v) <= 50.0:
            return max(0.0, float(v) / 100.0)
        return max(0.0, float(v))

    greedy_enter = _as_net_enter(args.greedy_fuse_enter, default=0.005)
    baseline_enter = float(
        args.fuse_enter
        if args.fuse_enter is not None
        else (
            req.get("fuse_enter")
            if req.get("fuse_enter") is not None
            else 0.002
        )
    )
    # 现网仍走 coerce；若用户显式传 0.5 也按 0.5% 理解
    if args.fuse_enter is not None:
        baseline_enter = _as_net_enter(args.fuse_enter, default=baseline_enter)

    # 现网落盘优先（避免 last_portfolio_backtest 占位请求把配重冲成 0.6/0.4）
    live_lots: Dict[str, Any] = {}
    try:
        from core.paper import load_paper
        from core.paper.rebalance.path_matrix import get_path_matrix_cfg
        from core.paths import PAPER_PATH

        live_lots = get_path_matrix_cfg(paper=load_paper(PAPER_PATH))
    except Exception:  # noqa: BLE001
        live_lots = {}

    def _pick(key: str, default: Any, *, cast=float):
        if live_lots.get(key) is not None:
            try:
                return cast(live_lots[key])
            except (TypeError, ValueError):
                pass
        if req.get(key) is not None:
            try:
                return cast(req[key])
            except (TypeError, ValueError):
                pass
        return default

    common = {
        "lookback": int(args.lookback),
        "apply_costs": bool(req.get("apply_costs", True)),
        "exclude_st": bool(req.get("exclude_st", True)),
        "fusion_w_co": float(_pick("fusion_w_co", 1.0)),
        "fusion_w_oo": float(_pick("fusion_w_oo", 0.9)),
        "fusion_w_oc": float(_pick("fusion_w_oc", 0.1)),
        "fuse_enter": baseline_enter,
        "fuse_strong": float(_pick("fuse_strong", 0.012)),
        "y_oo_gt0": bool(_pick("y_oo_gt0", True, cast=bool)),
        "y_τc_gt0": bool(_pick("y_τc_gt0", True, cast=bool)),
        "oo_rank_top_frac": live_lots.get("oo_rank_top_frac", req.get("oo_rank_top_frac")),
        "initial_cash": float(args.initial_cash),
        "fill_clock": str(
            live_lots.get("fill_clock") or req.get("fill_clock") or "09:30"
        ),
        "lot_base_amount": float(_pick("lot_base_amount", 20_000.0)),
        "lot_strong_amount": float(_pick("lot_strong_amount", 50_000.0)),
        "use_predictability_tiers": True,
        "predictability_tiers": ["A"],
        "price_space_gate": bool(req.get("price_space_gate", True)),
        # 临时烟测固定 research，避免 last 落盘成 live 把结果冲偏
        "score_model_role": "research",
        "score_backend": str(
            live_lots.get("score_backend") or req.get("score_backend") or "ridge"
        ),
        # 临时烟测：不挂基准、不补拉远端行情
        "include_benchmark": False,
        "benchmark_code": str(req.get("benchmark_code") or "pool"),
    }
    # 新方案手数上界跟现网强档金额对齐（add_max=base，stock_cap=strong）
    if args.add_max == 20_000.0:
        args.add_max = float(common["lot_base_amount"])
    if args.stock_cap == 50_000.0:
        args.stock_cap = float(common["lot_strong_amount"])

    print("对齐 last_portfolio_backtest 请求（lookback/新方案入场可覆盖）：")
    print(
        json.dumps(
            {
                "lookback": int(args.lookback),
                "baseline_fuse_enter": baseline_enter,
                "greedy_fuse_enter": greedy_enter,
                "greedy_fuse_enter_display": f"{greedy_enter * 100:g}%",
                "initial_cash": common["initial_cash"],
                "lot_base_amount": common["lot_base_amount"],
                "lot_strong_amount": common["lot_strong_amount"],
                "add_max": args.add_max,
                "stock_cap": args.stock_cap,
                "oo_rank_max": int(args.oo_rank_max),
                "cut_tail": not bool(args.no_cut_tail),
                "fusion_w_oo": common["fusion_w_oo"],
                "fusion_w_oc": common["fusion_w_oc"],
                "fusion_w_co": common["fusion_w_co"],
                "use_predictability_tiers": common["use_predictability_tiers"],
                "fill_clock": common["fill_clock"],
                "score_model_role": common["score_model_role"],
                "y_oo_gt0": common["y_oo_gt0"],
                "y_τc_gt0": common["y_τc_gt0"],
                "oo_rank_top_frac": common["oo_rank_top_frac"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    rows: List[Dict[str, Any]] = []

    def _progress(msg: str, cur: int = 0, tot: int = 1) -> None:
        print(f"  [{cur}/{tot}] {msg}", flush=True)

    # —— 现网（可跳过）——
    if not args.greedy_only:
        if args.skip_baseline and last.get("metrics"):
            base = {
                "success": True,
                "metrics": last["metrics"],
                "params": last.get("params") or {},
                "sim_trades": [],
                "equity_curve": [],
            }
            print(f"\n现网：使用落盘指标（saved_at={last.get('saved_at')}），不重跑")
        else:
            print("\n现网：重跑 rank_lots …", flush=True)
            t0 = time.time()
            base = _run_portfolio(progress_cb=_progress, **common)
            print(
                f"现网耗时 {time.time() - t0:.1f}s ok={base.get('success')} "
                f"err={base.get('error')}"
            )
        rows.append(_summarize("现网 rank_lots", base))

    # —— 新方案：打补丁替换日计划 ——
    oo_max = int(args.oo_rank_max)
    cut_on = not bool(args.no_cut_tail)
    print(
        f"\n新方案：greedy 按 oo_rank 一天一笔 + "
        f"{'不够砍尾' if cut_on else '砍尾关'} "
        f"TopK=oo_rank<{oo_max if oo_max > 0 else '关'} "
        f"add_max={args.add_max:g} stock_cap={args.stock_cap:g} "
        f"fuse_enter={greedy_enter*100:g}% lookback={args.lookback} …",
        flush=True,
    )
    import core.paper.rebalance.rank_lots as rl_mod

    original = rl_mod.plan_rank_lot_day
    rl_mod.plan_rank_lot_day = _make_greedy_adapter(
        add_max=args.add_max,
        stock_cap=args.stock_cap,
        # 宇宙已按枢纽 A 档过滤；日计划不再二次踢档，避免缺字段误杀
        allowed_tiers=None,
        oo_rank_max=oo_max if oo_max > 0 else None,
        cut_tail=cut_on,
    )
    greedy_common = dict(common)
    greedy_common["fuse_enter"] = greedy_enter
    # 新方案入场单一门槛；强档跟进入场，避免 fuse_strong < enter 被抬
    greedy_common["fuse_strong"] = max(
        float(greedy_common.get("fuse_strong") or 0), greedy_enter
    )
    # 新方案门槛：y_oo/y_τc>0；宇宙 A 档（落盘若是占位请求则强制打开）
    greedy_common["y_oo_gt0"] = True
    greedy_common["y_τc_gt0"] = True
    greedy_common["use_predictability_tiers"] = True
    greedy_common["predictability_tiers"] = ["A"]
    try:
        t1 = time.time()
        new = _run_portfolio(progress_cb=_progress, **greedy_common)
        print(
            f"新方案耗时 {time.time() - t1:.1f}s ok={new.get('success')} "
            f"err={new.get('error')}"
        )
    finally:
        rl_mod.plan_rank_lot_day = original

    rows.append(_summarize("新 topK贪心", new))

    title = "新方案结果（不落盘）" if args.greedy_only else "对比（同宇宙/同打分/同本金；不落盘）"
    print(f"\n===== {title} =====")
    _print_table(rows)
    if (
        not args.greedy_only
        and len(rows) >= 2
        and rows[0].get("total_return_pct") is not None
        and rows[1].get("total_return_pct") is not None
    ):
        dret = float(rows[1]["total_return_pct"]) - float(rows[0]["total_return_pct"])
        print(f"\nΔ收益 = 新 − 现网 = {dret:+.2f}%")
    if not args.greedy_only and base.get("success") and (base.get("sim_trades") or []):
        _print_edge_diagnostics("现网 rank_lots", base)
    if new.get("success"):
        m = new.get("metrics") or {}
        print(
            "摘要:",
            f"收益={m.get('total_return_pct')}%",
            f"回撤={m.get('max_drawdown_pct')}%",
            f"成交={m.get('trade_count') or len(new.get('sim_trades') or [])}",
            f"命中n={m.get('hit_n')}",
            f"命中率={m.get('hit_rate_pct')}",
        )
        note = (new.get("params") or {}).get("note") or new.get("note")
        if note:
            print("新方案 note:", str(note)[:240])
        _print_edge_diagnostics("新 topK贪心", new)
    else:
        print("新方案失败:", new.get("error") or new)


if __name__ == "__main__":
    main()
