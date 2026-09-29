"""滚动可预测性票档：命中率为主、IC 为辅。

从 score_ledger 冻结行 + outcomes 按票聚合近 N 个决策日：
- hit_rate = 符号命中率（|ŷ| 死区外）
- n_valid = 有效决策日
- ic = 票内 ŷ vs 已实现 Spearman（辅）

默认池 = 观察池（有打分流水线的窄集）；勿默认扫整份研究宇宙（C 海淹没信号）。
可选 pool=research_universe / ledger（仅有账本样本的票）。

档位（默认）：
- A：hit≥0.55 且 n≥min_n
- B：n≥min_n 且 hit∈[0.50,0.55)；或 n 不足但 hit≥0.50（样本薄、方向尚可）
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
DEFAULT_A_HIT = 0.55
DEFAULT_B_HIT = 0.50
DEFAULT_LIVE_ALLOWED = ("A", "B")
# 票内 IC 最少成对点数
_IC_MIN_PAIRS = 5

HEAD_OO = "oo"
HEAD_TAU = "tau"

POOL_WATCHING = "watching"
POOL_RESEARCH = "research_universe"
POOL_LEDGER = "ledger"
DEFAULT_POOL = POOL_WATCHING


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
        "n_ledger_dates": src.get("n_ledger_dates"),
        "as_of_last": ((src.get("ledger_dates") or [None])[-1] if src.get("ledger_dates") else None),
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
    # 持仓不在 cleaned 里也并入（与旧 fit keep 语义一致）
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
        # 样本薄：很差直接 C，尚可暂放 B（不升 A）
        return "C" if hr < b_thr else "B"
    if hr >= a_thr:
        return "A"
    if hr >= b_thr:
        return "B"
    return "C"


def effective_min_n(requested: int, n_ledger_dates: int) -> int:
    """账本决策日短于门槛时下调，避免永远无 A。

    例：配置 min_n=20、账本仅 10 日 → 生效 9（满窗−1，允许缺 1 日 outcome）。
    """
    req = max(1, int(requested if requested is not None else DEFAULT_MIN_N))
    nd = max(0, int(n_ledger_dates or 0))
    if nd <= 0:
        return req
    if nd >= req:
        return req
    if nd <= 5:
        return max(1, nd)
    return max(5, nd - 1)


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
    if h in ("tau", "oc", "y_oc", "y_tau", "ŷ_oc", "ŷ_τ"):
        return HEAD_TAU
    return HEAD_OO


def _pick_outcome_fields(
    row: dict,
    oc: dict,
    *,
    head: str,
    yhat_eps: float,
) -> Tuple[Optional[float], Optional[float], Optional[bool]]:
    """返回 (yhat, realized, sign_hit)。"""
    from core.score_ledger.io import _to_float
    from core.score_ledger.outcomes import _sign_hit

    if head == HEAD_TAU:
        yhat = _to_float(row.get("yhat_tau"))
        if yhat is None:
            yhat = _to_float(oc.get("yhat_tau"))
        realized = _to_float(oc.get("realized_tau"))
        hit = oc.get("sign_hit_tau")
        if not isinstance(hit, bool):
            hit = _sign_hit(yhat, realized)
    else:
        yhat = _to_float(row.get("yhat"))
        realized = _to_float(oc.get("realized_h"))
        hit = oc.get("sign_hit")
        if not isinstance(hit, bool):
            hit = _sign_hit(yhat, realized)
    if yhat is not None and abs(float(yhat)) < float(yhat_eps):
        return yhat, realized, None  # 无方向，不计入命中
    return yhat, realized, hit if isinstance(hit, bool) else None


def _accumulate_by_code(
    *,
    lookback_dates: int,
    head: str,
    code_filter: Optional[set],
    ledger_dates: Optional[Sequence[str]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], List[str], Dict[str, str]]:
    from core.score_ledger import io as _lio

    if ledger_dates is not None:
        # 调用方给定窗（已按时间序或无序均可）
        raw = [str(d).strip()[:10] for d in ledger_dates if str(d or "").strip()]
        dates_asc = sorted(set(raw))
    else:
        lim = max(5, min(int(lookback_dates or DEFAULT_LOOKBACK_DATES), 90))
        dates = _lio.list_ledger_dates(limit=lim)
        # 时间正序便于展示 as_of_span
        dates_asc = list(reversed(dates))
    yhat_eps = float(getattr(_lio, "_YHAT_EPS", 0.05) or 0.05)

    acc: Dict[str, Dict[str, Any]] = {}
    name_by: Dict[str, str] = {}
    for d in dates_asc:
        led = _lio.load_ledger(d)
        if not led.get("success") or led.get("empty"):
            continue
        outcomes = _lio.load_outcomes(d)
        by_oc = outcomes.get("by_code") or {} if outcomes.get("success") else {}
        if not isinstance(by_oc, dict):
            by_oc = {}
        for r in led.get("rows") or []:
            if not isinstance(r, dict):
                continue
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            if code_filter is not None and code not in code_filter:
                continue
            if r.get("name") and code not in name_by:
                name_by[code] = str(r.get("name"))
            yhat, realized, hit = _pick_outcome_fields(
                r, by_oc.get(code) or {}, head=head, yhat_eps=yhat_eps
            )
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
            if yhat is not None and realized is not None:
                try:
                    slot["yhats"].append(float(yhat))
                    slot["realized"].append(float(realized))
                except (TypeError, ValueError):
                    pass
            slot["dates"].append(d)
    return acc, dates_asc, name_by


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


def split_holdout_windows(
    holdout_n: int,
    *,
    ledger_limit: int = 90,
) -> Dict[str, Any]:
    """近 ``holdout_n`` 个账本决策日取前半作分档窗。

    后半不参与分档（避免用最新段打档）；回测天数由独立 lookback 决定，不绑这段。
    返回 ``tier_dates``（升序）、``tier_n`` 等。账本不足 2 日则失败。
    """
    from core.score_ledger import io as _lio

    h = max(2, min(int(holdout_n or 20), 90))
    newest_first = list(_lio.list_ledger_dates(limit=max(h, min(int(ledger_limit), 90))))
    block_desc = newest_first[:h]  # 最新在前
    block = list(reversed(block_desc))  # 升序：旧→新
    if len(block) < 2:
        return {
            "ok": False,
            "error": f"账本决策日不足（有 {len(block)}，需≥2）",
            "holdout_n": h,
            "n_ledger": len(block),
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
        "n_ledger": len(block),
        "tier_dates": tier_dates,
        "tier_n": len(tier_dates),
        "as_of_tier_last": tier_dates[-1] if tier_dates else None,
    }


def _normalize_pool(pool: Optional[str]) -> str:
    p = str(pool or DEFAULT_POOL).strip().lower()
    if p in ("research", "research_universe", "ru", "universe", "日线", "研究宇宙"):
        return POOL_RESEARCH
    if p in ("ledger", "scored", "sample", "有样本", "账本"):
        return POOL_LEDGER
    return POOL_WATCHING


def build_holdout_half_tiers(
    holdout_n: int = 20,
    *,
    pool: str = DEFAULT_POOL,
    min_n: int = DEFAULT_MIN_N,
    a_hit: float = DEFAULT_A_HIT,
    b_hit: float = DEFAULT_B_HIT,
    head: str = HEAD_OO,
    watching_codes: Optional[Sequence[str]] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """研究枢纽协议：近 ``holdout_n`` 账本日前半分档。

    落盘 last（含 ``tier_dates``）；回测/live 复用档位过滤宇宙。
    回测天数由回测页 lookback 独立设置。live 须再「启用 live」。
    """
    split = split_holdout_windows(holdout_n)
    if not split.get("ok"):
        return {
            "success": False,
            "error": split.get("error") or "Holdout 切分失败",
            "protocol": "holdout_half",
            "holdout_split": split,
        }
    rep = build_predictability_tiers(
        pool=pool,
        ledger_dates=list(split.get("tier_dates") or []),
        min_n=min_n,
        a_hit=a_hit,
        b_hit=b_hit,
        head=head,
        watching_codes=watching_codes,
        persist=False,
    )
    if not isinstance(rep, dict) or not rep.get("success"):
        if isinstance(rep, dict):
            rep["protocol"] = "holdout_half"
            rep["holdout_split"] = split
        return rep
    tier_n = int(split.get("tier_n") or 0)
    rep["protocol"] = "holdout_half"
    rep["holdout_n"] = split.get("holdout_n")
    rep["tier_n"] = tier_n
    rep["tier_dates"] = list(split.get("tier_dates") or [])
    rep["as_of_tier_last"] = split.get("as_of_tier_last")
    rep["holdout_split"] = split
    rep["lookback_dates"] = tier_n
    rep["note"] = (
        f"Holdout 前半 {tier_n} 日分档；落盘 last；"
        "「启用 live」后调仓按 A+B 过滤；"
        "回测天数用独立 lookback；研究套须在 Holdout 前训练。"
    )
    if persist:
        try:
            save_predictability_tiers_last(rep)
        except Exception:  # noqa: BLE001
            logger.debug("persist holdout_half tiers failed", exc_info=True)
    return rep


def build_predictability_tiers(
    codes: Optional[Sequence[str]] = None,
    *,
    pool: str = DEFAULT_POOL,
    lookback_dates: int = DEFAULT_LOOKBACK_DATES,
    ledger_dates: Optional[Sequence[str]] = None,
    min_n: int = DEFAULT_MIN_N,
    a_hit: float = DEFAULT_A_HIT,
    b_hit: float = DEFAULT_B_HIT,
    head: str = HEAD_OO,
    watching_codes: Optional[Sequence[str]] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """滚动可预测性票档（影子）。

    ``codes`` 显式传入时优先；否则按 ``pool``：
    - watching（默认）：观察池
    - research_universe：研究宇宙解析名单
    - ledger：只保留近窗 score_ledger 有样本的票（不填无账本 C）

    ``ledger_dates`` 非空时只用这些决策日（Holdout 前半分档窗），忽略 lookback_dates 截取。

    不写 watching、不进 live 闸（须 promote）。
    """
    head_n = _normalize_head(head)
    pool_mode = _normalize_pool(pool)

    if watching_codes is None:
        try:
            from core.watching.store import read_watching

            watching_codes = list((read_watching() or {}).get("watchlist") or [])
        except Exception:  # noqa: BLE001
            logger.debug("predictability_tiers: read_watching failed", exc_info=True)
            watching_codes = []
    watch_list = [str(c).strip() for c in (watching_codes or []) if str(c).strip()]
    watch_set = set(watch_list)

    resolved: Dict[str, Any]
    pad_missing = True
    if codes is not None:
        seen = set()
        pool_codes: List[str] = []
        for c in codes:
            code = str(c or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            pool_codes.append(code)
        pool_source = "explicit"
        resolved = {"count": len(pool_codes), "source": pool_source, "codes": pool_codes}
    elif pool_mode == POOL_RESEARCH:
        from core.research_universe import resolve_research_codes

        resolved = resolve_research_codes()
        pool_codes = list(resolved.get("codes") or [])
        pool_source = str(resolved.get("source") or POOL_RESEARCH)
    elif pool_mode == POOL_LEDGER:
        pool_codes = []
        pool_source = POOL_LEDGER
        pad_missing = False
        resolved = {"count": 0, "source": POOL_LEDGER, "codes": []}
    else:
        pool_codes = list(watch_list)
        pool_source = POOL_WATCHING
        resolved = {
            "count": len(pool_codes),
            "source": POOL_WATCHING,
            "codes": pool_codes,
        }

    # ledger 池：不过滤 codes，事后只保留有样本的票
    if pool_mode == POOL_LEDGER and codes is None:
        code_filter = None
    else:
        code_filter = set(pool_codes) if pool_codes else None

    acc, dates_used, name_by = _accumulate_by_code(
        lookback_dates=lookback_dates,
        head=head_n,
        code_filter=code_filter,
        ledger_dates=ledger_dates,
    )

    if pool_mode == POOL_LEDGER and codes is None:
        # 只留有账本日的票；无样本不进表
        acc = {
            c: slot
            for c, slot in acc.items()
            if int(slot.get("n_days") or 0) > 0 or int(slot.get("n_valid") or 0) > 0
        }
        pool_codes = sorted(acc.keys())
        resolved = {
            "count": len(pool_codes),
            "source": POOL_LEDGER,
            "codes": pool_codes,
        }
    elif pad_missing and pool_codes:
        # 池内无账本命中的票也要出现（C / n=0）——观察池缺覆盖一目了然
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

    # A→B→C，同档 hit_rate 降序
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

    n_with_sample = sum(1 for r in rows if int(r.get("n_valid") or 0) > 0)
    report = {
        "success": True,
        "schema": SCHEMA,
        "task": "predictability_tiers",
        "live_hook": False,
        "head": head_n,
        "head_label": "ŷ_oc" if head_n == HEAD_TAU else "ŷ_oo",
        "lookback_dates": max(5, min(int(lookback_dates or DEFAULT_LOOKBACK_DATES), 90)),
        "min_n": min_n_req,
        "min_n_effective": min_n_eff,
        "thresholds": {
            "a_hit": float(a_hit if a_hit is not None else DEFAULT_A_HIT),
            "b_hit": float(b_hit if b_hit is not None else DEFAULT_B_HIT),
            "min_n": min_n_req,
            "min_n_effective": min_n_eff,
            "min_n_adapted": min_n_eff < min_n_req,
        },
        "pool": pool_mode if codes is None else "explicit",
        "pool_source": pool_source,
        "pool_count": len(pool_codes) if pool_codes else len(acc),
        "n_with_sample": n_with_sample,
        "ledger_dates": dates_used,
        "n_ledger_dates": len(dates_used),
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
            "影子可预测性分档：默认观察池（非整份研究宇宙）；"
            "命中率为主、票内 Spearman IC 为辅；"
            + (
                f"账本仅 {len(dates_used)} 日，n 门槛 {min_n_req}→{min_n_eff}；"
                if min_n_eff < min_n_req
                else ""
            )
            + "落盘 last；须「启用 live」后才进调仓闸。原料=score_ledger+outcomes。"
        ),
    }
    if persist:
        try:
            save_predictability_tiers_last(report)
        except Exception:  # noqa: BLE001
            logger.debug("persist predictability_tiers_last failed", exc_info=True)
    return report
