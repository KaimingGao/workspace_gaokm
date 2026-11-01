"""形态闸长窗开/关与参数网格扫描（不落盘默认；只出表）。

用法::

  PYTHONUNBUFFERED=1 PYTHONPATH=. python scripts/run_morph_gate_scan.py

环境变量::

  MORPH_SCAN_LOOKBACK   默认 120
  MORPH_SCAN_LIMIT      股票数上限，默认 40
  MORPH_SCAN_QUICK      1=只跑关/开默认 + gap=3% 三组

结果打印到 stdout，并写 data/reports/morph_gate_scan.json（若目录可写）。
选参原则：回撤/尾部变好且换手不过度；默认 gap/vol_min 仍保持 0，除非本表明确更优。
"""

from __future__ import annotations

import json
import os
import sys
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _log(msg: str) -> None:
    print(msg, flush=True)


def _metrics(res: Dict[str, Any]) -> Dict[str, Any]:
    m = res.get("metrics") if isinstance(res.get("metrics"), dict) else {}
    cons = res.get("constraints_hit") if isinstance(res.get("constraints_hit"), dict) else {}
    params = res.get("params") if isinstance(res.get("params"), dict) else {}
    eq = res.get("equity_curve") or []
    navs = []
    for row in eq:
        if isinstance(row, dict) and row.get("nav") is not None:
            try:
                navs.append(float(row["nav"]))
            except (TypeError, ValueError):
                pass
    max_dd = None
    if navs:
        peak = navs[0]
        dd = 0.0
        for n in navs:
            peak = max(peak, n)
            if peak > 0:
                dd = min(dd, n / peak - 1.0)
        max_dd = round(dd * 100.0, 3)
    ret = None
    if navs and navs[0]:
        ret = round((navs[-1] / navs[0] - 1.0) * 100.0, 3)
    return {
        "success": bool(res.get("success")),
        "error": res.get("error"),
        "total_return_pct": ret if ret is not None else m.get("total_return_pct"),
        "max_drawdown_pct": max_dd if max_dd is not None else m.get("max_drawdown_pct"),
        "rebalance_days": cons.get("rebalance_days"),
        "morph_skips": cons.get("morph_skips"),
        "morph_gap_skips": cons.get("morph_gap_skips"),
        "morph_hv_skips": cons.get("morph_hv_skips"),
        "morph_stamp": params.get("morph_stamp"),
        "n_trades": res.get("sim_trade_count") or len(res.get("trades") or []),
    }


def _grid(quick: bool) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = [
        {"label": "off", "morph_gate_enabled": False},
        {
            "label": "on_default",
            "morph_gate_enabled": True,
            "morph_vol_pctl": 0.9,
            "morph_a1_max": 0.0,
            "morph_gap_max": 0.0,
            "morph_vol20_min": 0.0,
        },
    ]
    if quick:
        rows.append(
            {
                "label": "on_gap3",
                "morph_gate_enabled": True,
                "morph_vol_pctl": 0.9,
                "morph_a1_max": 0.0,
                "morph_gap_max": 0.03,
                "morph_vol20_min": 0.0,
            }
        )
        return rows
    for vp, a1 in product((0.85, 0.9, 0.95), (0.0, -0.01, -0.02)):
        rows.append(
            {
                "label": f"hv_p{vp:g}_a{a1:g}",
                "morph_gate_enabled": True,
                "morph_vol_pctl": vp,
                "morph_a1_max": a1,
                "morph_gap_max": 0.0,
                "morph_vol20_min": 0.0,
            }
        )
    for gap in (0.02, 0.03, 0.05):
        rows.append(
            {
                "label": f"gap_{int(gap * 100)}",
                "morph_gate_enabled": True,
                "morph_vol_pctl": 0.9,
                "morph_a1_max": 0.0,
                "morph_gap_max": gap,
                "morph_vol20_min": 0.0,
            }
        )
    return rows


def main() -> int:
    from core.backtest.paper_replay import backtest_paper_replay
    from quant.research.portfolio_data import load_portfolio_stock_bars
    from core.watching.store import read_watching

    lookback = int(os.environ.get("MORPH_SCAN_LOOKBACK", "120"))
    limit = int(os.environ.get("MORPH_SCAN_LIMIT", "40"))
    quick = str(os.environ.get("MORPH_SCAN_QUICK", "1")).strip() not in (
        "0",
        "false",
        "False",
        "",
    )

    uni = read_watching()
    pool = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    limit = max(8, min(limit, len(pool) or limit))
    _log(f"观察池 {len(pool)} · 用前 {limit} · lookback={lookback} · quick={quick}")
    if len(pool) < 8:
        _log("ERROR: 观察池不足 8")
        return 1

    loaded, _fail, _fund = load_portfolio_stock_bars(
        pool[:limit],
        lookback=lookback,
        fetch_fundamentals=False,
        offline_ok=True,
    )
    stock_bars = {
        str(c): bars
        for c, bars in (loaded or {}).items()
        if isinstance(bars, list) and len(bars) >= 30
    }
    _log(f"有效日线 {len(stock_bars)}")
    if len(stock_bars) < 8:
        _log("ERROR: 有效股票不足 8")
        return 1

    out_rows: List[Dict[str, Any]] = []
    for spec in _grid(quick):
        label = str(spec.pop("label"))
        _log(f"… {label}")
        try:
            res = backtest_paper_replay(
                stock_bars,
                top_k=len(stock_bars),
                lookback=lookback,
                cost_model="simple_cn",
                initial_cash=200_000.0,
                cash_floor=0.0,
                fill_clock="09:30",
                fuse_enter=0.001,
                fuse_strong=0.001,
                **spec,
            )
        except Exception as e:  # noqa: BLE001
            _log(f"  FAIL {label}: {e}")
            out_rows.append({"label": label, "success": False, "error": str(e), **spec})
            continue
        row = {"label": label, **spec, **_metrics(res if isinstance(res, dict) else {})}
        out_rows.append(row)
        _log(
            f"  ret={row.get('total_return_pct')} dd={row.get('max_drawdown_pct')} "
            f"morph_skips={row.get('morph_skips')} "
            f"gap/hv={row.get('morph_gap_skips')}/{row.get('morph_hv_skips')} "
            f"stamp={row.get('morph_stamp')}"
        )

    payload = {
        "lookback": lookback,
        "n_codes": len(stock_bars),
        "quick": quick,
        "note": (
            "默认 morph_gap_max/morph_vol20_min 保持 0，除非本表相对 off 明确更优；"
            "勿仅因单日 10-29 改默认。"
        ),
        "rows": out_rows,
    }
    rep = ROOT / "data" / "reports" / "morph_gate_scan.json"
    try:
        rep.parent.mkdir(parents=True, exist_ok=True)
        rep.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(f"已写 {rep}")
    except OSError as e:
        _log(f"写报告失败（忽略）: {e}")

    # 简表
    _log("\nlabel\tret%\tdd%\tmorph_skips\tgap\thv")
    for r in out_rows:
        _log(
            f"{r.get('label')}\t{r.get('total_return_pct')}\t{r.get('max_drawdown_pct')}\t"
            f"{r.get('morph_skips')}\t{r.get('morph_gap_skips')}\t{r.get('morph_hv_skips')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
