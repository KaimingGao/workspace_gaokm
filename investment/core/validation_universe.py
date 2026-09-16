"""策略验证宇宙（V0.5）：观察池（可选 include_only）；空财务码治理。"""


import logging

logger = logging.getLogger(__name__)
import json
import os
from typing import Any, Dict, List, Optional, Sequence

from core.io_atomic import atomic_write_json
from core.paths import DATA_DIR

UNIVERSE_PATH = os.path.join(DATA_DIR, "validation_universe.json")


def default_universe() -> Dict[str, Any]:
    return {
        "version": 1,
        "exclude_codes": [],
        "include_only": [],
        "min_codes": 5,
        "min_trading_days": 40,
        "note": "include_only 非空时仅用该列表；否则=观察池。屏蔽某票从观察池删除。B1：min_codes/min_trading_days 供闸门。",
    }


def load_validation_universe(*, path: Optional[str] = None) -> Dict[str, Any]:
    p = path or UNIVERSE_PATH
    if not os.path.isfile(p):
        return default_universe()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default_universe()
    out = default_universe()
    if isinstance(data, dict):
        out.update(data)
    # exclude_codes 已废弃：屏蔽某票请从观察池删除，读盘不再带回旧名单。
    out["exclude_codes"] = []
    out["include_only"] = [str(c).strip() for c in (out.get("include_only") or []) if str(c).strip()]
    return out


def save_validation_universe(
    payload: dict, *, path: Optional[str] = None
) -> Dict[str, Any]:
    p = path or UNIVERSE_PATH
    merged = default_universe()
    if isinstance(payload, dict):
        merged.update(payload)
    merged["exclude_codes"] = []
    merged["include_only"] = [
        str(c).strip() for c in (merged.get("include_only") or []) if str(c).strip()
    ]
    atomic_write_json(p, merged)
    return {"ok": True, "path": p, "universe": merged}


def resolve_validation_codes(
    *,
    watching_codes: Optional[Sequence[str]] = None,
    universe: Optional[dict] = None,
) -> Dict[str, Any]:
    """解析验证用股票宇宙：观察池，或 include_only。不再扣 exclude。"""
    uni = universe or load_validation_universe()
    include = [str(c).strip() for c in (uni.get("include_only") or []) if str(c).strip()]
    if include:
        codes = include
        source = "include_only"
    else:
        if watching_codes is None:
            try:
                from core.data.coverage import universe_codes

                watching_codes = universe_codes()
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in validation_universe.py", exc_info=True)
                watching_codes = []
        codes = [str(c).strip() for c in (watching_codes or []) if str(c).strip()]
        source = "watching"
    return {
        "ok": True,
        "codes": codes,
        "count": len(codes),
        "source": source,
        "excluded": [],
        "universe": uni,
        "min_codes": int(uni.get("min_codes") or 5),
        "min_trading_days": int(uni.get("min_trading_days") or 40),
        "universe_ok": len(codes) >= int(uni.get("min_codes") or 5),
    }


def universe_sample_gate(*, universe: Optional[dict] = None) -> Dict[str, Any]:
    """B1：验证宇宙码数门槛（供 promote / maturity）。"""
    resolved = resolve_validation_codes(universe=universe)
    min_c = int(resolved.get("min_codes") or 5)
    ok = bool(resolved.get("universe_ok"))
    blockers = []
    if not ok:
        blockers.append(
            f"验证宇宙 {resolved.get('count')} < min_codes={min_c}"
        )
    return {
        "ok": ok,
        "count": resolved.get("count"),
        "min_codes": min_c,
        "min_trading_days": resolved.get("min_trading_days"),
        "blockers": blockers,
        "track": "B1",
    }


def empty_fundamentals_report(
    *,
    codes: Optional[Sequence[str]] = None,
    store_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """列出空财务码，供补拉或移出验证宇宙。"""
    from core.sample_ops import fundamentals_history_coverage

    if codes is None:
        resolved = resolve_validation_codes()
        codes = resolved.get("codes") or []
        meta = {"universe_source": resolved.get("source")}
    else:
        meta = {"universe_source": "explicit"}
    cov = fundamentals_history_coverage(store_dir=store_dir, codes=codes)
    empty = list(cov.get("empty_codes") or [])
    return {
        "ok": True,
        "empty_codes": empty,
        "empty_count": len(empty),
        "total": cov.get("total"),
        "real_multi_point": cov.get("real_multi_point"),
        "real_multi_coverage": cov.get("real_multi_coverage"),
        **meta,
        "note": "空财务请 fundamentals_warmup，或从观察池删除该票。",
    }


def build_validation_hygiene_report(
    *,
    codes: Optional[Sequence[str]] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """验证宇宙一页卫生：日线覆盖 + 空财务 + 舆情 history 面板覆盖。"""
    from core.data.coverage import build_data_coverage

    resolved = resolve_validation_codes(watching_codes=codes) if codes is None else {
        "ok": True,
        "codes": [str(c).strip() for c in codes if str(c).strip()],
        "count": 0,
        "source": "explicit",
        "excluded": [],
        "universe": load_validation_universe(),
    }
    if codes is not None:
        resolved["count"] = len(resolved["codes"])
    code_list = list(resolved.get("codes") or [])

    coverage: Dict[str, Any] = {}
    try:
        coverage = build_data_coverage(codes=code_list, include_paper=False) or {}
    except Exception as e:
        logger.exception('unexpected error in build_validation_hygiene_report')
        coverage = {"ok": False, "error": str(e)}

    empty_rep = empty_fundamentals_report(codes=code_list)
    thin_or_empty: List[str] = []
    for row in coverage.get("items") or []:
        if not isinstance(row, dict):
            continue
        level = str(row.get("quality_level") or row.get("level") or "")
        code = str(row.get("stock_code") or row.get("code") or "").strip()
        stale = bool(row.get("stale"))
        if code and (level in ("thin", "empty") or stale):
            thin_or_empty.append(code)

    sentiment_panel: Dict[str, Any] = {}
    try:
        from core.sentiment import build_sentiment_panel_coverage

        sentiment_panel = build_sentiment_panel_coverage(
            code_list, as_of=as_of
        )
    except Exception as e:
        logger.exception('unexpected error in build_validation_hygiene_report')
        sentiment_panel = {"ok": False, "error": str(e)}

    actions: List[str] = []
    if empty_rep.get("empty_count"):
        actions.append(
            f"空财务 {empty_rep.get('empty_count')} 只 → 观察池删除或 fundamentals_warmup/ingest"
        )
    if thin_or_empty:
        actions.append(f"日线 thin/empty {len(thin_or_empty)} 只 → bars_warmup")
    if sentiment_panel.get("missing_count"):
        actions.append(
            f"舆情 history 缺失 {sentiment_panel.get('missing_count')} 只 → sentiment_scan / validation_prepare"
        )

    status = "ok"
    if empty_rep.get("empty_count") or thin_or_empty:
        status = "warn"
    if (resolved.get("count") or 0) < int((resolved.get("universe") or {}).get("min_codes") or 5):
        status = "fail"
        actions.append("验证宇宙码数不足 min_codes")

    return {
        "ok": True,
        "status": status,
        "universe": resolved,
        "bars_coverage": coverage,
        "bars_need_warmup": thin_or_empty,
        "empty_fundamentals": empty_rep,
        "sentiment_panel": sentiment_panel,
        "actions": actions,
        "note": "模拟验证卫生报告；不代客下单；不接 Tick/OMS。",
    }


def prepare_validation_universe(
    *,
    write_excludes: bool = False,
    warmup_bars: bool = True,
    warmup_sentiment: bool = True,
    bars_limit: int = 60,
    sentiment_limit: int = 5,
    codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """一键准备验证宇宙：卫生报告 + 日线/舆情预热。

    write_excludes 已废弃（忽略）：屏蔽某票请从观察池删除。
    不占用 schedule 槽位内部再开槽；可被 schedule kind=validation_prepare 调用。
    """
    _ = write_excludes
    hygiene = build_validation_hygiene_report(codes=codes)
    code_list = list((hygiene.get("universe") or {}).get("codes") or [])

    bars_summary: Dict[str, Any] = {"skipped": True}
    if warmup_bars and code_list:
        try:
            from core.data.facade import summarize_data_quality

            need = list(hygiene.get("bars_need_warmup") or []) or code_list
            bars_summary = summarize_data_quality(
                need[: max(1, min(len(need), 40))],
                limit=int(bars_limit or 60),
            )
            bars_summary["skipped"] = False
            bars_summary["warmed_codes"] = need[:40]
        except Exception as e:
            logger.exception('unexpected error in prepare_validation_universe')
            bars_summary = {"skipped": False, "ok": False, "error": str(e)}

    sentiment_summary: Dict[str, Any] = {"skipped": True}
    if warmup_sentiment and code_list:
        try:
            from core.sentiment import warmup_sentiment_history

            sentiment_summary = warmup_sentiment_history(
                code_list[:20], limit=int(sentiment_limit or 5)
            )
            sentiment_summary["skipped"] = False
        except Exception as e:
            logger.exception('unexpected error in prepare_validation_universe')
            sentiment_summary = {"skipped": False, "ok": False, "error": str(e)}

    return {
        "ok": True,
        "kind": "validation_prepare",
        "hygiene": hygiene,
        "exclude": {
            "skipped": True,
            "note": "exclude 已废弃；屏蔽请从观察池删除。",
        },
        "bars_warmup": bars_summary,
        "sentiment_warmup": sentiment_summary,
        "note": "验证宇宙准备完成；舆情仍 prior-only，不进 ŷ。",
    }
