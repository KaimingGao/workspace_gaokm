"""仪表盘：核心 KPI 构建。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from web import deps
from web.dashboard.paper_helpers import (
    _equity_curve_from_paper,
    _load_raw_paper,
    _north_star_from_paper,
    _trades_from_paper,
)


def _build_kpis() -> Dict[str, Any]:
    """聚合核心 KPI：收益与交易执行页同源（盯市）；曲线/夏普仍用 snapshots。"""
    paper = _load_raw_paper()
    ns_report = _north_star_from_paper(paper)

    pr = (ns_report or {}).get("paper_risk") or {}
    risk_strategy = (ns_report or {}).get("paper_risk_strategy") or {}

    today_ret = None
    total_ret = None
    sharpe = None
    max_dd = None
    win_rate = None
    trade_count = 0
    period = "相对本金"
    live_summary: Optional[Dict[str, Any]] = None

    # 今日 / 累计：与 /follow 交易执行页同源（mark_to_market）
    # 避免仪表盘只读陈旧 snapshots，回零后基线已换但曲线未续写时出现两页不一致。
    try:
        from core.paper import mark_to_market

        live_summary = mark_to_market(paper) if paper else None
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        live_summary = None
    if isinstance(live_summary, dict):
        tp = live_summary.get("today_pnl_pct")
        if tp is not None:
            try:
                today_ret = float(tp)
            except (TypeError, ValueError):
                pass
        tot = live_summary.get("total_pnl_pct")
        if tot is not None:
            try:
                total_ret = float(tot)
            except (TypeError, ValueError):
                pass

    # Today / Total return fallback：snapshots 曲线（无行情或盯市失败时）
    eq_curve = _equity_curve_from_paper(paper)
    if today_ret is None and eq_curve and len(eq_curve) >= 2:
        today_ret = eq_curve[-1].get("return_pct")
        if today_ret is None and eq_curve[-1].get("equity") and eq_curve[-2].get("equity"):
            e0 = eq_curve[-2]["equity"]
            e1 = eq_curve[-1]["equity"]
            if e0 and e0 > 0:
                today_ret = (e1 / e0 - 1) * 100
    if total_ret is None and eq_curve and len(eq_curve) >= 1:
        # 优先相对回零本金 initial_cash，与交易执行 total_pnl_pct 口径一致
        initial = paper.get("initial_cash")
        last_eq = eq_curve[-1].get("equity")
        try:
            initial_f = float(initial) if initial is not None else None
            last_f = float(last_eq) if last_eq is not None else None
        except (TypeError, ValueError):
            initial_f = None
            last_f = None
        if initial_f and initial_f > 0 and last_f is not None:
            total_ret = (last_f / initial_f - 1) * 100
        else:
            first_eq = eq_curve[0].get("equity")
            if first_eq and first_eq > 0 and last_eq:
                total_ret = (last_eq / first_eq - 1) * 100
                period = "历史累计"
    elif total_ret is not None and paper.get("initial_cash") is not None:
        period = "相对回零基线"
    # Sharpe / MaxDD from north_star；短样本时本地兜底，避免整卡「—」
    sharpe = pr.get("rolling_sharpe") or pr.get("sharpe") or risk_strategy.get("rolling_sharpe")
    max_dd = pr.get("max_drawdown_pct") or pr.get("max_drawdown") or risk_strategy.get("max_drawdown_pct")

    rets_pct: List[float] = []
    if eq_curve:
        peak = None
        local_max_dd = 0.0
        for e in eq_curve:
            v = e.get("equity")
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if peak is None or v > peak:
                peak = v
            if peak and peak > 0:
                local_max_dd = max(local_max_dd, (peak - v) / peak * 100.0)
        if max_dd is None:
            max_dd = local_max_dd
        for i in range(1, len(eq_curve)):
            e0 = eq_curve[i - 1].get("equity")
            e1 = eq_curve[i].get("equity")
            if e0 and e0 > 0 and e1 is not None:
                try:
                    rets_pct.append((float(e1) / float(e0) - 1) * 100.0)
                except (TypeError, ValueError):
                    pass
        if sharpe is None and len(rets_pct) >= 2:
            mean = sum(rets_pct) / len(rets_pct)
            var = sum((x - mean) ** 2 for x in rets_pct) / max(len(rets_pct) - 1, 1)
            std = var ** 0.5
            if std > 1e-9:
                unique_days = len({e.get("date") for e in eq_curve if e.get("date")})
                # 同日快照不做 252 年化，避免 Sharpe 夸张失真
                scale = (252 ** 0.5) if unique_days > 1 else 1.0
                sharpe = (mean / std) * scale

    # Win rate：优先已实现卖出盈亏；否则用净值期收益胜率
    trades = _trades_from_paper(paper)
    closed = [t for t in trades if t.get("side") == "sell" and t.get("pnl") is not None]
    trade_count = len(closed)
    if closed:
        wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        win_rate = (wins / len(closed)) * 100
    elif rets_pct:
        trade_count = len(rets_pct)
        wins = sum(1 for r in rets_pct if r > 0)
        win_rate = (wins / len(rets_pct)) * 100

    # Sparkline data (simple: last 30 days of equity-derived values)
    spark_today: List[float] = []
    spark_total: List[float] = []
    spark_sharpe: List[float] = []
    spark_dd: List[float] = []
    spark_wr: List[float] = []

    if eq_curve:
        rets = []
        for i in range(1, len(eq_curve)):
            e0 = eq_curve[i - 1].get("equity")
            e1 = eq_curve[i].get("equity")
            if e0 and e0 > 0 and e1:
                rets.append((e1 / e0 - 1) * 100)
        # Last 30 daily returns
        spark_today = rets[-30:] if rets else []
        # Cumulative equity normalized
        if eq_curve and eq_curve[0].get("equity"):
            base = eq_curve[0]["equity"]
            spark_total = [((e.get("equity", base) / base) - 1) * 100 for e in eq_curve[-30:]]
        # Rolling Sharpe (simplified)；短样本也给一条可读 spark
        if len(rets) >= 3:
            win_n = 7 if len(rets) >= 7 else len(rets)
            for i in range(win_n - 1, len(rets)):
                window = rets[max(0, i - win_n + 1): i + 1]
                if window:
                    mean = sum(window) / len(window)
                    std = (sum((x - mean) ** 2 for x in window) / len(window)) ** 0.5
                    if std > 0:
                        spark_sharpe.append(mean / std * (252 ** 0.5))
        # Drawdown series
        if eq_curve:
            peak = eq_curve[0].get("equity", 1.0)
            for e in eq_curve[-30:]:
                v = e.get("equity", peak)
                if v > peak:
                    peak = v
                if peak > 0:
                    spark_dd.append((v / peak - 1) * 100)
        # Win rate rolling
        if len(closed) >= 5:
            window = closed[-20:]
            wins = sum(1 for t in window if (t.get("pnl") or 0) > 0)
            spark_wr = [wins / len(window) * 100] * 5 if window else []
        elif rets_pct:
            wins = sum(1 for r in rets_pct if r > 0)
            spark_wr = [wins / len(rets_pct) * 100] * min(5, len(rets_pct))

    # Strategy leaderboard：登记策略缺绩效时，用当前纸面累计收益填一条
    strategies: List[Dict[str, Any]] = []
    try:
        strat_list = deps.quant.list_strategies()
        if isinstance(strat_list, dict):
            strat_list = strat_list.get("strategies") or strat_list.get("list") or []
        if isinstance(strat_list, list):
            for s in strat_list[:10]:
                if isinstance(s, dict):
                    label = s.get("label") or s.get("name") or s.get("strategy_id") or "未知策略"
                    desc = s.get("description") or ""
                    strategies.append({
                        "name": f"{label}" + (f" · {desc[:24]}" if desc else ""),
                        "return_pct": s.get("total_return_pct") or s.get("return_pct") or 0,
                        "sharpe": s.get("sharpe") or s.get("rolling_sharpe") or 0,
                    })
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        pass
    if strategies and total_ret is not None:
        # 当前策略卡用纸面累计收益/本地 sharpe 覆盖全 0
        sid = str(paper.get("strategy_id") or "")
        for s in strategies:
            if (s.get("return_pct") or 0) == 0 and (s.get("sharpe") or 0) == 0:
                # 仅给首条或名称匹配当前策略的卡填纸面数
                name = str(s.get("name") or "")
                if not sid or sid in name or s is strategies[0]:
                    s["return_pct"] = round(float(total_ret), 2)
                    if sharpe is not None:
                        s["sharpe"] = round(float(sharpe), 2)
                    break
    elif not strategies and total_ret is not None:
        strategies.append({
            "name": str(paper.get("strategy_id") or "纸面组合"),
            "return_pct": round(float(total_ret), 2),
            "sharpe": round(float(sharpe), 2) if sharpe is not None else 0,
        })

    return {
        "ok": True,
        "today_return": round(today_ret, 2) if today_ret is not None else None,
        "total_return": round(total_ret, 2) if total_ret is not None else None,
        "today_return_basis": (
            live_summary.get("today_pnl_basis") if isinstance(live_summary, dict) else None
        ),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "max_drawdown": round(max_dd, 2) if max_dd is not None else None,
        "win_rate": round(win_rate, 2) if win_rate is not None else None,
        "trade_count": trade_count,
        "period": period,
        "sparkline_today": spark_today,
        "sparkline_total": spark_total,
        "sparkline_sharpe": spark_sharpe[-30:],
        "sparkline_dd": spark_dd[-30:],
        "sparkline_winrate": spark_wr,
        "strategies": strategies,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }

