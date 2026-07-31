"""R5.6 · 产品核心黄金路径（北极星 KPI · PIT · 调仓硬拦）。

离线可跑，不依赖 LLM / 外网。供 CI 与 `evals/run_checklist.py --core-paths`。
"""

from __future__ import annotations

from typing import Any, Dict, List


def path_north_star_kpi() -> Dict[str, Any]:
    """北极星 KPI 形状：缺样本时 status=unavailable，不编造数字。"""
    from core.north_star import (
        build_north_star_report,
        compute_paper_risk_metrics,
        summarize_risk_blocks,
    )

    empty = compute_paper_risk_metrics([], window=60)
    assert empty.get("status") in ("unavailable", "ok", "partial")
    assert "rolling_sharpe" in empty
    assert "calmar" in empty

    # 合成净值序列 → 应能算出夏普或至少非崩
    snaps = []
    eq = 100000.0
    for i in range(40):
        eq *= 1.001 if i % 3 else 0.999
        snaps.append({"date": f"2024-01-{i+1:02d}" if i < 28 else f"2024-02-{i-27:02d}", "equity": round(eq, 2)})
    # use sequential dates that parse-ish
    snaps = [{"date": f"2024-06-{(i % 28) + 1:02d}", "equity": round(100000 * (1 + 0.002 * i), 2)} for i in range(30)]
    risk = compute_paper_risk_metrics(snaps, window=20)
    assert risk.get("status") in ("ok", "partial", "unavailable")

    report = build_north_star_report({"snapshots": snaps, "operation_log": []})
    assert report.get("ok") is True
    assert "paper_risk" in report
    assert "ttm" in report
    assert "risk_blocks" in report

    blocks = summarize_risk_blocks(
        [
            {
                "type": "risk_block",
                "ts": "2026-07-29T10:00:00",
                "detail": "行业超限",
                "meta": {"codes": ["max_sector"], "outcome": "true_positive"},
            }
        ]
    )
    assert blocks.get("block_count") == 1
    assert blocks.get("by_reason", {}).get("max_sector") == 1
    assert blocks.get("effectiveness_rate") == 1.0

    return {
        "id": "north_star_kpi",
        "ok": True,
        "paper_risk_status": risk.get("status"),
        "effectiveness_rate": blocks.get("effectiveness_rate"),
    }


def path_fundamentals_pit() -> Dict[str, Any]:
    """财务 PIT：不得选用 as_of 之后的快照；缺史 → 无点。"""
    from core.fundamentals_pit import select_point_as_of

    history = [
        {"as_of": "2024-03-31", "metrics": {"pe": 20.0}},
        {"as_of": "2024-06-30", "metrics": {"pe": 18.0}},
        {"as_of": "2024-12-31", "metrics": {"pe": 15.0}},
    ]
    point, meta = select_point_as_of(history, "2024-07-15")
    assert meta.get("ok") is True
    assert meta.get("selected_as_of") == "2024-06-30"
    assert point["metrics"]["pe"] == 18.0

    missing, meta2 = select_point_as_of(history, "2023-01-01")
    assert missing is None
    assert meta2.get("lookahead") is True or meta2.get("reason")

    # 合成 demo 点不得被当成「选了未来」——决策日早于全部点
    early, meta3 = select_point_as_of(
        [
            {
                "as_of": "2025-01-01",
                "metrics": {"pe": 10},
                "synthetic_demo": True,
            }
        ],
        "2024-06-01",
    )
    assert early is None

    return {
        "id": "fundamentals_pit",
        "ok": True,
        "selected_as_of": meta.get("selected_as_of"),
        "early_blocked": early is None,
    }


def path_risk_block_hard_gate() -> Dict[str, Any]:
    """行业超限 → check_account_risk 硬拦 + 结构化原因码。"""
    from core.risk.checks import check_account_risk

    paper = {"cash": 0, "strategy_id": "short", "holdings": []}
    summary = {
        "equity": 100000,
        "max_drawdown_pct": 1.0,
        "holdings": [
            {
                "stock_code": "300750",
                "shares": 100,
                "market_value": 30000,
                "sector": "新能源",
            },
            {
                "stock_code": "002594",
                "shares": 100,
                "market_value": 25000,
                "sector": "新能源",
            },
        ],
    }
    risk = {
        "max_drawdown_pct": 50,
        "max_position_pct": 40,
        "max_sector_pct": 40,
        "max_positions": 10,
    }
    out = check_account_risk(paper, summary, risk=risk)
    assert out.get("ok") is False
    assert "max_sector" in (out.get("block_codes") or [])
    assert out.get("blocks")
    return {
        "id": "risk_block_hard_gate",
        "ok": True,
        "block_codes": out.get("block_codes"),
    }


def path_cost_impact_monotonic() -> Dict[str, Any]:
    """V1 · 冲击成本随订单规模不减。"""
    from core.backtest.costs import estimate_impact_cost

    a = estimate_impact_cost(10_000, 2_000_000)
    b = estimate_impact_cost(200_000, 2_000_000)
    assert b >= a
    return {"id": "cost_impact_monotonic", "ok": True, "low": a, "high": b}


def path_cost_port_aligned() -> Dict[str, Any]:
    """T4 · CostPort：纸面 / 回测 / 因子计算器费率同源；组合含成本=turnover。"""
    from core.backtest.cost_port import (
        PORTFOLIO_COST_MODE,
        assert_cost_port_aligned,
        describe_cost_port,
    )
    from core.backtest.costs import rebalance_cost_pct

    snap = assert_cost_port_aligned()
    # 续持零成本（换手语义）
    hold = rebalance_cost_pct(["A", "B"], ["A", "B"])
    assert hold == 0.0

    return {
        "id": "cost_port_aligned",
        "ok": True,
        "portfolio_cost_mode": PORTFOLIO_COST_MODE,
        "describe": describe_cost_port(),
        "stamp_duty_bps_sell": (snap.get("simple_cn_fee") or {}).get("stamp_duty_bps_sell"),
    }


def path_validation_pack_shape() -> Dict[str, Any]:
    """V4 · 验证包含指纹与纪律字段位。"""
    from core.validation_pack import build_validation_pack

    out = build_validation_pack(
        backtest_result={"success": True, "metrics": {"x": 1}},
        signal_config={"weights": {"momentum": 1}},
    )
    assert out.get("ok")
    assert (out.get("pack") or {}).get("fingerprint")
    return {"id": "validation_pack_shape", "ok": True}


def run_all_core_paths() -> Dict[str, Any]:
    failures: List[str] = []
    results: List[Dict[str, Any]] = []
    for fn in (
        path_north_star_kpi,
        path_fundamentals_pit,
        path_risk_block_hard_gate,
        path_cost_impact_monotonic,
        path_cost_port_aligned,
        path_validation_pack_shape,
    ):
        try:
            results.append(fn())
        except Exception as e:
            failures.append(f"{fn.__name__}: {e}")
            results.append({"id": fn.__name__, "ok": False, "error": str(e)})
    return {
        "ok": not failures,
        "paths": results,
        "failures": failures,
        "count": len(results),
    }


if __name__ == "__main__":
    import json
    import sys

    out = run_all_core_paths()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if out["ok"] else 1)
