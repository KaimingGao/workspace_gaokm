"""策略验证宇宙（V0.5）：可配置纳入/排除；空财务码治理。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

from core.paths import DATA_DIR
from core.io_atomic import atomic_write_json

UNIVERSE_PATH = os.path.join(DATA_DIR, "validation_universe.json")


def default_universe() -> Dict[str, Any]:
    return {
        "version": 1,
        "exclude_codes": [],
        "include_only": [],
        "min_codes": 5,
        "min_trading_days": 40,
        "note": "include_only 非空时仅用该列表；否则 watching − exclude。B1：min_codes/min_trading_days 供闸门。",
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
    out["exclude_codes"] = [str(c).strip() for c in (out.get("exclude_codes") or []) if str(c).strip()]
    out["include_only"] = [str(c).strip() for c in (out.get("include_only") or []) if str(c).strip()]
    return out


def save_validation_universe(
    payload: dict, *, path: Optional[str] = None
) -> Dict[str, Any]:
    p = path or UNIVERSE_PATH
    merged = default_universe()
    if isinstance(payload, dict):
        merged.update(payload)
    merged["exclude_codes"] = [
        str(c).strip() for c in (merged.get("exclude_codes") or []) if str(c).strip()
    ]
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
    """解析验证用股票宇宙。"""
    uni = universe or load_validation_universe()
    include = list(uni.get("include_only") or [])
    exclude = set(uni.get("exclude_codes") or [])
    if include:
        codes = [c for c in include if c not in exclude]
        source = "include_only"
    else:
        if watching_codes is None:
            try:
                from core.data_coverage import universe_codes

                watching_codes = universe_codes()
            except Exception:
                watching_codes = []
        codes = [str(c).strip() for c in (watching_codes or []) if str(c).strip()]
        codes = [c for c in codes if c not in exclude]
        source = "watching_minus_exclude"
    return {
        "ok": True,
        "codes": codes,
        "count": len(codes),
        "source": source,
        "excluded": sorted(exclude),
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
        "note": "可将 empty_codes 写入 validation_universe.exclude_codes，或跑 fundamentals_warmup。",
    }


def exclude_empty_fundamentals(
    *,
    write: bool = False,
    codes: Optional[Sequence[str]] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """将空财务码并入 validation_universe.exclude_codes（可选写盘）。"""
    empty_rep = empty_fundamentals_report(codes=codes)
    empty = [str(c).strip() for c in (empty_rep.get("empty_codes") or []) if str(c).strip()]
    uni = load_validation_universe(path=path)
    before = list(uni.get("exclude_codes") or [])
    merged = sorted(set(before) | set(empty))
    added = sorted(set(merged) - set(before))
    uni["exclude_codes"] = merged
    out: Dict[str, Any] = {
        "ok": True,
        "wrote": False,
        "empty_codes": empty,
        "added_excludes": added,
        "exclude_codes": merged,
        "universe": uni,
        "note": "空财务码建议移出验证宇宙；write=true 才落盘。",
    }
    if write and added:
        save_validation_universe(uni, path=path)
        out["wrote"] = True
    elif write:
        out["wrote"] = False
        out["note"] = "无新增 exclude；未改盘。"
    return out


def build_validation_hygiene_report(
    *,
    codes: Optional[Sequence[str]] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """验证宇宙一页卫生：日线覆盖 + 空财务 + 舆情 history 面板覆盖。"""
    from core.data_coverage import build_data_coverage

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
        sentiment_panel = {"ok": False, "error": str(e)}

    actions: List[str] = []
    if empty_rep.get("empty_count"):
        actions.append(
            f"空财务 {empty_rep.get('empty_count')} 只 → exclude 或 fundamentals_warmup/ingest"
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
    """一键准备验证宇宙：卫生报告 + 可选 exclude 空财务 + 日线/舆情预热。

    不占用 schedule 槽位内部再开槽；可被 schedule kind=validation_prepare 调用。
    """
    hygiene = build_validation_hygiene_report(codes=codes)
    code_list = list((hygiene.get("universe") or {}).get("codes") or [])
    exclude_out = exclude_empty_fundamentals(write=bool(write_excludes), codes=code_list)
    if write_excludes and exclude_out.get("added_excludes"):
        # 排除后重算宇宙
        hygiene = build_validation_hygiene_report()
        code_list = list((hygiene.get("universe") or {}).get("codes") or [])

    bars_summary: Dict[str, Any] = {"skipped": True}
    if warmup_bars and code_list:
        try:
            from core.data_service import summarize_data_quality

            need = list(hygiene.get("bars_need_warmup") or []) or code_list
            bars_summary = summarize_data_quality(
                need[: max(1, min(len(need), 40))],
                limit=int(bars_limit or 60),
            )
            bars_summary["skipped"] = False
            bars_summary["warmed_codes"] = need[:40]
        except Exception as e:
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
            sentiment_summary = {"skipped": False, "ok": False, "error": str(e)}

    return {
        "ok": True,
        "kind": "validation_prepare",
        "hygiene": hygiene,
        "exclude": exclude_out,
        "bars_warmup": bars_summary,
        "sentiment_warmup": sentiment_summary,
        "note": "验证宇宙准备完成；舆情仍 prior-only，不进 ŷ。",
    }
