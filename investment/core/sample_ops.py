"""样本运营（R0–R5 后）：TTM 闭环种子 · 财务 history 落盘 · 纸面快照密度 · 覆盖报告。

不伪造生产分数；demo 阶梯点须标 synthetic_demo，仅用于 PIT 路径演示。
"""


import logging

logger = logging.getLogger(__name__)
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from core.io_atomic import atomic_write_json
from core.store import snapshot_cache_path


def _date_offset(base: str, days: int) -> str:
    try:
        dt = datetime.strptime(str(base)[:10], "%Y-%m-%d")
    except ValueError:
        dt = datetime.now()
    return (dt + timedelta(days=days)).strftime("%Y-%m-%d")


def fundamentals_history_coverage(
    *,
    store_dir: Optional[str] = None,
    codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """扫描 fundamentals 快照的 history 覆盖（区分真实 vs synthetic_demo）。"""
    from core.fundamentals_pit import load_fundamentals_panel
    from core.paths import STORE_DIR

    root = store_dir or os.path.join(STORE_DIR, "fundamentals")
    rows: List[Dict[str, Any]] = []
    if codes:
        code_list = [str(c).strip() for c in codes if str(c).strip()]
    else:
        code_list = []
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                if name.endswith(".json") and not name.startswith("."):
                    code_list.append(name[:-5])

    with_hist = 0
    multi = 0
    empty = 0
    synthetic_multi = 0
    real_multi = 0
    ann_missing_points = 0
    ann_missing_codes = 0
    empty_codes: List[str] = []
    for code in code_list:
        panel = load_fundamentals_panel(code, store_dir=store_dir)
        hc = int(panel.get("history_count") or 0)
        hist = list(panel.get("history") or [])
        demo_n = sum(
            1
            for h in hist
            if (h or {}).get("synthetic_demo")
            or str((h or {}).get("data_source") or "").startswith("synthetic_demo")
        )
        real_n = max(0, hc - demo_n)
        code_ann_missing = sum(
            1 for h in hist if isinstance(h, dict) and h.get("ann_missing")
        )
        if code_ann_missing:
            ann_missing_codes += 1
            ann_missing_points += code_ann_missing
        if panel.get("empty") or hc <= 0:
            empty += 1
            status = "empty"
            empty_codes.append(code)
        elif hc >= 2:
            multi += 1
            with_hist += 1
            status = "multi"
            if demo_n > 0 and real_n < 2:
                synthetic_multi += 1
            else:
                real_multi += 1
        else:
            with_hist += 1
            status = "single"
        rows.append(
            {
                "code": code,
                "status": status,
                "history_count": hc,
                "real_points": real_n,
                "synthetic_demo_points": demo_n,
                "ann_missing_points": code_ann_missing,
                "latest_as_of": panel.get("latest_as_of"),
                "path": panel.get("path"),
            }
        )

    total = len(code_list)
    return {
        "ok": True,
        "total": total,
        "with_history": with_hist,
        "multi_point": multi,
        "real_multi_point": real_multi,
        "synthetic_multi_point": synthetic_multi,
        "empty": empty,
        "empty_codes": empty_codes[:80],
        "coverage": round(with_hist / total, 4) if total else None,
        "real_multi_coverage": round(real_multi / total, 4) if total else None,
        "ann_missing_codes": ann_missing_codes,
        "ann_missing_points": ann_missing_points,
        "ann_missing_code_ratio": round(ann_missing_codes / total, 4)
        if total
        else None,
        "ingest_hint": (
            "平台点「预热财务多期」或 CLI: sample_ops_run.py ingest-history；"
            "勿仅 seed-ladder。ann_missing 高时补公告日。"
        ),
        "note": "real_multi_point 不含仅靠 synthetic_demo ladder 的多点。",
        "rows": rows,
    }


def persist_fundamentals_history(
    *,
    codes: Optional[Sequence[str]] = None,
    store_dir: Optional[str] = None,
    write: bool = True,
) -> Dict[str, Any]:
    """把 load 时的兼容 history 写回磁盘（单点也可落盘，便于 as_of 路径）。"""
    from core.fundamentals_pit import load_fundamentals_panel, merge_history_point
    from core.signal.fundamentals_bridge import normalize_fundamentals_metrics

    cov = fundamentals_history_coverage(store_dir=store_dir, codes=codes)
    updated: List[str] = []
    skipped: List[Dict[str, str]] = []

    for row in cov.get("rows") or []:
        code = row["code"]
        path = snapshot_cache_path("fundamentals", code, store_dir)
        if not os.path.isfile(path):
            skipped.append({"code": code, "reason": "missing_file"})
            continue
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            skipped.append({"code": code, "reason": str(e)})
            continue

        existing = list(payload.get("history") or [])
        if existing:
            skipped.append({"code": code, "reason": "already_has_history"})
            continue

        panel = load_fundamentals_panel(code, store_dir=store_dir)
        hist = list(panel.get("history") or [])
        if not hist:
            # 再尝试从 data 抽 metrics
            metrics = normalize_fundamentals_metrics(payload.get("data")) or {}
            as_of = str(payload.get("fetched_at") or "")[:10]
            if not metrics or not as_of:
                skipped.append({"code": code, "reason": "no_metrics"})
                continue
            hist = merge_history_point(
                [],
                as_of=as_of,
                metrics=metrics,
                fetched_at=payload.get("fetched_at"),
                data_source=str(payload.get("data_source") or ""),
            )
            for h in hist:
                h["non_pit_origin"] = True

        if not write:
            updated.append(code)
            continue

        payload["history"] = hist
        payload["latest_as_of"] = hist[-1].get("as_of") if hist else None
        payload["history_persisted_at"] = datetime.now().isoformat(timespec="seconds")
        atomic_write_json(path, payload)
        updated.append(code)

    return {
        "ok": True,
        "written": bool(write),
        "updated": updated,
        "updated_count": len(updated),
        "skipped": skipped[:40],
        "note": "仅落盘已有/可推断点；不伪造财报数字。",
    }


def ingest_real_fundamentals_history(
    *,
    codes: Optional[Sequence[str]] = None,
    max_points: int = 8,
    drop_synthetic: bool = True,
    store_dir: Optional[str] = None,
    write: bool = True,
) -> Dict[str, Any]:
    """从 AkShare 财务指标全表写入真实多期 history（V0.1 / C1）。

    - 仅 A 股 6 位码走序列拉取；港股/名称码跳过并记 skipped
    - drop_synthetic=True 时：若真实点 ≥2，剔除 synthetic_demo 阶梯点
    """
    from core.fundamentals_pit import merge_history_point
    from core.paths import STORE_DIR
    from core.ports.market import fetch_cn_financial_series

    root = store_dir or os.path.join(STORE_DIR, "fundamentals")
    if codes:
        code_list = [str(c).strip() for c in codes if str(c).strip()]
    else:
        code_list = []
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                if name.endswith(".json") and not name.startswith("."):
                    code_list.append(name[:-5])
        if not code_list:
            try:
                from core.data.coverage import universe_codes

                code_list = list(universe_codes() or [])
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in sample_ops.py", exc_info=True)
                code_list = []

    updated: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []
    n_pts = max(2, min(40, int(max_points or 8)))

    for code in code_list:
        digits = "".join(ch for ch in str(code) if ch.isdigit())
        if len(digits) != 6:
            skipped.append({"code": code, "reason": "not_cn_6digit"})
            continue
        try:
            series = fetch_cn_financial_series(digits, max_points=n_pts)
        except Exception as e:
            logger.exception('unexpected error in ingest_real_fundamentals_history')
            errors.append({"code": code, "reason": str(e)[:120]})
            continue
        if len(series) < 2:
            skipped.append(
                {
                    "code": code,
                    "reason": f"series_too_short:{len(series)}",
                }
            )
            continue

        path = snapshot_cache_path("fundamentals", code, store_dir)
        payload: Dict[str, Any] = {}
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError):
                payload = {}
        hist = list(payload.get("history") or [])
        fetched_at = datetime.now().isoformat(timespec="seconds")
        for pt in series:
            metrics = {
                k: pt.get(k)
                for k in (
                    "roe",
                    "profit_growth",
                    "revenue_growth",
                    "eps",
                    "pe",
                    "pe_ttm",
                    "pb",
                    "dividend_yield",
                )
                if pt.get(k) is not None
            }
            if not metrics:
                continue
            hist = merge_history_point(
                hist,
                as_of=str(pt.get("as_of") or "")[:10],
                metrics=metrics,
                fetched_at=fetched_at,
                data_source=str(pt.get("source") or "akshare_financial_indicator"),
                ann_date=pt.get("ann_date"),
                available_as_of=pt.get("available_as_of"),
            )
            # 确保真实点不带 synthetic 标记
            for h in hist:
                if str(h.get("as_of"))[:10] == str(pt.get("as_of") or "")[:10]:
                    h.pop("synthetic_demo", None)
                    if str(h.get("data_source") or "").startswith("synthetic_demo"):
                        h["data_source"] = "akshare_financial_indicator"

        if drop_synthetic:
            real_n = sum(
                1
                for h in hist
                if not (
                    (h or {}).get("synthetic_demo")
                    or str((h or {}).get("data_source") or "").startswith(
                        "synthetic_demo"
                    )
                )
            )
            if real_n >= 2:
                hist = [
                    h
                    for h in hist
                    if not (
                        (h or {}).get("synthetic_demo")
                        or str((h or {}).get("data_source") or "").startswith(
                            "synthetic_demo"
                        )
                    )
                ]

        real_after = sum(
            1
            for h in hist
            if not (
                (h or {}).get("synthetic_demo")
                or str((h or {}).get("data_source") or "").startswith("synthetic_demo")
            )
        )
        info = {
            "code": code,
            "series_points": len(series),
            "history_count": len(hist),
            "real_points": real_after,
        }
        if not write:
            updated.append(info)
            continue

        latest_metrics = {
            k: series[-1].get(k)
            for k in ("roe", "profit_growth", "revenue_growth", "eps")
            if series[-1].get(k) is not None
        }
        data_block = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if not isinstance(data_block, dict):
            data_block = {}
        metrics_block = dict(data_block.get("metrics") or {})
        metrics_block.update(latest_metrics)
        metrics_block["financial_as_of"] = str(series[-1].get("as_of") or "")[:10]
        data_block = {
            **data_block,
            "success": True,
            "stock_code": code,
            "metrics": metrics_block,
            "sources": list(
                dict.fromkeys(
                    list(data_block.get("sources") or [])
                    + ["akshare_financial_indicator"]
                )
            ),
        }
        payload.update(
            {
                "version": int(payload.get("version") or 1),
                "kind": "fundamentals",
                "code": code,
                "data_source": "akshare_financial_indicator",
                "fetched_at": fetched_at,
                "non_pit": True,
                "data": data_block,
                "history": hist,
                "latest_as_of": hist[-1].get("as_of") if hist else None,
                "history_ingested_at": fetched_at,
            }
        )
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        atomic_write_json(path, payload)
        updated.append(info)

    return {
        "ok": True,
        "written": bool(write),
        "updated": updated,
        "updated_count": len(updated),
        "skipped": skipped[:40],
        "errors": errors[:20],
        "note": "真实多期 history 入库；drop_synthetic 在 real≥2 时剔除 demo ladder。",
    }


def seed_fundamentals_history_ladder(
    *,
    codes: Optional[Sequence[str]] = None,
    quarters: int = 3,
    store_dir: Optional[str] = None,
    write: bool = True,
    day_step: int = 90,
) -> Dict[str, Any]:
    """
    演示用：在最新点之前复制 metrics 生成 earlier as_of（标 synthetic_demo）。
    禁止当作真实财报；仅验证 PIT 选取路径。
    """
    from core.fundamentals_pit import load_fundamentals_panel, merge_history_point
    from core.signal.fundamentals_bridge import normalize_fundamentals_metrics

    q = max(1, min(8, int(quarters or 3)))
    step = max(30, int(day_step or 90))
    cov = fundamentals_history_coverage(store_dir=store_dir, codes=codes)
    seeded: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []

    for row in cov.get("rows") or []:
        code = row["code"]
        if row.get("status") == "empty":
            skipped.append({"code": code, "reason": "empty"})
            continue
        path = snapshot_cache_path("fundamentals", code, store_dir)
        panel = load_fundamentals_panel(code, store_dir=store_dir)
        hist = list(panel.get("history") or [])
        if not hist:
            skipped.append({"code": code, "reason": "no_base_point"})
            continue
        latest = hist[-1]
        metrics = dict(latest.get("metrics") or {})
        if not metrics:
            try:
                with open(path, encoding="utf-8") as f:
                    payload0 = json.load(f)
                metrics = normalize_fundamentals_metrics(payload0.get("data")) or {}
            except (OSError, json.JSONDecodeError):
                metrics = {}
        if not metrics:
            skipped.append({"code": code, "reason": "no_metrics"})
            continue
        base_as_of = str(latest.get("as_of") or "")[:10]
        history = list(hist)
        added = 0
        for i in range(q, 0, -1):
            as_of = _date_offset(base_as_of, -i * step)
            if any(str(h.get("as_of"))[:10] == as_of for h in history):
                continue
            point_metrics = dict(metrics)
            history = merge_history_point(
                history,
                as_of=as_of,
                metrics=point_metrics,
                fetched_at=datetime.now().isoformat(timespec="seconds"),
                data_source="synthetic_demo_ladder",
            )
            # merge 不保留 demo 标记；补上
            for h in history:
                if str(h.get("as_of"))[:10] == as_of:
                    h["synthetic_demo"] = True
                    h["non_pit_origin"] = True
                    h["data_source"] = "synthetic_demo_ladder"
            added += 1

        if added <= 0:
            skipped.append({"code": code, "reason": "no_new_points"})
            continue
        if write:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            payload["history"] = history
            payload["latest_as_of"] = history[-1].get("as_of") if history else None
            payload["demo_ladder_at"] = datetime.now().isoformat(timespec="seconds")
            atomic_write_json(path, payload)
        seeded.append({"code": code, "added": added, "history_count": len(history)})

    return {
        "ok": True,
        "written": bool(write),
        "seeded": seeded,
        "seeded_count": len(seeded),
        "skipped": skipped[:40],
        "note": "synthetic_demo 点仅供 PIT 路径演示，非真实财报。",
    }


def seed_ttm_cycles(
    *,
    cycles: int = 3,
    path: Optional[str] = None,
    write: bool = True,
    idea_to_bt_hours: Sequence[float] = (2.0, 4.0, 6.0),
    bt_to_paper_hours: Sequence[float] = (3.0, 5.0, 8.0),
) -> Dict[str, Any]:
    """写入完整 idea→backtest→paper 闭环（带 cycle_id），抬 TTM 中位可算。"""
    from core.north_star import (
        TTM_EVENT_BACKTEST,
        TTM_EVENT_IDEA,
        TTM_EVENT_PAPER,
        TTM_EVENTS_PATH,
        compute_ttm_metrics,
    )

    n = max(1, min(20, int(cycles or 3)))
    p = path or TTM_EVENTS_PATH
    base = datetime.now() - timedelta(days=n * 2 + 1)
    written: List[Dict[str, Any]] = []

    for i in range(n):
        cid = f"seed-{uuid.uuid4().hex[:10]}"
        i2b = float(idea_to_bt_hours[i % len(idea_to_bt_hours)])
        b2p = float(bt_to_paper_hours[i % len(bt_to_paper_hours)])
        t_idea = base + timedelta(days=i * 2, hours=9)
        t_bt = t_idea + timedelta(hours=i2b)
        t_paper = t_bt + timedelta(hours=b2p)
        meta_base = {"cycle_id": cid, "seeded": True}

        entries = [
            (t_idea, TTM_EVENT_IDEA, "sample_ops_seed"),
            (t_bt, TTM_EVENT_BACKTEST, "sample_ops_seed"),
            (t_paper, TTM_EVENT_PAPER, "sample_ops_seed"),
        ]
        for ts, ev, ref in entries:
            entry = {
                "ts": ts.isoformat(timespec="seconds"),
                "event": ev,
                "ref": ref,
                "meta": dict(meta_base),
            }
            if write:
                os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            written.append(entry)

    metrics = compute_ttm_metrics(path=p) if write else compute_ttm_metrics(written)
    return {
        "ok": True,
        "written": bool(write),
        "cycles": n,
        "events_appended": len(written),
        "path": p,
        "ttm": {
            "status": metrics.get("status"),
            "median_idea_to_paper_hours": metrics.get("median_idea_to_paper_hours"),
            "pair_counts": metrics.get("pair_counts"),
        },
        "note": "seeded 事件带 meta.cycle_id；配对优先同 cycle。",
    }


def densify_paper_snapshots(
    paper: dict,
    *,
    target_days: int = 40,
    daily_drift: float = 0.0008,
    write_key: bool = True,
) -> Dict[str, Any]:
    """
    若 snapshots 过少，从最近权益向前插值日更点（标 densified=true）。
    仅用于抬滚动夏普可算；不改持仓。
    """
    snaps = list(paper.get("snapshots") or [])
    target = max(10, min(120, int(target_days or 40)))
    if len(snaps) >= target:
        return {
            "ok": True,
            "changed": False,
            "count": len(snaps),
            "note": "已足够密",
        }

    # 锚定权益
    equity = 1_000_000.0
    cash = equity
    for s in reversed(snaps):
        try:
            if s.get("equity") is not None:
                equity = float(s["equity"])
                cash = float(s.get("cash") if s.get("cash") is not None else equity)
                break
        except (TypeError, ValueError):
            continue

    need = target - len(snaps)
    start = datetime.now() - timedelta(days=need + len(snaps))
    new_snaps: List[dict] = []
    eq = equity / ((1.0 + daily_drift) ** max(1, need))
    for i in range(need):
        eq = eq * (1.0 + daily_drift)
        day = start + timedelta(days=i)
        new_snaps.append(
            {
                "ts": day.replace(hour=15, minute=0, second=0).isoformat(timespec="seconds"),
                "equity": round(eq, 2),
                "cash": round(cash, 2),
                "stock_value": round(max(0.0, eq - cash), 2),
                "total_pnl_pct": None,
                "position_count": 0,
                "densified": True,
            }
        )
    merged = (new_snaps + snaps)[-120:]
    if write_key:
        paper["snapshots"] = merged
        paper["snapshots_densified_at"] = datetime.now().isoformat(timespec="seconds")
    return {
        "ok": True,
        "changed": True,
        "added": need,
        "count": len(merged),
        "note": "插值日更仅供 KPI 演示；真实曲线仍靠 paper_daily。",
    }


def prune_densified_snapshots(
    paper: dict,
    *,
    write_key: bool = True,
) -> Dict[str, Any]:
    """剔除 densified=true 的插值快照，保留真实日更点（W2b）。"""
    snaps = list(paper.get("snapshots") or [])
    kept = [s for s in snaps if not (s or {}).get("densified")]
    removed = len(snaps) - len(kept)
    if write_key and removed:
        paper["snapshots"] = kept
        paper.pop("snapshots_densified_at", None)
        paper["snapshots_pruned_at"] = datetime.now().isoformat(timespec="seconds")
    return {
        "ok": True,
        "changed": bool(removed),
        "removed": removed,
        "count": len(kept),
        "note": "已剔除 densified；请继续真实 paper_daily 积累。",
    }


def sample_status(
    *,
    paper: Optional[dict] = None,
    store_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """一屏：TTM / PIT history / 纸面快照 / 拦截标注 覆盖（含 demo 纪律）。

    财务覆盖默认只扫 **验证宇宙**（观察池 / include_only），不把 store 里历史港股/脏键算进空码。
    """
    from core.north_star import compute_ttm_metrics, load_ttm_events, summarize_risk_blocks
    from core.paths import STORE_DIR
    from core.validation_universe import resolve_validation_codes

    ttm = compute_ttm_metrics()
    resolved = resolve_validation_codes()
    uni_codes = list(resolved.get("codes") or [])
    fund = fundamentals_history_coverage(store_dir=store_dir, codes=uni_codes)

    root = store_dir or os.path.join(STORE_DIR, "fundamentals")
    store_codes: List[str] = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if name.endswith(".json") and not name.startswith("."):
                store_codes.append(name[:-5])
    uni_set = set(uni_codes)
    orphans = [c for c in store_codes if c not in uni_set]

    snaps = list((paper or {}).get("snapshots") or [])
    snap_n = len(snaps)
    densified_n = sum(1 for s in snaps if (s or {}).get("densified"))
    rb = summarize_risk_blocks((paper or {}).get("operation_log") or [])

    evs = load_ttm_events(limit=300)
    seeded_n = sum(
        1
        for e in evs
        if ((e.get("meta") or {}).get("seeded") is True)
        or str(e.get("ref") or "").startswith("sample_ops")
    )
    real_ttm_n = max(0, len(evs) - seeded_n)

    discipline = {
        "demo_not_validation": True,
        "warnings": [],
    }
    if densified_n > 0:
        discipline["warnings"].append(
            f"纸面有 {densified_n} 条 densified 插值快照，不得单独当作策略已验证"
        )
    if seeded_n > 0:
        discipline["warnings"].append(
            f"TTM 有 {seeded_n} 条 seeded 事件，中位耗时含演示样本"
        )
    if int(fund.get("synthetic_multi_point") or 0) > 0:
        discipline["warnings"].append(
            f"财务 history 有 {fund.get('synthetic_multi_point')} 只主要靠 synthetic_demo 多点"
        )
    try:
        ann_ratio = fund.get("ann_missing_code_ratio")
        if ann_ratio is not None and float(ann_ratio) > 0.3:
            discipline["warnings"].append(
                f"ann_missing 码占比={ann_ratio}（{fund.get('ann_missing_codes')} 只）· "
                + str(fund.get("ingest_hint") or "请补公告日 / ingest-history")
            )
    except (TypeError, ValueError):
        pass
    if orphans:
        discipline["warnings"].append(
            f"store 有 {len(orphans)} 只不在验证宇宙："
            + ", ".join(orphans[:8])
            + ("…" if len(orphans) > 8 else "")
        )
    if not discipline["warnings"]:
        discipline["warnings"].append("未检测到明显演示污染标记")

    labeled = int(rb.get("labeled_count") or 0)
    eff = rb.get("effectiveness_rate")
    eff_ready = labeled >= 20

    from core.risk.block_outcome import unlabeled_digest

    outcome_unlabeled = unlabeled_digest((paper or {}).get("operation_log") or [], limit=15)

    return {
        "ok": True,
        "ttm": {
            "status": ttm.get("status"),
            "sample_count": ttm.get("sample_count"),
            "seeded_events": seeded_n,
            "real_events": real_ttm_n,
            "pair_counts": ttm.get("pair_counts"),
            "median_idea_to_paper_hours": ttm.get("median_idea_to_paper_hours"),
            "cycle_tagged_events": ttm.get("cycle_tagged_events"),
        },
        "fundamentals_history": {
            "total": fund.get("total"),
            "with_history": fund.get("with_history"),
            "multi_point": fund.get("multi_point"),
            "real_multi_point": fund.get("real_multi_point"),
            "synthetic_multi_point": fund.get("synthetic_multi_point"),
            "empty": fund.get("empty"),
            "empty_codes": fund.get("empty_codes") or [],
            "coverage": fund.get("coverage"),
            "real_multi_coverage": fund.get("real_multi_coverage"),
            "ann_missing_codes": fund.get("ann_missing_codes"),
            "ann_missing_points": fund.get("ann_missing_points"),
            "ann_missing_code_ratio": fund.get("ann_missing_code_ratio"),
            "ann_missing_top": __import__(
                "core.research.beta_accuracy", fromlist=["ann_missing_top_codes"]
            ).ann_missing_top_codes(fund, limit=20),
            "ingest_hint": fund.get("ingest_hint"),
            "universe_source": resolved.get("source"),
            "universe_count": len(uni_codes),
            "store_orphan_codes": orphans[:40],
            "store_orphan_count": len(orphans),
            "note": (
                "覆盖仅统计验证宇宙（观察池）；"
                "store 孤儿不计入空码。real_multi 不含仅 synthetic_demo ladder。"
            ),
        },
        "paper_snapshots": {
            "count": snap_n,
            "densified_count": densified_n,
            "real_count": max(0, snap_n - densified_n),
            "enough_for_sharpe": snap_n >= 20,
            "densified_at": (paper or {}).get("snapshots_densified_at"),
        },
        "risk_blocks": {
            "block_count": rb.get("block_count"),
            "labeled_count": labeled,
            "effectiveness_rate": eff if eff_ready else None,
            "effectiveness_pending": not eff_ready,
            "effectiveness_min_labeled": 20,
            "note": None
            if eff_ready
            else f"标注未达 20（当前 {labeled}），暂不展示有效率趋势",
        },
        "outcome_unlabeled": outcome_unlabeled,
        "discipline": discipline,
        "hints": _status_hints(ttm, fund, snap_n, rb, densified_n, seeded_n),
    }



def _status_hints(
    ttm, fund, snap_n, rb, densified_n: int = 0, seeded_n: int = 0
) -> List[str]:
    hints: List[str] = []
    if ttm.get("status") != "ok":
        hints.append("跑 sample_ops seed-ttm 或完成一次 promote 闭环")
    elif seeded_n and seeded_n >= int(ttm.get("sample_count") or 0) * 0.5:
        hints.append("TTM 仍偏 seeded；请用真实草稿/回测/promote 打点")
    if int(fund.get("real_multi_point") or 0) < 3:
        hints.append(
            "对 A 股跑 research/sample_ops_run.py ingest-history（多期真实财报；勿仅 seed-ladder）"
        )
    if int(fund.get("empty") or 0) > 0:
        hints.append(
            f"空财务 {fund.get('empty')} 只：补拉或移出验证宇宙（empty_codes）"
        )
    if snap_n < 20:
        hints.append("连续 paper_daily（densify 仅演示）")
    elif densified_n > snap_n * 0.5:
        hints.append("纸面快照过半为 densified，应用真实日更替换")
    if int(rb.get("block_count") or 0) and not rb.get("labeled_count"):
        hints.append("策略页标注 risk_block outcome")
    elif int(rb.get("labeled_count") or 0) < 20 and int(rb.get("block_count") or 0):
        hints.append("继续标注 outcome 至 ≥20 再解读有效率")
    if not hints:
        hints.append("样本覆盖可用；继续真实日更与财报拉取即可")
    return hints
