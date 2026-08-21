"""仪表盘：纸面只读加载与净值/持仓辅助。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from web import deps

def _load_raw_paper() -> Dict[str, Any]:
    """只读落盘纸面（不盯市），避免仪表盘与行情/AkShare 锁互拖。"""
    path = getattr(deps.paper, "path", None)
    if not path or not os.path.isfile(path):
        return {}
    try:
        from core.paper import load_paper

        paper = load_paper(path)
        return paper if isinstance(paper, dict) else {}
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        return {}


def _equity_curve_from_paper(paper: Dict[str, Any]) -> List[Dict[str, Any]]:
    """净值序列：优先 snapshots（生产落盘），兼容遗留 equity_curve。

    同时给出 ``date``（日历日，供区间/基准对齐）与 ``time``（唯一时间轴，
    同日多次快照用完整 ISO，避免图表按日去重后只剩 1 点）。
    """
    out: List[Dict[str, Any]] = []
    for s in paper.get("snapshots") or []:
        if not isinstance(s, dict):
            continue
        eq = s.get("equity")
        if eq is None:
            continue
        try:
            equity = float(eq)
        except (TypeError, ValueError):
            continue
        ts = s.get("ts") or s.get("date") or s.get("time") or ""
        ts_s = str(ts).strip().replace("Z", "")
        day = ts_s[:10] if len(ts_s) >= 10 else None
        # 有时分秒则保留完整时间，保证同日多快照可画线
        if "T" in ts_s and len(ts_s) > 10:
            time_key = ts_s
        else:
            time_key = day
        out.append(
            {
                "date": day,
                "time": time_key or day,
                "equity": equity,
                "cash": s.get("cash"),
                "stock_value": s.get("stock_value"),
                "total_pnl_pct": s.get("total_pnl_pct"),
                "live": bool(s.get("live")),
            }
        )
    if out:
        return out
    legacy = paper.get("equity_curve") or []
    normalized: List[Dict[str, Any]] = []
    for e in legacy:
        if not isinstance(e, dict) or e.get("equity") is None:
            continue
        day = str(e.get("date") or e.get("time") or "")[:10] or None
        time_key = e.get("time") or e.get("ts") or day
        row = dict(e)
        row.setdefault("date", day)
        row.setdefault("time", time_key)
        normalized.append(row)
    return normalized


def _equity_curve_with_live(paper: Dict[str, Any]) -> List[Dict[str, Any]]:
    """落盘 snapshots + 现价盯市末点（只读，不写 paper.json）。

    仪表盘 KPI 累计/今日已走 mark_to_market；曲线若只读快照，盘中末点会和摘要分叉。
    """
    if not paper:
        return []
    try:
        from core.paper import mark_to_market, snapshots_for_ui

        summary = mark_to_market(paper)
        work = dict(paper)
        work["snapshots"] = snapshots_for_ui(paper, summary)
        return _equity_curve_from_paper(work)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        return _equity_curve_from_paper(paper)


def _holdings_from_paper(paper: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [h for h in (paper.get("holdings") or []) if isinstance(h, dict)]


def _holding_market_value(h: Dict[str, Any]) -> float:
    """盯市市值优先；缺省时用成本市值（手动仓常无 market_value）。"""
    try:
        mv = float(h.get("market_value") or 0)
    except (TypeError, ValueError):
        mv = 0.0
    if mv > 0:
        return mv
    try:
        return float(h.get("shares") or 0) * float(h.get("cost") or 0)
    except (TypeError, ValueError):
        return 0.0


def _holding_sector(h: Dict[str, Any], sector_map: Optional[Dict[str, str]] = None) -> str:
    """持仓行业：显式字段 → sector_map → 板块启发式。"""
    raw = h.get("sector") or h.get("industry")
    if raw:
        return str(raw)
    code = str(h.get("code") or h.get("stock_code") or "").strip()
    try:
        from core.portfolio_optimize import _sector_for, load_sector_map

        return str(_sector_for(code, sector_map if sector_map is not None else load_sector_map()))
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        return "其他"


def _trades_from_paper(paper: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [t for t in (paper.get("trades") or []) if isinstance(t, dict)]


def _north_star_from_paper(paper: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cached = paper.get("last_north_star")
    if isinstance(cached, dict) and cached.get("ok") is not False:
        return cached
    try:
        from core.north_star import build_north_star_report

        return build_north_star_report(paper)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        return None


def _unpack_index_bars(raw: Any) -> List[dict]:
    """兼容 DataService 信封 dict、``(bars, label)`` 与 list。"""
    if raw is None:
        return []
    if isinstance(raw, dict):
        bars = raw.get("bars")
        return list(bars) if isinstance(bars, list) else []
    if isinstance(raw, tuple):
        bars = raw[0] if raw else []
        return list(bars) if isinstance(bars, list) else []
    if isinstance(raw, list):
        return raw
    return []


def _fetch_index_bars_bounded(
    code: str,
    *,
    limit: int = 2,
    timeout_sec: float = 4.0,
) -> List[dict]:
    """带超时的指数日线；超时/失败返回 []，避免仪表盘整页卡住。

    不用 ``ThreadPoolExecutor``（``shutdown(wait=True)`` 会在超时后继续等 worker）。
    """
    import threading

    try:
        from core.data_service import get_index_bars
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        return []

    box: Dict[str, Any] = {"raw": None, "err": None}
    done = threading.Event()

    def _worker() -> None:
        try:
            box["raw"] = get_index_bars(str(code or "").strip(), limit=int(limit))
        except Exception as exc:
            logger.exception('unexpected error in _worker')
            box["err"] = exc
        finally:
            done.set()

    t = threading.Thread(target=_worker, daemon=True, name="dash-index-bars")
    t.start()
    if not done.wait(timeout=max(0.5, float(timeout_sec))):
        return []
    if box["err"] is not None:
        return []
    return _unpack_index_bars(box["raw"])

