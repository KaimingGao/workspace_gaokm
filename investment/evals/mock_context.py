"""Golden case 离线 mock：patch 行情/日线/基本面，使 checklist 可完全离线跑通。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from unittest.mock import patch


def rising_bars(n: int = 45, start: float = 100.0, step: float = 0.5) -> List[dict]:
    bars = []
    price = start
    for i in range(n):
        price += step
        bars.append(
            {
                "date": f"2026-01-{i+1:02d}",
                "open": price - 0.2,
                "high": price + 0.3,
                "low": price - 0.3,
                "close": price,
                "volume": 1000 + i * 20,
            }
        )
    return bars


def stepped_bars(start: float = 100.0, steps: Optional[List[float]] = None) -> List[dict]:
    steps = steps if steps is not None else list(range(21))
    out = []
    for i, s in enumerate(steps):
        close = start + s
        out.append(
            {
                "date": f"d{i}",
                "open": close - 0.2,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "volume": 1000 + i * 10,
            }
        )
    return out


def resolve_daily_bars(spec: Any) -> Tuple[List[dict], str]:
    if isinstance(spec, list):
        return spec, "fixture"
    if spec in (None, "", "empty"):
        return [], "mock_empty"
    if spec == "rising_45":
        return rising_bars(45), "mock_daily"
    if spec == "rising_21":
        return rising_bars(21), "mock_daily"
    if spec == "half_rise_21":
        return stepped_bars(100.0, [i * 0.5 for i in range(21)]), "mock_index"
    raise ValueError(f"未知 daily_bars mock: {spec!r}")


def resolve_index_bars(spec: Any, label: str = "沪深300") -> Tuple[List[dict], str]:
    if spec in (None, "", "empty", "none"):
        return [], ""
    if spec == "half_rise_21":
        return stepped_bars(100.0, [i * 0.5 for i in range(21)]), label
    if isinstance(spec, list):
        return spec, label
    raise ValueError(f"未知 index_bars mock: {spec!r}")


def _normalize_key(text: str) -> str:
    return str(text or "").strip().lower()


def build_quote_fn(mock_cfg: dict) -> Optional[Callable[[str], dict]]:
    quotes = mock_cfg.get("quotes")
    fallback = mock_cfg.get("quote")

    if quotes:
        table: Dict[str, dict] = {}
        for key, val in quotes.items():
            table[_normalize_key(key)] = val
            code = val.get("stock_code")
            name = val.get("stock_name")
            if code:
                table[_normalize_key(str(code))] = val
            if name:
                table[_normalize_key(str(name))] = val

        def query(code: str) -> dict:
            key = _normalize_key(code)
            if key in table:
                return table[key]
            for k, v in table.items():
                if k and (k in key or key in k):
                    return v
            if fallback:
                return fallback
            return {"success": False, "error": f"mock: 无行情 {code}"}

        return query

    if fallback:
        return lambda code: fallback

    return None


def _patch_daily_bars(stack, mock_cfg: dict) -> None:
    if "daily_bars" not in mock_cfg:
        return
    bars, src = resolve_daily_bars(mock_cfg["daily_bars"])
    ret = (bars, src)
    pack = {"bars": bars, "data_source": src, "production_ok": True}
    # 只 patch 仍存在的符号；日线入口已收敛到 core.data.facade
    for target, value in (
        ("core.data.facade.bars_and_source", ret),
        ("core.data.facade.bars_and_source_research", ret),
        ("core.data.facade.get_bars", pack),
        ("skills.common.history.fetch_daily_bars", ret),
        ("core.signal.score_stock.fetch_daily_bars", ret),
    ):
        try:
            stack.enter_context(patch(target, return_value=value))
        except AttributeError:
            continue

    if "index_bars" not in mock_cfg:
        stack.enter_context(
            patch("skills.index.engine.fetch_index_bars", return_value=([], ""))
        )
        try:
            stack.enter_context(
                patch("skills.backtest.engine.fetch_index_bars", return_value=([], ""))
            )
        except AttributeError:
            pass
        try:
            stack.enter_context(
                patch("core.ports.market.fetch_index_bars", return_value=([], ""))
            )
        except AttributeError:
            pass


def _patch_fundamentals(stack, mock_cfg: dict) -> None:
    fin = mock_cfg.get("fundamentals")
    if not fin:
        return
    spot = fin.get("spot")
    if spot is not None:
        stack.enter_context(
            patch("skills.fundamentals.engine.fetch_cn_spot_row", return_value=spot)
        )
    stack.enter_context(
        patch(
            "skills.fundamentals.engine.fetch_cn_valuation_latest",
            return_value=fin.get("valuation") or {},
        )
    )
    stack.enter_context(
        patch(
            "skills.fundamentals.engine.fetch_cn_financial_latest",
            return_value=fin.get("financial") or {},
        )
    )
    rmc = fin.get("resolve_market_code")
    if rmc is not None:
        stack.enter_context(
            patch(
                "skills.fundamentals.engine.resolve_market_code",
                return_value=tuple(rmc),
            )
        )


@contextmanager
def apply_case_mocks(mock_cfg: Optional[dict]) -> Iterator[None]:
    if not mock_cfg:
        yield
        return

    from contextlib import ExitStack

    stack = ExitStack()
    quote_fn = build_quote_fn(mock_cfg)
    if quote_fn is not None:
        stack.enter_context(
            patch("skills.common.quote_api.StockAPI.query", side_effect=quote_fn)
        )

    _patch_daily_bars(stack, mock_cfg)

    if "index_bars" in mock_cfg:
        ib, ilabel = resolve_index_bars(
            mock_cfg["index_bars"],
            str(mock_cfg.get("index_label") or "沪深300"),
        )
        stack.enter_context(
            patch("skills.index.engine.fetch_index_bars", return_value=(ib, ilabel))
        )
        stack.enter_context(
            patch("skills.backtest.engine.fetch_index_bars", return_value=(ib, ilabel))
        )

    rmc = mock_cfg.get("resolve_market_code")
    if rmc is not None:
        tup = tuple(rmc)
        stack.enter_context(
            patch("skills.index.engine.resolve_market_code", return_value=tup)
        )
        stack.enter_context(
            patch("skills.backtest.engine.resolve_market_code", return_value=tup)
        )
        stack.enter_context(
            patch("skills.common.history.resolve_market_code", return_value=tup)
        )
        stack.enter_context(
            patch("skills.fundamentals.engine.resolve_market_code", return_value=tup)
        )

    _patch_fundamentals(stack, mock_cfg)

    portfolio = mock_cfg.get("portfolio")
    if portfolio is not None:

        def _load_paper_holdings(path=None):
            return {
                "path": "(mock)",
                "cash": float(portfolio.get("cash") or 0),
                "holdings": list(portfolio.get("holdings") or []),
                "source": "paper",
            }

        stack.enter_context(
            patch(
                "skills.position.engine.load_paper_holdings",
                side_effect=_load_paper_holdings,
            )
        )

    quant_health = mock_cfg.get("quant_health")
    if quant_health is not None:
        stack.enter_context(
            patch(
                "quant.services.quant_service.QuantService.build_health_summary",
                return_value=quant_health,
            )
        )

    quant_bridge = mock_cfg.get("quant_portfolio_bridge")
    if quant_bridge is not None:
        stack.enter_context(
            patch(
                "quant.services.quant_service.QuantService.build_portfolio_bridge",
                return_value=quant_bridge,
            )
        )

    quant_package = mock_cfg.get("quant_package_info")
    if quant_package is not None:
        stack.enter_context(
            patch(
                "quant.services.quant_service.QuantService.build_package_info",
                return_value=quant_package,
            )
        )

    watching = mock_cfg.get("watching")
    if watching is not None:
        stack.enter_context(
            patch("core.watching.store.read_watching", return_value=watching)
        )
        stack.enter_context(
            patch("core.signal.cross_section.read_watching", return_value=watching)
        )

    with stack:
        yield
