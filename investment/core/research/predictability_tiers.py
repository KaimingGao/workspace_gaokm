"""滚动可预测性票档：命中率为主、IC 为辅。

用研究套 ŷ_oo 在 Holdout（测试窗）前半上的 OOS 预估 vs 日线实现标签，按票聚合：
- hit_rate = 符号命中率（|ŷ| 死区外）
- n_valid = 有效决策日
- ic = 票内 ŷ vs 已实现 Spearman（辅）

默认池 = 观察池；可选 research_universe（pool=ledger 已废弃，等同 watching）。

档位（默认）：
- A：hit≥0.60 且 n≥min_n（min_n = 分档窗天数的 90%，向上取整）
- B：n≥min_n 且 hit∈[0.50,0.60)；或 n 不足但 hit≥0.50（样本薄、方向尚可）
- C：hit<0.50（含 n 不足但命中很差）或无有效样本

落盘：
- ``predictability_tiers_last.json``：影子报告（研究枢纽）
- ``predictability_tiers_active.json``：显式「启用 live」后调仓才按档过滤；持仓始终保留
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json

SCHEMA = "predictability_tiers_v1"
ACTIVE_SCHEMA = "predictability_tiers_active_v1"
DEFAULT_LOOKBACK_DATES = 40
DEFAULT_MIN_N = 20
DEFAULT_A_HIT = 0.60
DEFAULT_B_HIT = 0.50
# 升 A 的有效日数门槛 = 分档窗天数 × 该比例（向上取整）
MIN_N_WINDOW_FRAC = 0.9
DEFAULT_LIVE_ALLOWED = ("A", "B")
DEFAULT_HOLDOUT_N = 20
DEFAULT_PANEL_LOOKBACK = 120
DEFAULT_HORIZON_DAYS = 1
DEFAULT_RIDGE_LAMBDA = 1.0
_YHAT_EPS = 0.05
# 票内 IC 最少成对点数
_IC_MIN_PAIRS = 5

HEAD_OO = "oo"
HEAD_TAU = "tau"

POOL_WATCHING = "watching"
POOL_RESEARCH = "research_universe"
POOL_LEDGER = "ledger"  # 废弃：解析为 watching
DEFAULT_POOL = POOL_WATCHING

PROTOCOL_HOLDOUT_OOS = "holdout_oos_half"


def predictability_tiers_last_path() -> str:
    from core.paths import PREDICTABILITY_TIERS_LAST_PATH

    return PREDICTABILITY_TIERS_LAST_PATH


def predictability_tiers_active_path() -> str:
    from core.paths import PREDICTABILITY_TIERS_ACTIVE_PATH

    return PREDICTABILITY_TIERS_ACTIVE_PATH


def save_predictability_tiers_last(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    path = predictability_tiers_last_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_predictability_tiers_last() -> Optional[Dict[str, Any]]:
    import json

    path = predictability_tiers_last_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        return None
    except Exception:  # noqa: BLE001
        logger.debug("load predictability_tiers_last failed", exc_info=True)
        return None
    if isinstance(doc, dict) and doc.get("success"):
        return doc
    return None


def load_predictability_tiers_active() -> Optional[Dict[str, Any]]:
    """读取 live 启用的分档指针；无文件 / enabled=false → None。"""
    import json

    path = predictability_tiers_active_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        return None
    except Exception:  # noqa: BLE001
        logger.debug("load predictability_tiers_active failed", exc_info=True)
        return None
    if not isinstance(doc, dict):
        return None
    if doc.get("enabled") is False:
        return None
    report = doc.get("report")
    if isinstance(report, dict) and report.get("success"):
        return doc
    # 兼容：整份即报告
    if doc.get("success") and isinstance(doc.get("rows"), list):
        return {
            "schema": ACTIVE_SCHEMA,
            "enabled": True,
            "allowed_tiers": list(DEFAULT_LIVE_ALLOWED),
            "report": doc,
            "promoted_at": doc.get("promoted_at"),
        }
    return None


def promote_predictability_tiers_to_live(
    report: Optional[Dict[str, Any]] = None,
    *,
    allowed_tiers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """把影子报告写入 active，调仓闸生效。缺省读 last。"""
    from core.numbers import now_iso_utc

    src = report if isinstance(report, dict) else load_predictability_tiers_last()
    if not isinstance(src, dict) or not src.get("success"):
        return {
            "ok": False,
            "error": "无可用分档报告；请先跑「观察池分档」",
            "path": predictability_tiers_active_path(),
        }
    allow = [
        str(t).strip().upper()
        for t in (allowed_tiers if allowed_tiers is not None else DEFAULT_LIVE_ALLOWED)
        if str(t or "").strip()
    ] or list(DEFAULT_LIVE_ALLOWED)
    payload = {
        "schema": ACTIVE_SCHEMA,
        "enabled": True,
        "promoted_at": now_iso_utc(),
        "allowed_tiers": allow,
        "counts": src.get("counts"),
        "head": src.get("head"),
        "head_label": src.get("head_label"),
        "n_ledger_dates": src.get("n_ledger_dates") or src.get("tier_n"),
        "as_of_last": (
            src.get("as_of_tier_last")
            or ((src.get("tier_dates") or src.get("ledger_dates") or [None])[-1])
        ),
        "report": src,
        "note": "显式启用：调仓按 allowed_tiers 过滤新开；持仓保留。不改 watching。",
    }
    path = predictability_tiers_active_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, payload)
    try:
        from core.live_config_manifest import write_live_config_manifest

        write_live_config_manifest(note="predictability_tiers promote")
    except Exception:  # noqa: BLE001
        logger.debug("manifest after tier promote failed", exc_info=True)
    return {"ok": True, "path": path, "active": payload}


def clear_predictability_tiers_live() -> Dict[str, Any]:
    """关闭 live 分档闸（删 active 或写 enabled=false）。"""
    path = predictability_tiers_active_path()
    existed = os.path.isfile(path)
    try:
        if existed:
            os.remove(path)
    except OSError as e:
        return {"ok": False, "error": str(e), "path": path}
    try:
        from core.live_config_manifest import write_live_config_manifest

        write_live_config_manifest(note="predictability_tiers demote")
    except Exception:  # noqa: BLE001
        logger.debug("manifest after tier demote failed", exc_info=True)
    return {"ok": True, "path": path, "cleared": existed}


def live_tier_status() -> Dict[str, Any]:
    active = load_predictability_tiers_active()
    if not active:
        return {
            "enabled": False,
            "path": predictability_tiers_active_path(),
            "allowed_tiers": [],
            "counts": None,
            "promoted_at": None,
        }
    return {
        "enabled": True,
        "path": predictability_tiers_active_path(),
        "allowed_tiers": list(active.get("allowed_tiers") or DEFAULT_LIVE_ALLOWED),
        "counts": active.get("counts"),
        "promoted_at": active.get("promoted_at"),
        "head_label": active.get("head_label"),
        "as_of_last": active.get("as_of_last"),
        "n_ledger_dates": active.get("n_ledger_dates"),
    }


def filter_codes_by_predictability_live(
    codes: Sequence[str],
    *,
    keep: Sequence[str] = (),
) -> Tuple[List[str], Dict[str, Any]]:
    """调仓宇宙过滤：无 active → 不过滤；有则只留 allowed_tiers + keep（持仓）。"""
    cleaned = [str(c).strip() for c in (codes or []) if str(c).strip()]
    keep_set = {str(c).strip() for c in (keep or []) if str(c).strip()}
    active = load_predictability_tiers_active()
    if not active:
        return cleaned, {
            "predictability_live": False,
            "unrestricted": True,
            "n_in": len(cleaned),
            "n_kept": len(cleaned),
            "n_dropped": 0,
            "reason": "no_active",
            "allowed_tiers": [],
            "universe_fit_tiers": ["A", "B", "C"],
        }
    allow = [
        str(t).strip().upper()
        for t in (active.get("allowed_tiers") or DEFAULT_LIVE_ALLOWED)
        if str(t or "").strip()
    ] or list(DEFAULT_LIVE_ALLOWED)
    allow_codes = tier_code_set(active.get("report"), allow)
    out: List[str] = []
    seen = set()
    dropped = 0
    for c in cleaned:
        if c in seen:
            continue
        if c in keep_set or c in allow_codes:
            seen.add(c)
            out.append(c)
        else:
            dropped += 1
    for c in keep_set:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out, {
        "predictability_live": True,
        "unrestricted": False,
        "n_in": len(cleaned),
        "n_kept": len(out),
        "n_dropped": dropped,
        "allowed_tiers": allow,
        "universe_fit_tiers": allow,
        "reason": "predictability_active",
        "promoted_at": active.get("promoted_at"),
        "counts": active.get("counts"),
    }


def assign_tier(
    hit_rate: Optional[float],
    n_valid: int,
    *,
    min_n: int = DEFAULT_MIN_N,
    a_hit: float = DEFAULT_A_HIT,
    b_hit: float = DEFAULT_B_HIT,
) -> str:
    """按命中率 + 有效日数分档。

    n 不足时：命中 < b_hit → C；命中 ≥ b_hit → B（仍不够进 A）。
    """
    n = max(0, int(n_valid or 0))
    mn = max(1, int(min_n or DEFAULT_MIN_N))
    a_thr = float(a_hit if a_hit is not None else DEFAULT_A_HIT)
    b_thr = float(b_hit if b_hit is not None else DEFAULT_B_HIT)
    if b_thr > a_thr:
        b_thr = a_thr
    if n <= 0:
        return "C"
    if hit_rate is None or not math.isfinite(float(hit_rate)):
        return "C"
    hr = float(hit_rate)
    if n < mn:
        return "C" if hr < b_thr else "B"
    if hr >= a_thr:
        return "A"
    if hr >= b_thr:
        return "B"
    return "C"


def effective_min_n(requested: int, n_ledger_dates: int) -> int:
    """升 A 的 n 门槛 = 分档窗天数的 90%（向上取整），且不超过窗长。

    例：分档窗 10 日 → 生效 9；窗 20 日 → 生效 18。
    ``requested`` 仅在窗为空时回退。
    """
    nd = max(0, int(n_ledger_dates or 0))
    if nd <= 0:
        return max(1, int(requested if requested is not None else DEFAULT_MIN_N))
    need = max(1, int(math.ceil(nd * float(MIN_N_WINDOW_FRAC))))
    return min(need, nd)


def _spearman_ic(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < _IC_MIN_PAIRS or n != len(ys):
        return None

    def _ranks(vals: Sequence[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: float(vals[i]))
        ranks = [0.0] * n
        for r, i in enumerate(order):
            ranks[i] = float(r + 1)
        return ranks

    rx, ry = _ranks(xs), _ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denx = sum((a - mx) ** 2 for a in rx) ** 0.5
    deny = sum((b - my) ** 2 for b in ry) ** 0.5
    if denx < 1e-12 or deny < 1e-12:
        return None
    return round(num / (denx * deny), 4)


def _normalize_head(head: Optional[str]) -> str:
    h = str(head or HEAD_OO).strip().lower()
    if h in ("tau", "oc", "y_oc", "y_tau", "ŷ_oc", "ŷ_τ", "ŷ_τc"):
        return HEAD_TAU
    return HEAD_OO


def _sign_hit_pair(
    yhat: Optional[float], realized: Optional[float], *, yhat_eps: float = _YHAT_EPS
) -> Optional[bool]:
    if yhat is None or realized is None:
        return None
    try:
        yh = float(yhat)
        ry = float(realized)
    except (TypeError, ValueError):
        return None
    if abs(yh) < float(yhat_eps):
        return None
    if abs(ry) < 1e-12:
        return None
    return (yh > 0) == (ry > 0)


def _watching_name_map(
    watching_codes: Optional[Sequence[str]] = None,
) -> Dict[str, str]:
    """观察池代码→名称（优先落盘 watchlist_names，不强制打行情）。"""
    try:
        from core.watching.store import read_watching, watchlist_names_for
    except Exception:  # noqa: BLE001
        logger.debug("predictability_tiers: name map import failed", exc_info=True)
        return {}
    try:
        data = read_watching() or {}
    except Exception:  # noqa: BLE001
        logger.debug("predictability_tiers: read_watching failed", exc_info=True)
        return {}
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    saved = data.get("watchlist_names")
    names: List[str]
    if isinstance(saved, list) and len(saved) == len(wl) and any(
        str(x or "").strip() for x in saved
    ):
        names = [str(x or "").strip() for x in saved]
    else:
        try:
            # 仅用落盘/static 提示；watchlist_names_for 可能轻量解析
            names = watchlist_names_for(data)
        except Exception:  # noqa: BLE001
            logger.debug("predictability_tiers: watchlist_names_for failed", exc_info=True)
            names = [""] * len(wl)
    out: Dict[str, str] = {}
    for c, n in zip(wl, names):
        if c and n:
            out[c] = n
    # 显式传入的 watching_codes 若已在 map 中则保留；缺名不补网
    for c in watching_codes or []:
        code = str(c or "").strip()
        if code and code not in out:
            out.setdefault(code, "")
    return {k: v for k, v in out.items() if v}


def _normalize_pool(pool: Optional[str]) -> str:
    p = str(pool or DEFAULT_POOL).strip().lower()
    if p in ("research", "research_universe", "ru", "universe", "日线", "研究宇宙"):
        return POOL_RESEARCH
    # ledger 池已废弃：等同 watching
    return POOL_WATCHING


def split_holdout_half_dates(
    test_days: Sequence[str],
    *,
    holdout_n: int,
) -> Dict[str, Any]:
    """Holdout 测试日升序取前半作分档窗。"""
    h = max(2, min(int(holdout_n or DEFAULT_HOLDOUT_N), 90))
    block = sorted({str(d).strip()[:10] for d in (test_days or []) if str(d or "").strip()[:10]})
    if len(block) > h:
        block = block[-h:]
    if len(block) < 2:
        return {
            "ok": False,
            "error": f"Holdout 测试日不足（有 {len(block)}，需≥2）",
            "holdout_n": h,
            "n_test": len(block),
            "n_ledger": len(block),
            "test_days": block,
            "tier_dates": [],
            "tier_n": 0,
        }
    mid = max(1, len(block) // 2)
    if mid >= len(block):
        mid = len(block) - 1
    tier_dates = block[:mid]
    return {
        "ok": True,
        "holdout_n": h,
        "n_test": len(block),
        "n_ledger": len(block),
        "test_days": block,
        "tier_dates": tier_dates,
        "tier_n": len(tier_dates),
        "as_of_tier_last": tier_dates[-1] if tier_dates else None,
    }


def split_holdout_windows(
    holdout_n: int,
    *,
    test_days: Optional[Sequence[str]] = None,
    ledger_limit: int = 90,  # noqa: ARG001 — 兼容旧签名
) -> Dict[str, Any]:
    """兼容入口：给定 Holdout 测试日则切前半；未给则失败（须走日线 OOS）。"""
    if test_days is None:
        return {
            "ok": False,
            "error": "分档已改吃日线 Holdout OOS；请调用 build_holdout_half_tiers",
            "holdout_n": max(2, min(int(holdout_n or DEFAULT_HOLDOUT_N), 90)),
            "n_ledger": 0,
            "tier_dates": [],
            "tier_n": 0,
        }
    return split_holdout_half_dates(test_days, holdout_n=holdout_n)


def _resolve_pool_codes(
    *,
    codes: Optional[Sequence[str]],
    pool: str,
    watching_codes: Optional[Sequence[str]],
) -> Tuple[List[str], str, Dict[str, Any], set]:
    if watching_codes is None:
        try:
            from core.watching.store import read_watching

            watching_codes = list((read_watching() or {}).get("watchlist") or [])
        except Exception:  # noqa: BLE001
            logger.debug("predictability_tiers: read_watching failed", exc_info=True)
            watching_codes = []
    watch_list = [str(c).strip() for c in (watching_codes or []) if str(c).strip()]
    watch_set = set(watch_list)
    pool_mode = _normalize_pool(pool)

    if codes is not None:
        seen = set()
        pool_codes: List[str] = []
        for c in codes:
            code = str(c or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            pool_codes.append(code)
        resolved = {"count": len(pool_codes), "source": "explicit", "codes": pool_codes}
        return pool_codes, "explicit", resolved, watch_set

    if pool_mode == POOL_RESEARCH:
        from core.research_universe import resolve_research_codes

        resolved = resolve_research_codes()
        pool_codes = list(resolved.get("codes") or [])
        return (
            pool_codes,
            str(resolved.get("source") or POOL_RESEARCH),
            resolved,
            watch_set,
        )

    resolved = {
        "count": len(watch_list),
        "source": POOL_WATCHING,
        "codes": list(watch_list),
    }
    return list(watch_list), POOL_WATCHING, resolved, watch_set


def accumulate_holdout_oos_by_code(
    codes: Sequence[str],
    *,
    holdout_n: int = DEFAULT_HOLDOUT_N,
    lookback: int = DEFAULT_PANEL_LOOKBACK,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    yhat_eps: float = _YHAT_EPS,
) -> Dict[str, Any]:
    """日线面板训 ŷ_oo，Holdout 测试窗前半按票聚合命中。

    返回 ``ok`` / ``acc`` / ``tier_dates`` / ``split`` / ``split_meta`` 等。
    """
    from core.research.holdout import (
        calendar_dates_from_stock_bars,
        resolve_ridge_split,
    )
    from core.research.panel import collect_subscore_forward_panel
    from core.research.portfolio_bars import load_portfolio_stock_bars
    from core.signal.return_score import fit_return_model_from_panel
    from core.watching.store import WATCHING_MAX_SIZE

    use_codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    cap = max(3, int(WATCHING_MAX_SIZE))
    use_codes = use_codes[:cap]
    if len(use_codes) < 2:
        return {"ok": False, "error": "标的不足 2 只", "acc": {}, "tier_dates": []}

    h_req = max(2, min(int(holdout_n or DEFAULT_HOLDOUT_N), 90))
    hz = max(1, min(int(horizon_days or DEFAULT_HORIZON_DAYS), 10))
    stock_bars, failures, _fund = load_portfolio_stock_bars(
        use_codes,
        lookback=max(40, int(lookback or DEFAULT_PANEL_LOOKBACK)),
        fetch_fundamentals=False,
    )
    if len(stock_bars) < 2:
        return {
            "ok": False,
            "error": f"有效日线不足（失败 {len(failures)}）",
            "failures": (failures or [])[:8],
            "acc": {},
            "tier_dates": [],
        }

    all_xs: List[Dict[str, Any]] = []
    all_ys: List[float] = []
    all_dates: List[str] = []
    all_codes: List[str] = []
    for code, bars in stock_bars.items():
        xs, ys, dates = collect_subscore_forward_panel(
            bars,
            horizon_days=hz,
            stock_code=code,
            pit_fundamentals=False,
        )
        all_xs.extend(xs)
        all_ys.extend(ys)
        all_dates.extend(dates)
        all_codes.extend([code] * len(dates))

    if len(all_ys) < 8:
        return {
            "ok": False,
            "error": f"面板样本不足（{len(all_ys)}）",
            "acc": {},
            "tier_dates": [],
        }

    cal_items = [{"bars": bars} for bars in stock_bars.values()]
    train_idx, test_idx, split_meta = resolve_ridge_split(
        all_dates,
        holdout_trading_days=h_req,
        label_horizon_days=hz,
        calendar_dates=calendar_dates_from_stock_bars(cal_items),
    )
    if not test_idx:
        return {
            "ok": False,
            "error": "Holdout 切不出测试窗",
            "split_meta": split_meta,
            "acc": {},
            "tier_dates": [],
        }

    xs_tr = [all_xs[i] for i in train_idx]
    ys_tr = [all_ys[i] for i in train_idx]
    as_of = None
    for bars in stock_bars.values():
        if bars:
            as_of = str(bars[-1].get("date") or "") or None
            break

    research_model, research_report = fit_return_model_from_panel(
        xs_tr,
        ys_tr,
        horizon_days=hz,
        ridge_lambda=float(ridge_lambda if ridge_lambda is not None else DEFAULT_RIDGE_LAMBDA),
        fitted_as_of=as_of,
        min_samples=24,
    )
    if research_model is None:
        return {
            "ok": False,
            "error": (research_report or {}).get("error") or "Holdout 前训练失败",
            "report": research_report,
            "split_meta": split_meta,
            "acc": {},
            "tier_dates": [],
        }

    test_days_meta = list(split_meta.get("test_days") or [])
    if not test_days_meta:
        test_days_meta = sorted(
            {str(all_dates[i])[:10] for i in test_idx if str(all_dates[i] or "").strip()}
        )
    half = split_holdout_half_dates(test_days_meta, holdout_n=h_req)
    if not half.get("ok"):
        return {
            "ok": False,
            "error": half.get("error") or "Holdout 前半切分失败",
            "holdout_split": half,
            "split_meta": split_meta,
            "acc": {},
            "tier_dates": [],
        }
    tier_set = set(half.get("tier_dates") or [])

    acc: Dict[str, Dict[str, Any]] = {}
    for i in test_idx:
        d = str(all_dates[i])[:10]
        if d not in tier_set:
            continue
        code = str(all_codes[i] or "").strip()
        if not code:
            continue
        pred = research_model.predict(all_xs[i])
        y = all_ys[i]
        hit = _sign_hit_pair(pred, y, yhat_eps=yhat_eps)
        slot = acc.get(code)
        if slot is None:
            slot = {
                "hits": 0,
                "n_valid": 0,
                "n_days": 0,
                "yhats": [],
                "realized": [],
                "dates": [],
            }
            acc[code] = slot
        slot["n_days"] = int(slot["n_days"]) + 1
        if isinstance(hit, bool):
            slot["n_valid"] = int(slot["n_valid"]) + 1
            if hit:
                slot["hits"] = int(slot["hits"]) + 1
        if pred is not None and y is not None:
            try:
                slot["yhats"].append(float(pred))
                slot["realized"].append(float(y))
            except (TypeError, ValueError):
                pass
        slot["dates"].append(d)

    return {
        "ok": True,
        "acc": acc,
        "tier_dates": list(half.get("tier_dates") or []),
        "tier_n": int(half.get("tier_n") or 0),
        "holdout_split": half,
        "split_meta": split_meta,
        "n_train": len(train_idx),
        "n_test": len(test_idx),
        "stock_count": len(stock_bars),
        "codes_used": list(stock_bars.keys()),
        "failures": (failures or [])[:8],
        "horizon_days": hz,
        "ridge_lambda": float(ridge_lambda if ridge_lambda is not None else DEFAULT_RIDGE_LAMBDA),
        "source": "oo_holdout_oos",
    }


def tier_code_set(
    report: Optional[Dict[str, Any]],
    allowed: Optional[Sequence[str]] = None,
) -> set:
    """从分档报告取出允许档内的代码集合。默认 A+B。"""
    allow = {
        str(t).strip().upper()
        for t in (allowed if allowed is not None else ("A", "B"))
        if str(t or "").strip()
    }
    if not allow:
        allow = {"A", "B"}
    out: set = set()
    if not isinstance(report, dict):
        return out
    for r in report.get("rows") or []:
        if not isinstance(r, dict):
            continue
        tier = str(r.get("tier") or "").strip().upper()
        code = str(r.get("code") or "").strip()
        if code and tier in allow:
            out.add(code)
    return out


def _rows_from_acc(
    acc: Dict[str, Dict[str, Any]],
    *,
    pool_codes: Sequence[str],
    watch_set: set,
    pad_missing: bool,
    dates_used: Sequence[str],
    min_n: int,
    a_hit: float,
    b_hit: float,
    name_by: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], int, int]:
    name_by = name_by or {}
    if pad_missing and pool_codes:
        for c in pool_codes:
            if c not in acc:
                acc[c] = {
                    "hits": 0,
                    "n_valid": 0,
                    "n_days": 0,
                    "yhats": [],
                    "realized": [],
                    "dates": [],
                }

    rows: List[Dict[str, Any]] = []
    counts = {"A": 0, "B": 0, "C": 0}
    min_n_req = max(1, int(min_n or DEFAULT_MIN_N))
    min_n_eff = effective_min_n(min_n_req, len(dates_used))
    for code, slot in acc.items():
        n_valid = int(slot.get("n_valid") or 0)
        hits = int(slot.get("hits") or 0)
        hit_rate = round(hits / n_valid, 4) if n_valid > 0 else None
        ic = _spearman_ic(slot.get("yhats") or [], slot.get("realized") or [])
        tier = assign_tier(
            hit_rate,
            n_valid,
            min_n=min_n_eff,
            a_hit=a_hit,
            b_hit=b_hit,
        )
        counts[tier] = int(counts.get(tier) or 0) + 1
        ds = list(slot.get("dates") or [])
        rows.append(
            {
                "code": code,
                "name": name_by.get(code),
                "tier": tier,
                "hit_rate": hit_rate,
                "hits": hits,
                "n_valid": n_valid,
                "n_days": int(slot.get("n_days") or 0),
                "ic": ic,
                "in_watching": code in watch_set,
                "as_of_first": ds[0] if ds else None,
                "as_of_last": ds[-1] if ds else None,
            }
        )

    tier_rank = {"A": 0, "B": 1, "C": 2}

    def _sort_key(r: dict):
        hr = r.get("hit_rate")
        return (
            tier_rank.get(str(r.get("tier") or "C"), 9),
            -(float(hr) if hr is not None else -1.0),
            -int(r.get("n_valid") or 0),
            str(r.get("code") or ""),
        )

    rows.sort(key=_sort_key)
    return rows, counts, min_n_req, min_n_eff


def build_holdout_half_tiers(
    holdout_n: int = DEFAULT_HOLDOUT_N,
    *,
    pool: str = DEFAULT_POOL,
    min_n: int = DEFAULT_MIN_N,
    a_hit: float = DEFAULT_A_HIT,
    b_hit: float = DEFAULT_B_HIT,
    head: str = HEAD_OO,
    watching_codes: Optional[Sequence[str]] = None,
    codes: Optional[Sequence[str]] = None,
    persist: bool = True,
    lookback: int = DEFAULT_PANEL_LOOKBACK,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
) -> Dict[str, Any]:
    """研究枢纽协议：ŷ_oo 在 Holdout 前半 OOS 按票分档。

    落盘 last；回测/live 复用档位过滤宇宙。回测天数独立。live 须再「启用 live」。
    """
    head_n = _normalize_head(head)
    if head_n == HEAD_TAU:
        return {
            "success": False,
            "error": "观察池分档仅支持 ŷ_oo Holdout OOS（head=oo）",
            "protocol": PROTOCOL_HOLDOUT_OOS,
        }

    pool_codes, pool_source, resolved, watch_set = _resolve_pool_codes(
        codes=codes,
        pool=pool,
        watching_codes=watching_codes,
    )
    pool_mode = _normalize_pool(pool) if codes is None else "explicit"
    if len(pool_codes) < 2:
        return {
            "success": False,
            "error": "池内标的不足 2 只",
            "protocol": PROTOCOL_HOLDOUT_OOS,
            "pool": pool_mode,
        }

    packed = accumulate_holdout_oos_by_code(
        pool_codes,
        holdout_n=holdout_n,
        lookback=lookback,
        horizon_days=horizon_days,
        ridge_lambda=ridge_lambda,
    )
    if not packed.get("ok"):
        return {
            "success": False,
            "error": packed.get("error") or "Holdout OOS 分档失败",
            "protocol": PROTOCOL_HOLDOUT_OOS,
            "holdout_split": packed.get("holdout_split"),
            "split_meta": packed.get("split_meta"),
            "failures": packed.get("failures"),
        }

    acc = dict(packed.get("acc") or {})
    tier_dates = list(packed.get("tier_dates") or [])
    half = packed.get("holdout_split") or {}
    name_by = _watching_name_map(
        watching_codes if watching_codes is not None else list(watch_set or pool_codes)
    )
    rows, counts, min_n_req, min_n_eff = _rows_from_acc(
        acc,
        pool_codes=pool_codes,
        watch_set=watch_set,
        pad_missing=True,
        dates_used=tier_dates,
        min_n=min_n,
        a_hit=a_hit,
        b_hit=b_hit,
        name_by=name_by,
    )
    n_with_sample = sum(1 for r in rows if int(r.get("n_valid") or 0) > 0)
    tier_n = int(packed.get("tier_n") or len(tier_dates))
    rep: Dict[str, Any] = {
        "success": True,
        "schema": SCHEMA,
        "task": "predictability_tiers",
        "live_hook": False,
        "protocol": PROTOCOL_HOLDOUT_OOS,
        "source": "oo_holdout_oos",
        "head": HEAD_OO,
        "head_label": "ŷ_oo",
        "lookback_dates": tier_n,
        "min_n": min_n_req,
        "min_n_effective": min_n_eff,
        "thresholds": {
            "a_hit": float(a_hit if a_hit is not None else DEFAULT_A_HIT),
            "b_hit": float(b_hit if b_hit is not None else DEFAULT_B_HIT),
            "min_n": min_n_req,
            "min_n_effective": min_n_eff,
            "min_n_adapted": min_n_eff < min_n_req,
        },
        "pool": pool_mode,
        "pool_source": pool_source,
        "pool_count": len(pool_codes),
        "n_with_sample": n_with_sample,
        "ledger_dates": tier_dates,
        "n_ledger_dates": tier_n,
        "tier_dates": tier_dates,
        "tier_n": tier_n,
        "holdout_n": half.get("holdout_n") or max(2, min(int(holdout_n or DEFAULT_HOLDOUT_N), 90)),
        "as_of_tier_last": half.get("as_of_tier_last"),
        "holdout_split": half,
        "split_meta": packed.get("split_meta"),
        "n_train": packed.get("n_train"),
        "n_test": packed.get("n_test"),
        "horizon_days": packed.get("horizon_days"),
        "counts": counts,
        "rows": rows,
        "watching_overlap": {
            "A": sum(1 for r in rows if r.get("tier") == "A" and r.get("in_watching")),
            "B": sum(1 for r in rows if r.get("tier") == "B" and r.get("in_watching")),
            "C": sum(1 for r in rows if r.get("tier") == "C" and r.get("in_watching")),
        },
        "resolved": {
            "count": resolved.get("count"),
            "source": resolved.get("source"),
        },
        "note": (
            f"ŷ_oo Holdout OOS 前半 {tier_n} 日分档；落盘 last；"
            "「启用 live」后调仓按 A+B 过滤；"
            "回测天数用独立 lookback；研究套须在 Holdout 前训练。"
            + (
                f" 分档窗仅 {tier_n} 日，n 门槛 {min_n_req}→{min_n_eff}。"
                if min_n_eff < min_n_req
                else ""
            )
        ),
    }
    if persist:
        try:
            save_predictability_tiers_last(rep)
        except Exception:  # noqa: BLE001
            logger.debug("persist holdout_oos_half tiers failed", exc_info=True)
    return rep


def build_predictability_tiers(
    codes: Optional[Sequence[str]] = None,
    *,
    pool: str = DEFAULT_POOL,
    lookback_dates: int = DEFAULT_LOOKBACK_DATES,
    ledger_dates: Optional[Sequence[str]] = None,  # noqa: ARG001 — 废弃
    min_n: int = DEFAULT_MIN_N,
    a_hit: float = DEFAULT_A_HIT,
    b_hit: float = DEFAULT_B_HIT,
    head: str = HEAD_OO,
    watching_codes: Optional[Sequence[str]] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """兼容入口：转 ``build_holdout_half_tiers``（Holdout=lookback_dates）。

    ``ledger_dates`` 已忽略（分档不再读 score_ledger）。
    """
    return build_holdout_half_tiers(
        holdout_n=max(2, min(int(lookback_dates or DEFAULT_LOOKBACK_DATES), 90)),
        pool=pool,
        min_n=min_n,
        a_hit=a_hit,
        b_hit=b_hit,
        head=head,
        watching_codes=watching_codes,
        codes=codes,
        persist=persist,
    )
