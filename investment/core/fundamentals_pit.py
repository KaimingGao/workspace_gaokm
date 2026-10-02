"""财务 PIT 最小可用（R1）：快照 history 面板 + as_of 选取。

约定：
- 日线 PIT 见 data_pit；本模块只管 fundamentals。
- 无 history 或 as_of 早于最早点 → 按 missing_as_of_policy 降级（默认不喂未来快照）。
- 不伪造历史财报；只能选用已落盘的 as_of 点。
"""

import logging

logger = logging.getLogger(__name__)
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.numbers import date_key
from core.signal.fundamentals_bridge import normalize_fundamentals_metrics
from core.store import snapshot_cache_path

# 财报报告期末（无公告日时不得把期末当天当「市场可知」）
_FISCAL_MD = frozenset({"03-31", "06-30", "09-30", "12-31"})


def is_fiscal_period_end(d: Optional[str]) -> bool:
    key = date_key(d)
    return bool(key) and key[5:] in _FISCAL_MD


def apply_point_availability(
    point: dict,
    *,
    fetched_at: Optional[str] = None,
) -> dict:
    """补全 available_as_of；报告期末无公告日仍标 ann_missing（防前视）。

    估值/现货快照的 as_of 是观测日（交易日），不是财报期末：该日 PE/PB 已可知，
    不得按 ann_missing 整票 zero_weight（否则分组表基本面全空）。
    报告期末点仅当 metrics 带 valuation_as_of（明确晚于期末的市场观测）才可解禁。
    fetched_at 只是写盘时间，不能给财报点当公告日。
    """
    if not isinstance(point, dict):
        return point
    metrics = point.get("metrics") if isinstance(point.get("metrics"), dict) else {}
    ann = (
        date_key(point.get("ann_date"))
        or date_key(metrics.get("ann_date"))
        or date_key(point.get("announce_date"))
        or date_key(metrics.get("announce_date"))
        or date_key(metrics.get("pub_date"))
    )
    as_of = date_key(point.get("as_of"))
    val_obs = date_key(metrics.get("valuation_as_of")) or date_key(
        point.get("valuation_as_of")
    )
    existing_avail = date_key(point.get("available_as_of")) or date_key(
        metrics.get("available_as_of")
    )
    if ann:
        point["ann_date"] = ann
        point["available_as_of"] = existing_avail or ann
        point.pop("ann_missing", None)
        return point
    if not is_fiscal_period_end(as_of):
        avail = existing_avail or val_obs or as_of
        if avail:
            point["available_as_of"] = avail
            point.pop("ann_missing", None)
            return point
        point["ann_missing"] = True
        return point
    # 报告期末：只认明确估值观测日（≥ 报告期末），不认 fetched_at
    if val_obs and val_obs >= as_of:
        point["available_as_of"] = val_obs
        point.pop("ann_missing", None)
        return point
    point["ann_missing"] = True
    return point


def _metrics_as_of_hint(payload: Any) -> str:
    """从 metrics / notes 猜报告期；否则用空串。"""
    if not isinstance(payload, dict):
        return ""
    src = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
    if not isinstance(src, dict):
        return ""
    for key in ("as_of", "report_date", "report_period", "end_date", "ann_date"):
        d = date_key(src.get(key))
        if d:
            return d
    return ""


def load_fundamentals_panel(
    code: str,
    *,
    store_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """读 fundamentals 快照文件（含 history），不受 TTL 限制。"""
    path = snapshot_cache_path("fundamentals", code, store_dir)
    if not os.path.isfile(path):
        return {
            "ok": False,
            "empty": True,
            "code": str(code or "").strip(),
            "path": path,
            "history": [],
            "fetched_at": None,
        }
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "ok": False,
            "empty": True,
            "error": str(e),
            "code": str(code or "").strip(),
            "path": path,
            "history": [],
        }

    history = list(payload.get("history") or [])
    # 兼容旧文件：把当前 data 视为唯一点
    if not history and payload.get("data") is not None:
        as_of = _metrics_as_of_hint(payload.get("data")) or date_key(
            payload.get("fetched_at")
        )
        hist_metrics = normalize_fundamentals_metrics(payload.get("data")) or {}
        if as_of and hist_metrics:
            point = {
                "as_of": as_of,
                "fetched_at": payload.get("fetched_at"),
                "metrics": hist_metrics,
                "data_source": payload.get("data_source"),
                "non_pit_origin": True,
            }
            apply_point_availability(point, fetched_at=payload.get("fetched_at"))
            history = [point]
    history = sorted(
        [h for h in history if isinstance(h, dict) and date_key(h.get("as_of"))],
        key=lambda h: (
            date_key(h.get("as_of")),
            date_key(h.get("ann_date")) or date_key((h.get("metrics") or {}).get("ann_date")),
        ),
    )
    for h in history:
        apply_point_availability(h, fetched_at=payload.get("fetched_at"))
    return {
        "ok": True,
        "empty": not bool(history),
        "code": payload.get("code") or code,
        "path": path,
        "fetched_at": payload.get("fetched_at"),
        "data_source": payload.get("data_source"),
        "history": history,
        "history_count": len(history),
        "latest_as_of": history[-1].get("as_of") if history else None,
    }


def _point_ann(point: dict) -> str:
    if not isinstance(point, dict):
        return ""
    metrics = point.get("metrics") if isinstance(point.get("metrics"), dict) else {}
    return (
        date_key(point.get("ann_date"))
        or date_key(metrics.get("ann_date"))
        or date_key(point.get("announce_date"))
        or date_key(metrics.get("announce_date"))
    )


def merge_history_point(
    history: List[dict],
    *,
    as_of: str,
    metrics: dict,
    fetched_at: Optional[str] = None,
    data_source: str = "",
    ann_date: Optional[str] = None,
    available_as_of: Optional[str] = None,
    max_points: int = 40,
) -> List[dict]:
    """按报告期 + 公告日合并。

    同一报告期、同一公告日以后到为准。
    同一报告期、不同公告日各留一点（修订链）。
    后到的点若带公告日，替换同一报告期上缺公告日的旧点。
    """
    key = date_key(as_of)
    if not key or not metrics:
        return list(history or [])
    ann = date_key(ann_date) or date_key((metrics or {}).get("ann_date"))
    point = {
        "as_of": key,
        "fetched_at": fetched_at or datetime.now().isoformat(timespec="seconds"),
        "metrics": dict(metrics),
        "data_source": data_source or "",
    }
    avail = date_key(available_as_of)
    if ann:
        point["ann_date"] = ann
        point["metrics"] = {**point["metrics"], "ann_date": ann}
    if avail:
        point["available_as_of"] = avail
    apply_point_availability(point, fetched_at=fetched_at)
    out: List[dict] = []
    replaced = False
    for raw in history or []:
        if not isinstance(raw, dict):
            continue
        h_as = date_key(raw.get("as_of"))
        h_ann = _point_ann(raw)
        same_revision = h_as == key and h_ann == ann
        upgrade_missing = h_as == key and (not h_ann) and bool(ann)
        if same_revision or upgrade_missing:
            if not replaced:
                out.append(point)
                replaced = True
            continue
        out.append(dict(raw))
    if not replaced:
        out.append(point)
    out.sort(key=lambda h: (date_key(h.get("as_of")), _point_ann(h)))
    if max_points > 0 and len(out) > max_points:
        out = out[-max_points:]
    return out


def _available_date(h: dict) -> str:
    """决策时「市场可知」日：公告/发布日优先，否则报告 as_of。

    S0.1：避免用报告期末日当可用日造成前视（报告期已过、公告未出）。
    """
    if not isinstance(h, dict):
        return ""
    for key in ("available_as_of", "ann_date", "announce_date", "pub_date"):
        d = date_key(h.get(key))
        if d:
            return d
    metrics = h.get("metrics") if isinstance(h.get("metrics"), dict) else {}
    for key in ("ann_date", "announce_date", "available_as_of", "pub_date"):
        d = date_key(metrics.get(key))
        if d:
            return d
    return date_key(h.get("as_of"))


def select_point_as_of(
    history: List[dict],
    as_of: str,
) -> Tuple[Optional[dict], Dict[str, Any]]:
    """选取决策日及以前**已可获取**的最新财务点（可用日 ≤ as_of）。"""
    cutoff = date_key(as_of)
    meta: Dict[str, Any] = {
        "ok": False,
        "as_of": cutoff,
        "selected_as_of": None,
        "selected_available_as_of": None,
        "lookahead": False,
        "reason": None,
        "availability_rule": "ann_date_preferred",
    }
    if not cutoff:
        meta["reason"] = "missing_as_of"
        return None, meta
    eligible = []
    for h in history or []:
        if not isinstance(h, dict):
            continue
        avail = _available_date(h)
        if avail and avail <= cutoff:
            eligible.append((avail, date_key(h.get("as_of")), h))
    if not eligible:
        meta["reason"] = "no_point_on_or_before"
        future = []
        for h in history or []:
            if not isinstance(h, dict):
                continue
            avail = _available_date(h)
            if avail and avail > cutoff:
                future.append(avail)
        if future:
            meta["lookahead"] = True
            meta["earliest_future_as_of"] = sorted(future)[0]
        return None, meta
    eligible.sort(key=lambda t: (t[0], t[1]))
    avail, report_as_of, chosen = eligible[-1]
    meta["ok"] = True
    meta["selected_as_of"] = report_as_of
    meta["selected_available_as_of"] = avail
    meta["ann_missing"] = bool(chosen.get("ann_missing"))
    meta["reason"] = None
    return chosen, meta


def merge_local_fundamentals_snapshot(
    code: str,
    metrics: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """只读本地 fundamentals 快照最新点（不联网、不做 PIT 解析）。

    刷簿 ``skip_fundamentals`` 热路径用：补 ROE / 增速等 valuation_em 没有的字段。
    已有键不覆盖。
    """
    code_s = str(code or "").strip()
    if not code_s:
        return metrics
    try:
        panel = load_fundamentals_panel(code_s)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in fundamentals_pit.py", exc_info=True)
        return metrics
    history = panel.get("history") or []
    if not history:
        return metrics
    latest = history[-1] if isinstance(history[-1], dict) else {}
    snap = dict(latest.get("metrics") or {})
    if not snap:
        return metrics
    out = dict(metrics or {})
    for k, v in snap.items():
        if out.get(k) is None and v is not None:
            out[k] = v
    return out or None


def resolve_fundamentals_for_score(
    code: str,
    *,
    as_of: Optional[str] = None,
    fund_cfg: Optional[dict] = None,
    live_fallback: bool = True,
) -> Dict[str, Any]:
    """供打分 / 回测：返回 metrics + PIT 元数据。

    missing_as_of_policy:
      - zero_weight: 不传 metrics（因子保持中性 50，等效该票无估值信息）
      - omit: 同 zero_weight
      - snapshot_if_live: 仅当 as_of 为空或 live 路径才用最新快照（默认 live）
    """
    cfg = dict(fund_cfg or {})
    pit_mode = str(cfg.get("pit_mode") or "as_of").strip().lower()
    policy = str(cfg.get("missing_as_of_policy") or "zero_weight").strip().lower()

    panel = load_fundamentals_panel(code)
    history = panel.get("history") or []

    if not as_of or pit_mode in ("snapshot", "off", "none"):
        # live / 显式快照模式
        if history:
            latest = history[-1]
            metrics = dict(latest.get("metrics") or {})
            try:
                from core.valuation_em import enrich_fundamentals_metrics

                metrics = enrich_fundamentals_metrics(code, metrics) or metrics
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in fundamentals_pit.py", exc_info=True)
                pass
            return {
                "ok": bool(metrics),
                "metrics": metrics or None,
                "fundamentals_pit": False,
                "non_pit": True,
                "as_of": latest.get("as_of"),
                "mode": "snapshot",
                "history_count": len(history),
                "note": "使用最新财务快照（非严格 PIT）。",
            }
        if live_fallback:
            try:
                from core.data.facade import get_fundamentals

                live = get_fundamentals(code, use_cache=True)
                metrics = normalize_fundamentals_metrics(live)
                try:
                    from core.valuation_em import enrich_fundamentals_metrics

                    metrics = enrich_fundamentals_metrics(code, metrics) or metrics
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in fundamentals_pit.py", exc_info=True)
                    pass
                return {
                    "ok": bool(metrics),
                    "metrics": metrics,
                    "fundamentals_pit": False,
                    "non_pit": True,
                    "as_of": date_key(live.get("fetched_at")) if isinstance(live, dict) else None,
                    "mode": "live_snapshot",
                    "history_count": 0,
                    "note": "实时/缓存快照；非 PIT。",
                }
            except Exception as e:
                logger.exception('unexpected error in resolve_fundamentals_for_score')
                return {
                    "ok": False,
                    "metrics": None,
                    "fundamentals_pit": False,
                    "non_pit": True,
                    "error": str(e),
                    "mode": "error",
                }
        return {
            "ok": False,
            "metrics": None,
            "fundamentals_pit": False,
            "non_pit": True,
            "mode": "empty",
            "reason": "no_history",
        }

    # as_of PIT 路径
    from core.data.policy import DEFAULT_ANN_MISSING_POLICY

    point, meta = select_point_as_of(history, as_of)
    ann_pol = str(cfg.get("ann_missing_policy") or DEFAULT_ANN_MISSING_POLICY).strip().lower()
    if point and point.get("metrics"):
        metrics = dict(point.get("metrics") or {})
        ann_miss = bool(meta.get("ann_missing") or point.get("ann_missing"))
        try:
            from core.valuation_em import enrich_fundamentals_metrics

            metrics = enrich_fundamentals_metrics(code, metrics) or metrics
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in fundamentals_pit.py", exc_info=True)
            pass
        if ann_miss and ann_pol in ("zero_weight", "omit"):
            return {
                "ok": False,
                "metrics": None,
                "fundamentals_pit": True,
                "non_pit": False,
                "as_of": meta.get("selected_as_of"),
                "decision_as_of": meta.get("as_of"),
                "mode": "ann_missing_zero_weight",
                "history_count": len(history),
                "pit_meta": meta,
                "ann_missing": True,
                "ann_missing_policy": ann_pol,
                "policy": ann_pol,
                "note": "缺公告日：财务因子按 zero_weight 中性化（防前视）。",
            }
        if ann_miss and ann_pol == "hard_reject":
            return {
                "ok": False,
                "metrics": None,
                "fundamentals_pit": True,
                "non_pit": False,
                "as_of": meta.get("selected_as_of"),
                "decision_as_of": meta.get("as_of"),
                "mode": "ann_missing_hard_reject",
                "history_count": len(history),
                "pit_meta": meta,
                "ann_missing": True,
                "ann_missing_policy": ann_pol,
                "hard_reject": True,
                "note": "缺公告日：hard_reject，不进生产财务因子。",
            }
        return {
            "ok": True,
            "metrics": metrics,
            "fundamentals_pit": True,
            "non_pit": False,
            "as_of": meta.get("selected_as_of"),
            "decision_as_of": meta.get("as_of"),
            "mode": "as_of",
            "history_count": len(history),
            "pit_meta": meta,
            "ann_missing": ann_miss,
            "ann_missing_policy": ann_pol,
            "note": "财务按 as_of 选取；无未来报告期。",
        }

    # 缺失：禁止用未来快照
    out = {
        "ok": False,
        "metrics": None,
        "fundamentals_pit": True,  # 意图是 PIT；结果缺失
        "non_pit": False,
        "as_of": None,
        "decision_as_of": meta.get("as_of"),
        "mode": "as_of_missing",
        "history_count": len(history),
        "pit_meta": meta,
        "ann_missing": False,
        "policy": policy,
        "note": "决策日无可用财务点；已拒绝未来快照。",
    }
    if policy in ("snapshot_if_live", "allow_snapshot"):
        # 仍禁止：as_of 路径下不允许偷看最新
        out["note"] = "as_of 路径拒绝 snapshot 回退（防未来函数）。"
    return out


def fundamentals_pit_summary(
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """汇总多次 resolve 结果，写入 pit_report。"""
    total = len(results or [])
    pit_ok = sum(1 for r in results if r.get("fundamentals_pit") and r.get("ok"))
    missing = sum(1 for r in results if r.get("mode") == "as_of_missing")
    snapshot = sum(1 for r in results if r.get("mode") in ("snapshot", "live_snapshot"))
    lookahead = sum(
        1 for r in results if ((r.get("pit_meta") or {}).get("lookahead"))
    )
    return {
        "fundamentals_pit": pit_ok > 0 and missing + snapshot == 0,
        "fundamentals_pit_partial": pit_ok > 0,
        "resolved_ok": pit_ok,
        "missing_as_of": missing,
        "snapshot_used": snapshot,
        "lookahead_blocked": lookahead,
        "sample_count": total,
        "note": (
            "财务 PIT：ok 点按 as_of；缺失不回退未来快照。"
            if pit_ok or missing
            else "本轮未使用财务 as_of。"
        ),
    }
