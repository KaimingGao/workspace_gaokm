"""分组 OLS 研究面板：日线 +（可选）PIT 财务探针。

``pit_fundamentals=True`` 时：
- 面板附带「末日 as_of」财务 metrics（供组 IC / 展示）；
- 逐决策日 PIT 仍由 ``collect_subscore_forward_panel`` 按日 resolve。
``False`` 时：不注入财务，``lookahead_flags.fundamentals=none``。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def _last_bar_date(bars: Sequence[dict]) -> Optional[str]:
    if not bars:
        return None
    d = str((bars[-1] or {}).get("date") or "")[:10]
    return d or None


def build_cluster_ols_panels(
    codes: Sequence[str],
    *,
    lookback: int = 80,
    pit_fundamentals: bool = True,
    code_roles: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建分组 OLS 输入面板，并汇总财务 PIT 覆盖。"""
    from core.data_service import bars_and_source, get_quote
    from core.fundamentals_pit import (
        fundamentals_pit_summary,
        resolve_fundamentals_for_score,
    )
    from core.ports.market import default_benchmark, fetch_index_bars, resolve_market_code
    from core.signal.config import load_signal_config

    use_pit = bool(pit_fundamentals)
    roles = dict(code_roles or {})
    fund_cfg = (load_signal_config() or {}).get("fundamentals") or {}

    index_cache: Dict[str, Any] = {}
    panels: List[Dict[str, Any]] = []
    bars_by_code: Dict[str, Any] = {}
    quotes_by_code: Dict[str, Any] = {}
    fund_resolves: List[Dict[str, Any]] = []

    resolved_codes: List[str] = []
    resolved_seen: set = set()
    watching_resolved: List[str] = []
    holdings_resolved: List[str] = []
    holdings_added_resolved: List[str] = []

    for code in codes:
        role = roles.get(code) or {}
        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        sym_s = str(sym)
        if quote.get("success"):
            quotes_by_code[sym_s] = quote
        if sym_s not in resolved_seen:
            resolved_seen.add(sym_s)
            resolved_codes.append(sym_s)
        if role.get("from_watching") and sym_s not in watching_resolved:
            watching_resolved.append(sym_s)
        if role.get("from_holdings") and sym_s not in holdings_resolved:
            holdings_resolved.append(sym_s)
        if role.get("holdings_added") and sym_s not in holdings_added_resolved:
            holdings_added_resolved.append(sym_s)

        bars, src = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = bars_and_source(sym, limit=lookback + 35)
        if not bars:
            panels.append(
                {
                    "code": sym_s,
                    "bars": [],
                    "fundamentals": None,
                    "fundamentals_mode": "empty_bars",
                }
            )
            continue

        bars_by_code[sym_s] = bars
        market, _ = resolve_market_code(code)
        bench = default_benchmark(market)
        if bench not in index_cache:
            index_cache[bench], _ = fetch_index_bars(bench, limit=lookback + 35)

        fund_metrics = None
        fund_mode = "none"
        as_of = _last_bar_date(bars)
        if use_pit and as_of:
            try:
                resolved = resolve_fundamentals_for_score(
                    sym_s,
                    as_of=as_of,
                    fund_cfg=fund_cfg,
                    live_fallback=False,
                )
                fund_resolves.append(resolved)
                if resolved.get("ok") and resolved.get("metrics"):
                    fund_metrics = dict(resolved["metrics"])
                    fund_mode = "pit_as_of"
                else:
                    fund_mode = str(resolved.get("mode") or "as_of_missing")
            except Exception as e:
                fund_resolves.append(
                    {
                        "ok": False,
                        "metrics": None,
                        "fundamentals_pit": True,
                        "mode": "error",
                        "error": str(e)[:120],
                    }
                )
                fund_mode = "error"
        elif use_pit:
            fund_mode = "as_of_missing"
        else:
            fund_mode = "none"

        panels.append(
            {
                "code": sym_s,
                "bars": bars,
                "index_bars": index_cache.get(bench) or None,
                # PIT 开：末日 as_of 探针；逐日仍由 research panel 再 resolve
                "fundamentals": fund_metrics,
                "fundamentals_mode": fund_mode,
                "fundamentals_as_of": as_of if use_pit else None,
                "data_source": src,
            }
        )

    pit_summary = fundamentals_pit_summary(fund_resolves) if use_pit else {
        "fundamentals_pit": False,
        "sample_count": 0,
        "note": "未启用 PIT 财务。",
    }
    if use_pit:
        if pit_summary.get("resolved_ok", 0) > 0:
            fund_label = "pit_as_of"
            note = (
                f"财务按决策日 as_of 解析；末日探针 ok="
                f"{pit_summary.get('resolved_ok')}/{pit_summary.get('sample_count')}；"
                f"逐日拟合走 collect_subscore_forward_panel PIT。"
            )
        else:
            fund_label = "pit_as_of_missing"
            note = (
                "已启用 PIT，但末日探针无可用财务点（拒未来快照）；"
                "财务因子按缺失/中性处理。"
            )
    else:
        fund_label = "none"
        note = "非 PIT · 未注入财务面板 · 勿当实盘证据。"

    lookahead_flags = {
        "pit_fundamentals": use_pit,
        "fundamentals": fund_label,
        "pit_summary": pit_summary,
        "note": note,
    }

    return {
        "panels": panels,
        "bars_by_code": bars_by_code,
        "quotes_by_code": quotes_by_code,
        "resolved_codes": resolved_codes,
        "watching_resolved": watching_resolved,
        "holdings_resolved": holdings_resolved,
        "holdings_added_resolved": holdings_added_resolved,
        "fund_resolves": fund_resolves,
        "lookahead_flags": lookahead_flags,
        "pit_fundamentals": use_pit,
    }
