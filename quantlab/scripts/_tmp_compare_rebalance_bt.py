"""离线临时对比：现网 rank_lots vs 新 topK 贪心（先花光再砍尾）。

对齐最近一次 ``last_portfolio_backtest`` 请求参数；``persist_curve=False`` 不落盘。
宇宙仍走观察池分档（``use_predictability_tiers=True``）。

用法（在 quantlab/ 下）::

    .venv/bin/python scripts/_tmp_compare_rebalance_bt.py
    .venv/bin/python scripts/_tmp_compare_rebalance_bt.py --skip-baseline  # 只用落盘现网指标
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, ".")

from scripts._tmp_topk_greedy_offline import plan_topk_greedy_day  # noqa: E402


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
        "rank_enter": p.get("rank_enter"),
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
    greedy_top_k: int,
    add_max: float,
    stock_cap: float,
    allowed_tiers: Optional[Sequence[str]],
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
            rp = rl.ranking_pct_of(row, cfg, open_px=o, price_tau=p)
            row["_ranking_pct"] = rp
            if rp is not None:
                row["ranking"] = float(rp) / 100.0
            if row.get("predictability_tier") in (None, "") and allowed_tiers:
                row["predictability_tier"] = str(list(allowed_tiers)[0])
            enriched.append(row)
            item_by[code] = row

        # 持仓缺分行也要能挂 day_open / ranking，否则清仓腿进不了命中率分母
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
            rp = rl.ranking_pct_of(row, cfg, open_px=row.get("day_open"), price_tau=p)
            row["_ranking_pct"] = rp
            if rp is not None:
                row["ranking"] = float(rp) / 100.0
            item_by[code] = row
            enriched.append(row)

        w_fields = rl._fusion_weight_fields(cfg)

        def _leg_scores(code: str) -> Dict[str, Any]:
            item = item_by.get(code) or {"stock_code": code}
            rp = item.get("_ranking_pct")
            if rp is None:
                rp = rl.ranking_pct_of(
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
        raw_enter = (cfg or {}).get("rank_enter")
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
            rank_enter=float(enter),
            top_k=int(greedy_top_k),
            add_max=float(add_max),
            stock_cap=float(stock_cap),
            allowed_tiers=allowed_tiers,
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
            "rank_enter": enter,
            "top_k": int(greedy_top_k),
            "greedy": True,
            "candidate": list(plan.candidate),
            "notes": list(plan.notes),
        }

    return _adapter


def _run_portfolio(**kwargs) -> Dict[str, Any]:
    from quant.services.quant_service import QuantService

    svc = QuantService()
    return svc.run_portfolio_backtest(persist_curve=False, **kwargs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-baseline", action="store_true", help="现网用落盘指标，不重跑")
    ap.add_argument("--lookback", type=int, default=20, help="回测交易日数（默认 20）")
    ap.add_argument("--greedy-top-k", type=int, default=30)
    ap.add_argument("--add-max", type=float, default=20_000.0)
    ap.add_argument("--stock-cap", type=float, default=50_000.0)
    ap.add_argument(
        "--rank-enter",
        type=float,
        default=None,
        help="现网入场门槛（净收益）；默认跟 last 请求",
    )
    ap.add_argument(
        "--greedy-rank-enter",
        type=float,
        default=0.5,
        help="新方案 ranking 入场（默认 0.5 表示 0.5%%；也可传净收益 0.005）",
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

    greedy_enter = _as_net_enter(args.greedy_rank_enter, default=0.005)
    baseline_enter = float(
        args.rank_enter
        if args.rank_enter is not None
        else (
            req.get("rank_enter")
            if req.get("rank_enter") is not None
            else 0.002
        )
    )
    # 现网仍走 coerce；若用户显式传 0.5 也按 0.5% 理解
    if args.rank_enter is not None:
        baseline_enter = _as_net_enter(args.rank_enter, default=baseline_enter)

    print("对齐 last_portfolio_backtest 请求（lookback/新方案入场可覆盖）：")
    print(
        json.dumps(
            {
                "lookback": int(args.lookback),
                "baseline_rank_enter": baseline_enter,
                "greedy_rank_enter": greedy_enter,
                "greedy_rank_enter_display": f"{greedy_enter * 100:g}%",
                "initial_cash": req.get("initial_cash"),
                "lot_base_amount": req.get("lot_base_amount"),
                "lot_strong_amount": req.get("lot_strong_amount"),
                "fusion_w_oo": req.get("fusion_w_oo"),
                "fusion_w_oc": req.get("fusion_w_oc"),
                "fusion_w_co": req.get("fusion_w_co"),
                "use_predictability_tiers": req.get("use_predictability_tiers"),
                "fill_clock": req.get("fill_clock"),
                "score_model_role": req.get("score_model_role"),
                "y_oo_gt0": req.get("y_oo_gt0"),
                "y_τc_gt0": req.get("y_τc_gt0"),
                "oo_rank_top_frac": req.get("oo_rank_top_frac"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    common = {
        "lookback": int(args.lookback),
        "apply_costs": bool(req.get("apply_costs", True)),
        "exclude_st": bool(req.get("exclude_st", True)),
        "fusion_w_co": float(req.get("fusion_w_co") or 1.0),
        "fusion_w_oo": float(req.get("fusion_w_oo") or 0.9),
        "fusion_w_oc": float(req.get("fusion_w_oc") or 0.1),
        "rank_enter": baseline_enter,
        "rank_strong": float(req.get("rank_strong") or 0.01),
        "rank_enter_alt": req.get("rank_enter_alt"),
        "y_enter_enabled": bool(req.get("y_enter_enabled", True)),
        "y_enter_alt_enabled": bool(req.get("y_enter_alt_enabled", True)),
        "y_oo_gt0": bool(req.get("y_oo_gt0", True)),
        "y_τc_gt0": bool(req.get("y_τc_gt0", True)),
        "oo_rank_top_frac": req.get("oo_rank_top_frac"),
        "initial_cash": float(req.get("initial_cash") or 200_000.0),
        "fill_clock": str(req.get("fill_clock") or "09:30"),
        "lot_base_amount": float(req.get("lot_base_amount") or 20_000.0),
        "lot_strong_amount": float(req.get("lot_strong_amount") or 50_000.0),
        "use_predictability_tiers": bool(req.get("use_predictability_tiers", True)),
        "predictability_tiers": ["A"],
        "price_space_gate": bool(req.get("price_space_gate", True)),
        "score_model_role": req.get("score_model_role") or "research",
        "score_backend": req.get("score_backend") or "ridge",
        "include_benchmark": True,
        "benchmark_code": str(req.get("benchmark_code") or "pool"),
    }

    rows: List[Dict[str, Any]] = []

    def _progress(msg: str, cur: int = 0, tot: int = 1) -> None:
        print(f"  [{cur}/{tot}] {msg}", flush=True)

    # —— 现网 ——
    if args.skip_baseline and last.get("metrics"):
        base = {
            "success": True,
            "metrics": last["metrics"],
            "params": last.get("params") or {},
            "sim_trades": [],
            "equity_curve": [],
        }
        # 落盘里有 trade_count；补齐
        print(f"\n现网：使用落盘指标（saved_at={last.get('saved_at')}），不重跑")
    else:
        print("\n现网：重跑 rank_lots …", flush=True)
        t0 = time.time()
        base = _run_portfolio(progress_cb=_progress, **common)
        print(f"现网耗时 {time.time() - t0:.1f}s ok={base.get('success')} err={base.get('error')}")
    rows.append(_summarize("现网 rank_lots", base))

    # —— 新方案：打补丁替换日计划 ——
    print(
        f"\n新方案：greedy top_k={args.greedy_top_k} add_max={args.add_max:g} "
        f"stock_cap={args.stock_cap:g} rank_enter={greedy_enter*100:g}% "
        f"lookback={args.lookback} …",
        flush=True,
    )
    import core.paper.rebalance.rank_lots as rl_mod

    original = rl_mod.plan_rank_lot_day
    rl_mod.plan_rank_lot_day = _make_greedy_adapter(
        greedy_top_k=args.greedy_top_k,
        add_max=args.add_max,
        stock_cap=args.stock_cap,
        # 宇宙已按枢纽 A 档过滤；日计划不再二次踢档，避免缺字段误杀
        allowed_tiers=None,
    )
    greedy_common = dict(common)
    greedy_common["rank_enter"] = greedy_enter
    # 新方案入场单一门槛；强档跟进入场，避免 rank_strong < enter 被抬
    greedy_common["rank_strong"] = max(
        float(greedy_common.get("rank_strong") or 0), greedy_enter
    )
    try:
        t1 = time.time()
        new = _run_portfolio(progress_cb=_progress, **greedy_common)
        print(f"新方案耗时 {time.time() - t1:.1f}s ok={new.get('success')} err={new.get('error')}")
    finally:
        rl_mod.plan_rank_lot_day = original

    rows.append(_summarize("新 topK贪心", new))

    print("\n===== 对比（同宇宙/同打分/同本金；不落盘）=====")
    _print_table(rows)
    if rows[0].get("total_return_pct") is not None and rows[1].get("total_return_pct") is not None:
        dret = float(rows[1]["total_return_pct"]) - float(rows[0]["total_return_pct"])
        print(f"\nΔ收益 = 新 − 现网 = {dret:+.2f}%")
    if new.get("success"):
        note = (new.get("params") or {}).get("note") or new.get("note")
        if note:
            print("新方案 note:", str(note)[:240])


if __name__ == "__main__":
    main()
