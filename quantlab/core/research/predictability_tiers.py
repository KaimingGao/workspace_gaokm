"""滚动可预测性票档：Holdout 前 60 个交易日的猜涨技能定档。

定档模型只拟合这 60 日之前（交界再空 1 日），不覆盖研究套。交易打分仍用研究套。
定档技能 = 当场拟合的 oo / oo_rank / τc / τc_rank 有样本头等权（猜涨精度 − 上涨日比例）。
- 收益头 oo / τc：说涨 = 预估 > ε（默认 0.05pp）
- 排序头 oo_rank / τc_rank：说涨 = 预估高于当日截面中位数（可与收益头同预估）
- Score = 有样本头技能等权。收益大小不进分数。oo_rank 硬失败；其余头软跳过
- 单头说涨少于 8 次的不计入该头。没有分数 → C
- 有分数者按 Score 降序取 top ``a_count``→A，接着 ``b_count``→B，其余→C（默认 100 / 300）
- 表内：四头技能 + Rank_Score；档标题只留 Holdout Rank_Score 均值
- 档内 KPI「平均收益率」= Holdout 整段等权买持（close；无模型）

交易打分仍用已落盘研究套。定档用的 Ridge 只在内存里，不覆盖研究套或执行套。
面板拉 lookback 全窗（有研究套优先 Feature Lake 按日打分）。

默认池 = 观察池；可选 research_universe（其它 pool 名，含已废弃的 ledger，等同 watching）。

落盘：
- ``predictability_tiers_last.json``：影子报告（研究枢纽）
- ``predictability_tiers_active.json``：live 调仓闸；档位跟随历史回测「分档」勾选
  （有勾选且有 last → 截面 z 用整池，ŷ/名次与开仓只写允许档；不勾 → 不过滤）。
  持仓始终保留；掉出允许档则清仓。
"""

from __future__ import annotations

import logging
import math
import os
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_TIER_PROGRESS_LOCK = threading.Lock()
_TIER_PROGRESS: Dict[str, Any] = {"running": False, "phase": "", "detail": ""}


def set_tier_progress(phase: str, detail: str = "") -> None:
    """分档进行到哪一步。页面轮询这一句，不把规则写进状态条。"""
    with _TIER_PROGRESS_LOCK:
        _TIER_PROGRESS["running"] = True
        _TIER_PROGRESS["phase"] = str(phase or "").strip()
        _TIER_PROGRESS["detail"] = str(detail or "").strip()


def finish_tier_progress() -> None:
    with _TIER_PROGRESS_LOCK:
        _TIER_PROGRESS["running"] = False
        _TIER_PROGRESS["phase"] = ""
        _TIER_PROGRESS["detail"] = ""


def tier_progress_snapshot() -> Dict[str, Any]:
    with _TIER_PROGRESS_LOCK:
        return {
            "running": bool(_TIER_PROGRESS.get("running")),
            "phase": str(_TIER_PROGRESS.get("phase") or ""),
            "detail": str(_TIER_PROGRESS.get("detail") or ""),
        }


from core.io_atomic import atomic_write_json

SCHEMA = "predictability_tiers_v1"
ACTIVE_SCHEMA = "predictability_tiers_active_v1"
# Score 序截取：过 A 档 F1 门槛者 top a_count→A，接着 b_count→B，其余→C
DEFAULT_A_COUNT = 100
DEFAULT_B_COUNT = 300
DEFAULT_A_F1 = 0.20  # A 档 OO_F1
DEFAULT_TAU_A_F1 = 0.20  # A 档 TC_F1（有研究套时）
DEFAULT_REQUIRE_OO_R_POS = True  # A 档可选 OO_R+>0
DEFAULT_REQUIRE_TC_R_POS = True  # A 档可选 TC_R+>0（有 ŷ_τc 研究套时）
A_COUNT_MIN = 1
A_COUNT_MAX = 2000
B_COUNT_MIN = 0
B_COUNT_MAX = 5000


def clamp_a_count(raw: Any, default: int = DEFAULT_A_COUNT) -> int:
    try:
        n = int(round(float(raw)))
    except (TypeError, ValueError):
        n = int(default if default is not None else DEFAULT_A_COUNT)
    return max(A_COUNT_MIN, min(A_COUNT_MAX, n))


def clamp_b_count(raw: Any, default: int = DEFAULT_B_COUNT) -> int:
    try:
        n = int(round(float(raw)))
    except (TypeError, ValueError):
        n = int(default if default is not None else DEFAULT_B_COUNT)
    return max(B_COUNT_MIN, min(B_COUNT_MAX, n))


def clamp_f1_floor(raw: Any, default: float = DEFAULT_A_F1) -> float:
    """F1 比例门槛，返回 0–1。``0.2`` 与 ``20`` 都是 20%。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(default if default is not None else DEFAULT_A_F1)
    if not math.isfinite(v):
        v = float(default if default is not None else DEFAULT_A_F1)
    if v > 1.0:
        v = v / 100.0
    return max(0.0, min(1.0, v))


def _meets_f1_floor(raw: Any, floor: float) -> bool:
    """F1 是否过线（≥）；缺/非有限 → 否。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v + 1e-12 >= float(floor)


DEFAULT_LIVE_ALLOWED = ("A",)
DEFAULT_HOLDOUT_N = 20
DEFAULT_PANEL_LOOKBACK = 120
DEFAULT_HORIZON_DAYS = 1
DEFAULT_RIDGE_LAMBDA = 1.0
_YHAT_EPS = 0.05
# 票内 IC 最少成对点数
HEAD_OO = "oo"
HEAD_TAU = "tau"

POOL_WATCHING = "watching"
POOL_RESEARCH = "research_universe"
DEFAULT_POOL = POOL_WATCHING

# 主协议：Holdout 前 60 日猜涨技能定档 + Holdout 整段评测
PROTOCOL_HOLDOUT_OOS = "tier60_quad_upskill_holdout_eval"
TITLE_METRIC_WINDOW = "holdout"
SELECTION_WINDOW = "tier60_quad_upskill"
DEFAULT_TIER_WINDOW_DAYS = 60
DEFAULT_TIER_EMBARGO_DAYS = 1
DEFAULT_TIER_MIN_DAYS = 8
# 评测末日（Holdout 末）相对今天的最大日历日滞后（约 5 个交易日）
DEFAULT_TIER_AS_OF_MAX_LAG_DAYS = 7


def pool_codes_fingerprint(codes: Sequence[str]) -> str:
    """观察池代码集短指纹（排序后 SHA1 前 16）。"""
    import hashlib

    cleaned = sorted({str(c).strip() for c in (codes or []) if str(c).strip()})
    return hashlib.sha1(",".join(cleaned).encode("utf-8")).hexdigest()[:16]


def _parse_holdout_n(raw: Any) -> Optional[int]:
    """``holdout_n`` → 正整数；非法 → None。"""
    if raw is None or raw is False or raw == "":
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _parse_iso_date(raw: Any):
    """``YYYY-MM-DD`` → date；失败 → None。"""
    from datetime import datetime

    s = str(raw or "").strip()[:10]
    if len(s) < 10:
        return None, ""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date(), s
    except ValueError:
        return None, s


def validate_predictability_tiers_report(
    report: Optional[Dict[str, Any]],
    *,
    holdout_n: Optional[int] = None,
    pool_codes: Optional[Sequence[str]] = None,
    as_of_max_lag_days: int = DEFAULT_TIER_AS_OF_MAX_LAG_DAYS,
    require_pool_fp: bool = True,
) -> Dict[str, Any]:
    """回测 / live 套用分档前的硬校验。失败须重跑「观察池分档」。

    协议 / 评测窗须显式且匹配；新鲜度只认评测末日（不用定档末日兜底）。
    ``require_pool_fp``：报告须有 ``pool_codes_fp``；若再传 ``pool_codes`` 则比对指纹。
    """
    from datetime import date

    if not isinstance(report, dict) or not report.get("success"):
        return {
            "ok": False,
            "error": "无枢纽分档报告；请先在研究枢纽跑「观察池分档」",
        }
    protocol = str(report.get("protocol") or "").strip()
    if protocol != PROTOCOL_HOLDOUT_OOS:
        return {
            "ok": False,
            "error": (
                f"分档报告协议缺失或过旧（{protocol or '空'}）；"
                "请重跑「观察池分档」（60 日猜涨技能定档 + Holdout 评测）"
            ),
            "protocol": protocol or None,
        }
    title_win = str(report.get("title_metric_window") or "").strip()
    if title_win != TITLE_METRIC_WINDOW:
        return {
            "ok": False,
            "error": (
                f"分档报告评测窗缺失或过旧（{title_win or '空'}）；"
                "请重跑「观察池分档」"
            ),
            "title_metric_window": title_win or None,
        }

    last_hold = _parse_holdout_n(report.get("holdout_n"))
    if report.get("holdout_n") is not None and last_hold is None:
        return {
            "ok": False,
            "error": "分档报告 holdout_n 无效；请重跑「观察池分档」",
            "holdout_n": report.get("holdout_n"),
        }
    req = _parse_holdout_n(holdout_n) if holdout_n is not None else None
    if holdout_n is not None and req is None:
        return {
            "ok": False,
            "error": f"请求 Holdout={holdout_n!r} 无效",
            "holdout_n_req": holdout_n,
        }
    if req is not None:
        if last_hold is None:
            return {
                "ok": False,
                "error": (
                    f"ŷ_oo Holdout={req} 但分档报告无 holdout_n；"
                    "请先在研究枢纽用当前 Holdout 重跑「观察池分档」。"
                ),
                "holdout_n_req": req,
            }
        if last_hold != req:
            return {
                "ok": False,
                "error": (
                    f"ŷ_oo Holdout={req} 与枢纽分档 Holdout={last_hold} 不一致；"
                    "请先在研究枢纽用当前 Holdout 重跑「观察池分档」。"
                ),
                "holdout_n": last_hold,
                "holdout_n_req": req,
            }

    # 新鲜度只认评测窗末日（Holdout 末）；禁止回落定档末日
    hs = report.get("holdout_split") if isinstance(report.get("holdout_split"), dict) else {}
    eval_days = list(hs.get("eval_dates") or hs.get("test_days") or [])
    fresh_raw = report.get("as_of_eval_last") or (eval_days[-1] if eval_days else None)
    fresh_d, fresh_s = _parse_iso_date(fresh_raw)
    if fresh_d is None:
        return {
            "ok": False,
            "error": "分档报告无评测末日；请重跑「观察池分档」",
            "as_of_eval_last": fresh_s or None,
        }
    try:
        max_lag = max(1, int(as_of_max_lag_days or DEFAULT_TIER_AS_OF_MAX_LAG_DAYS))
    except (TypeError, ValueError):
        max_lag = DEFAULT_TIER_AS_OF_MAX_LAG_DAYS
    lag = (date.today() - fresh_d).days
    if lag < -1:
        return {
            "ok": False,
            "error": (
                f"分档报告评测末日 {fresh_s} 晚于今天；请重跑「观察池分档」"
            ),
            "as_of_eval_last": fresh_s,
            "as_of_lag_days": lag,
        }
    if lag > max_lag:
        return {
            "ok": False,
            "error": (
                f"分档报告评测末日 {fresh_s} 已滞后 {lag} 日"
                f"（阈值 {max_lag}）；请重跑「观察池分档」"
            ),
            "as_of_eval_last": fresh_s,
            "as_of_lag_days": lag,
        }

    fp_rep = str(report.get("pool_codes_fp") or "").strip()
    if require_pool_fp:
        if not fp_rep:
            return {
                "ok": False,
                "error": "分档报告无池指纹；请重跑「观察池分档」",
                "pool_codes_fp": None,
            }
        if pool_codes is not None:
            fp_now = pool_codes_fingerprint(pool_codes)
            if fp_rep != fp_now:
                return {
                    "ok": False,
                    "error": (
                        "分档报告观察池与当前池不一致；请重跑「观察池分档」"
                    ),
                    "pool_codes_fp": fp_rep,
                    "pool_codes_fp_now": fp_now,
                }
    return {
        "ok": True,
        "protocol": protocol,
        "title_metric_window": title_win,
        "holdout_n": last_hold,
        "as_of_eval_last": fresh_s,
        "as_of_tier_last": report.get("as_of_tier_last"),
        "pool_codes_fp": fp_rep or report.get("pool_codes_fp"),
    }


def build_lookback_holdout_split(
    train_days: Sequence[str],
    test_days: Sequence[str],
    *,
    holdout_n: int,
    tier_window_days: int = DEFAULT_TIER_WINDOW_DAYS,
    embargo_days: int = DEFAULT_TIER_EMBARGO_DAYS,
) -> Dict[str, Any]:
    """定档窗=Holdout 前 ``tier_window_days`` 个交易日；评测窗=Holdout 整段。

    训练日与 Holdout 重叠的日子只留在评测窗。定档窗与评测窗、拟合窗与定档窗
    交界各空 ``embargo_days`` 个交易日，避免次日开盘标签跨窗。
    """
    h = max(2, min(int(holdout_n or DEFAULT_HOLDOUT_N), 90))
    try:
        tw = int(tier_window_days)
    except (TypeError, ValueError):
        tw = DEFAULT_TIER_WINDOW_DAYS
    tw = max(5, min(tw, 250))
    try:
        emb = int(embargo_days)
    except (TypeError, ValueError):
        emb = DEFAULT_TIER_EMBARGO_DAYS
    emb = max(0, min(emb, 5))
    train = sorted(
        {str(d).strip()[:10] for d in (train_days or []) if str(d or "").strip()[:10]}
    )
    test = sorted(
        {str(d).strip()[:10] for d in (test_days or []) if str(d or "").strip()[:10]}
    )
    overlap = sorted(set(train) & set(test))
    if overlap:
        drop = set(overlap)
        train = [d for d in train if d not in drop]
    empty = {
        "ok": False,
        "holdout_n": h,
        "n_test": len(test),
        "test_days": test,
        "tier_dates": [],
        "tier_n": 0,
        "fit_dates": [],
        "fit_n": 0,
        "eval_dates": test,
        "eval_n": len(test),
        "overlap_dropped": overlap,
        "embargo_days": emb,
        "tier_window_days": tw,
        "selection_window": SELECTION_WINDOW,
        "title_metric_window": TITLE_METRIC_WINDOW,
    }
    if len(test) < 1 or len(train) < 1:
        empty["error"] = (
            f"训练/Holdout 日不足（train={len(train)}, holdout={len(test)}"
            + (f", overlap_dropped={len(overlap)}" if overlap else "")
            + "）"
        )
        return empty
    body = list(train)
    if emb and len(body) <= emb:
        empty["error"] = f"训练日不够空出评测前 {emb} 日（train={len(body)}）"
        return empty
    embargo_before_test = body[-emb:] if emb else []
    body = body[:-emb] if emb else body
    if len(body) < tw + emb + 1:
        empty["error"] = (
            f"定档窗不足（需要 {tw} 日定档 + {emb} 日交界 + 拟合日，"
            f"评测前还剩 {len(body)} 日）"
        )
        return empty
    tier = body[-tw:]
    rest = body[:-tw]
    embargo_before_tier = rest[-emb:] if emb else []
    fit = rest[:-emb] if emb else rest
    if len(fit) < 1:
        empty["error"] = "拟合窗为空"
        return empty
    return {
        "ok": True,
        "holdout_n": h,
        "n_test": len(test),
        "test_days": test,
        "tier_dates": tier,
        "tier_n": len(tier),
        "fit_dates": fit,
        "fit_n": len(fit),
        "embargo_before_test": embargo_before_test,
        "embargo_before_tier": embargo_before_tier,
        "eval_dates": list(test),
        "eval_n": len(test),
        "overlap_dropped": overlap,
        "embargo_days": emb,
        "tier_window_days": tw,
        "as_of_tier_last": tier[-1],
        "selection_window": SELECTION_WINDOW,
        "title_metric_window": TITLE_METRIC_WINDOW,
    }


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
    # live 已开时刷新报告内容，保留回测勾选的 allowed_tiers（先硬校验再晋级）
    try:
        active = load_predictability_tiers_active()
        if active:
            refreshed = promote_predictability_tiers_to_live(
                report,
                allowed_tiers=active.get("allowed_tiers") or DEFAULT_LIVE_ALLOWED,
            )
            if not refreshed.get("ok"):
                logger.warning(
                    "skip refresh predictability_tiers active: %s",
                    refreshed.get("error") or "validate failed",
                )
    except Exception:  # noqa: BLE001
        logger.debug("refresh predictability_tiers active after last failed", exc_info=True)


_SLIM_ROW_KEYS = (
    "code",
    "name",
    "tier",
    "score",
    "hit_rate",
    "n_valid",
    "n_days",
    "hit_rate_tau",
    "n_valid_tau",
    "realized_pct",
    "mean_y_pos",
    "n_y_pos",
    "f1_pos",
    "precision_pos",
    "recall_pos",
    "mean_y_pos_tau",
    "n_y_pos_tau",
    "f1_pos_tau",
    "precision_pos_tau",
    "recall_pos_tau",
)


def slim_tier_rows(rows: Any) -> List[Dict[str, Any]]:
    """主表只需要档位与命中摘要，去掉 ŷ/实现序列。"""
    out: List[Dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({k: r.get(k) for k in _SLIM_ROW_KEYS})
    return out


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
    """把影子报告写入 active，调仓闸生效。缺省读 last。

    晋级前硬校验协议 / 评测窗 / 评测末日 / 池指纹字段（不对池快照，因枢纽跑档时才有全池）。
    """
    from core.numbers import now_iso_utc

    src = report if isinstance(report, dict) else load_predictability_tiers_last()
    if not isinstance(src, dict) or not src.get("success"):
        return {
            "ok": False,
            "error": "无可用分档报告；请先跑「观察池分档」",
            "path": predictability_tiers_active_path(),
        }
    gate = validate_predictability_tiers_report(
        src,
        require_pool_fp=True,
        pool_codes=None,
    )
    if not gate.get("ok"):
        return {
            "ok": False,
            "error": gate.get("error") or "分档报告校验失败；请重跑「观察池分档」",
            "path": predictability_tiers_active_path(),
            "gate": gate,
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
        "tier_n": src.get("tier_n"),
        "as_of_last": src.get("as_of_eval_last") or gate.get("as_of_eval_last"),
        "as_of_eval_last": src.get("as_of_eval_last") or gate.get("as_of_eval_last"),
        "pool_codes_fp": src.get("pool_codes_fp") or gate.get("pool_codes_fp"),
        "report": src,
        "note": "live 随历史回测分档勾选：调仓按 allowed_tiers 过滤新开；持仓掉出允许档则清仓。不改 watching。",
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


def sync_predictability_tiers_live(
    allowed_tiers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """历史回测「分档」勾选 → live 闸。空=关闭；非空=启用（须已有 last）。"""
    raw = [
        str(t).strip().upper()
        for t in (allowed_tiers or [])
        if str(t or "").strip()
    ]
    allow = [t for t in ("A", "B", "C") if t in set(raw)]
    if not allow:
        out = clear_predictability_tiers_live()
        return {**out, "synced": True, "enabled": False, "allowed_tiers": []}
    out = promote_predictability_tiers_to_live(allowed_tiers=allow)
    return {
        **out,
        "synced": True,
        "enabled": bool(out.get("ok")),
        "allowed_tiers": allow,
    }


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
        "tier_n": active.get("tier_n"),
    }


def filter_codes_by_predictability_live(
    codes: Sequence[str],
    *,
    keep: Sequence[str] = (),
    pool_codes_for_fp: Optional[Sequence[str]] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Live 分档闸。无 active 时原样返回，``tradable_codes`` 为 None。

    有 active 时返回列表 = 允许档 ∪ ``keep``（持仓）；勿用它缩小截面 z。
    ``tradable_codes``：允许写 ŷ/名次并开仓（不含仅因持仓保留的掉档票）。
    掉出允许档的持仓在 ``force_exit_held``。
    ``pool_codes_for_fp``：指纹比对用观察池（勿含仅因持仓并入的票）；缺省用 ``codes``。
    闸失败时 ``tradable_codes=None``（与「允许集为空列表」区分）。
    """
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
            "force_exit_held": [],
            "reason": "no_active",
            "allowed_tiers": [],
            "universe_fit_tiers": ["A", "B", "C"],
            "tradable_codes": None,
        }
    allow = [
        str(t).strip().upper()
        for t in (active.get("allowed_tiers") or DEFAULT_LIVE_ALLOWED)
        if str(t or "").strip()
    ] or list(DEFAULT_LIVE_ALLOWED)
    report = active.get("report") if isinstance(active.get("report"), dict) else None
    fp_codes = (
        pool_codes_for_fp if pool_codes_for_fp is not None else cleaned
    )
    gate = validate_predictability_tiers_report(
        report,
        pool_codes=fp_codes,
        require_pool_fp=True,
    )
    if not gate.get("ok"):
        return cleaned, {
            "predictability_live": True,
            "unrestricted": False,
            "gate_ok": False,
            "gate_error": gate.get("error") or "分档报告校验失败",
            "n_in": len(cleaned),
            "n_kept": len(cleaned),
            "n_dropped": 0,
            "force_exit_held": [],
            "n_force_exit_held": 0,
            "allowed_tiers": allow,
            "universe_fit_tiers": allow,
            "tradable_codes": None,
            "reason": "gate_rejected",
            "promoted_at": active.get("promoted_at"),
            "counts": active.get("counts"),
            "gate": gate,
        }
    allow_codes = tier_code_set(report, allow)
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
    force_exit_held = sorted(c for c in keep_set if c not in allow_codes)
    return out, {
        "predictability_live": True,
        "unrestricted": False,
        "gate_ok": True,
        "n_in": len(cleaned),
        "n_kept": len(out),
        "n_dropped": dropped,
        "force_exit_held": force_exit_held,
        "n_force_exit_held": len(force_exit_held),
        "allowed_tiers": allow,
        "universe_fit_tiers": allow,
        "tradable_codes": sorted(allow_codes),
        "reason": "predictability_active",
        "promoted_at": active.get("promoted_at"),
        "counts": active.get("counts"),
        "gate": gate,
    }


def _finite_f1(raw: Any) -> Optional[float]:
    """上涨正类 F1；缺失或非有限 → None（合成 Score 时按 0）。"""
    try:
        v = float(raw) if raw is not None else float("nan")
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def assign_tiers_by_rank_score(
    rows: Sequence[Dict[str, Any]],
    *,
    require_tau: bool = False,
    a_count: int = DEFAULT_A_COUNT,
    b_count: int = DEFAULT_B_COUNT,
    a_f1: Optional[float] = None,
    tau_a_f1: Optional[float] = None,
    require_oo_r_pos: bool = DEFAULT_REQUIRE_OO_R_POS,
    require_tc_r_pos: bool = DEFAULT_REQUIRE_TC_R_POS,
) -> None:
    """遗留 F1 定档（仅测试保留）。现网用 ``assign_tiers_by_lift``。

    Score = 0.5*OO_F1 + 0.5*TC_F1（无 ŷ_τc 时 Score = OO_F1）。

    - 有 ŷ_τc（``require_tau``）：缺一侧 F1 按 0；两侧都缺 → 无 Score，档 C
    - 无 ŷ_τc：Score = OO_F1；缺 OO_F1 → C（TC_F1 不计入）
    - A：OO_F1 ≥ ``a_f1``（默认 20%）；``require_tau`` 时还须 TC_F1 ≥ ``tau_a_f1``；
      ``require_oo_r_pos`` 时还须 ``mean_y_pos``（OO_R+）> 0；
      ``require_tc_r_pos`` 且 ``require_tau`` 时还须 ``mean_y_pos_tau``（TC_R+）> 0；
      （缺/非有限/不过线不占 A；两侧勾选则须都过）。命中率（OO_ACC/TC_ACC）不进门槛
    - 过门槛者按 Score 降序取 top ``a_count``（默认 100）→ A；未过门槛不占 A 名额
    - 其余按 Score 接着 ``b_count``（默认 300）→ B；再其余 → C
    """
    n_a = clamp_a_count(a_count)
    n_b = clamp_b_count(b_count)
    oo_raw = a_f1
    tau_raw = tau_a_f1
    oo_floor = clamp_f1_floor(
        DEFAULT_A_F1 if oo_raw is None else oo_raw, DEFAULT_A_F1
    )
    tau_floor = clamp_f1_floor(
        DEFAULT_TAU_A_F1 if tau_raw is None else tau_raw, DEFAULT_TAU_A_F1
    )
    need_oo_r_pos = bool(require_oo_r_pos)
    need_tc_r_pos = bool(require_tc_r_pos) and bool(require_tau)

    scored: List[Tuple[float, str, Dict[str, Any]]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        oo_f1 = _finite_f1(r.get("f1_pos"))
        tau_f1 = _finite_f1(r.get("f1_pos_tau"))
        if require_tau:
            if oo_f1 is None and tau_f1 is None:
                r["tier"] = "C"
                r["score"] = None
                continue
            score = 0.5 * (0.0 if oo_f1 is None else oo_f1) + 0.5 * (
                0.0 if tau_f1 is None else tau_f1
            )
        elif oo_f1 is None:
            r["tier"] = "C"
            r["score"] = None
            continue
        else:
            score = oo_f1
        r["score"] = round(float(score), 6)
        scored.append((float(score), str(r.get("code") or ""), r))

    if not scored:
        return

    scored.sort(key=lambda t: (-t[0], t[1]))

    def _clears_a(row: Dict[str, Any]) -> bool:
        if not _meets_f1_floor(row.get("f1_pos"), oo_floor):
            return False
        if require_tau and not _meets_f1_floor(row.get("f1_pos_tau"), tau_floor):
            return False
        def _r_pos(key: str) -> float:
            try:
                return (
                    float(row.get(key))
                    if row.get(key) is not None
                    else float("nan")
                )
            except (TypeError, ValueError):
                return float("nan")

        if need_oo_r_pos:
            oo_r = _r_pos("mean_y_pos_raw")
            if not math.isfinite(oo_r):
                oo_r = _r_pos("mean_y_pos")
            if not (math.isfinite(oo_r) and oo_r > 0.0):
                return False
        if need_tc_r_pos:
            tc_r = _r_pos("mean_y_pos_tau_raw")
            if not math.isfinite(tc_r):
                tc_r = _r_pos("mean_y_pos_tau")
            if not (math.isfinite(tc_r) and tc_r > 0.0):
                return False
        return True

    a_left = n_a
    b_left = n_b
    for _score, _code, r in scored:
        if a_left > 0 and _clears_a(r):
            r["tier"] = "A"
            a_left -= 1
        elif b_left > 0:
            r["tier"] = "B"
            b_left -= 1
        else:
            r["tier"] = "C"


def attach_holdout_scores(
    rows: Sequence[Dict[str, Any]],
    combined: Mapping[str, Mapping[str, Any]],
) -> None:
    """写入 Holdout 猜涨技能。不改档位，也不改定档用的 Rank_Score。"""
    for r in rows:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code") or "")
        meta = combined.get(code) if code else None
        r["score_holdout"] = None
        if not isinstance(meta, dict) or meta.get("score") is None:
            continue
        try:
            score = float(meta["score"])
        except (TypeError, ValueError):
            continue
        if math.isfinite(score):
            r["score_holdout"] = round(score, 6)


_SKILL_HEAD_PREFIXES = ("oo_rank", "oo", "tc_rank", "tc")
_SKILL_ROW_KEYS = tuple(
    f"{kind}_{prefix}"
    for prefix in _SKILL_HEAD_PREFIXES
    for kind in ("skill", "precision", "base", "n_up")
)


def _normalize_call_mode(call_mode: Optional[str]) -> str:
    mode = str(call_mode or "abs").strip().lower()
    if mode in ("cs", "rank", "cs_rank", "median", "cross_section"):
        return "cs"
    return "abs"


def up_precision_lift_by_code(
    day_rows: Sequence[Tuple[str, Sequence[Tuple[str, Any, Any]]]],
    *,
    min_up_calls: int = DEFAULT_TIER_MIN_DAYS,
    yhat_eps: float = _YHAT_EPS,
    call_mode: str = "abs",
) -> Dict[str, Dict[str, Any]]:
    """猜涨技能 = P(次日涨 | 说涨) − P(次日涨)。

    ``call_mode``:
      - ``abs``：说涨 = 预估 > ε（收益头 oo / τc）
      - ``cs``：说涨 = 预估 > 当日截面中位数（排序头 oo_rank / τc_rank）

    同一套预估可出两套技能；说涨少于 ``min_up_calls`` 不入结果。收益大小不进分数。
    上涨日比例用该票定档窗里所有有标签的日子，不限说涨日。
    """
    try:
        need = int(min_up_calls)
    except (TypeError, ValueError):
        need = DEFAULT_TIER_MIN_DAYS
    need = max(1, need)
    mode = _normalize_call_mode(call_mode)
    try:
        eps = float(yhat_eps)
    except (TypeError, ValueError):
        eps = float(_YHAT_EPS)
    if not math.isfinite(eps) or eps < 0.0:
        eps = float(_YHAT_EPS)
    labeled: Dict[str, int] = {}
    up_days: Dict[str, int] = {}
    calls: Dict[str, int] = {}
    hits: Dict[str, int] = {}
    for _date, rows in day_rows or []:
        day_pts: List[Tuple[str, float, float]] = []
        for code, score, y in rows or []:
            try:
                yy = float(y)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(yy):
                continue
            c = str(code or "").strip()
            if not c:
                continue
            labeled[c] = labeled.get(c, 0) + 1
            rose = yy > 1e-12
            if rose:
                up_days[c] = up_days.get(c, 0) + 1
            try:
                sc = float(score)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(sc):
                continue
            day_pts.append((c, sc, yy))
        if not day_pts:
            continue
        if mode == "cs":
            if len(day_pts) < 2:
                continue
            preds = sorted(sc for _c, sc, _yy in day_pts)
            n_p = len(preds)
            mid = n_p // 2
            if n_p % 2 == 1:
                med = float(preds[mid])
            else:
                med = 0.5 * (float(preds[mid - 1]) + float(preds[mid]))
            for c, sc, yy in day_pts:
                if sc <= med:
                    continue
                calls[c] = calls.get(c, 0) + 1
                if yy > 1e-12:
                    hits[c] = hits.get(c, 0) + 1
        else:
            for c, sc, yy in day_pts:
                if sc <= eps:
                    continue
                calls[c] = calls.get(c, 0) + 1
                if yy > 1e-12:
                    hits[c] = hits.get(c, 0) + 1
    out: Dict[str, Dict[str, Any]] = {}
    for code, n_calls in calls.items():
        n_lab = int(labeled.get(code) or 0)
        if n_calls < need or n_lab < 1:
            continue
        precision = float(hits.get(code) or 0) / float(n_calls)
        base = float(up_days.get(code) or 0) / float(n_lab)
        out[code] = {
            "lift": precision - base,
            "precision": precision,
            "base_rate": base,
            "n_up": int(n_calls),
            "n_labeled": n_lab,
            "call_mode": mode,
        }
    return out


def display_metrics_from_day_rows(
    day_rows: Sequence[Tuple[str, Sequence[Tuple[str, Any, Any]]]],
    *,
    yhat_eps: float = _YHAT_EPS,
) -> Dict[str, Dict[str, Any]]:
    """定档窗排序头日预估 → 表内 R+ / ACC / P / R / F1（死区与 absorb 一致）。"""
    try:
        eps = float(yhat_eps)
    except (TypeError, ValueError):
        eps = float(_YHAT_EPS)
    if not math.isfinite(eps) or eps < 0.0:
        eps = float(_YHAT_EPS)
    slots: Dict[str, Dict[str, Any]] = {}
    for _date, rows in day_rows or []:
        for code, pred, y in rows or []:
            c = str(code or "").strip()
            if not c:
                continue
            slot = slots.get(c)
            if slot is None:
                slot = {
                    "hits": 0,
                    "n_valid": 0,
                    "n_days": 0,
                    "sum_y_pos": 0.0,
                    "n_y_pos": 0,
                    "tp_pos": 0,
                    "fp_pos": 0,
                    "fn_pos": 0,
                }
                slots[c] = slot
            slot["n_days"] = int(slot["n_days"]) + 1
            hit = _sign_hit_pair(pred, y, yhat_eps=eps)
            try:
                yh = float(pred) if pred is not None else float("nan")
                ry = float(y) if y is not None else float("nan")
            except (TypeError, ValueError):
                yh = float("nan")
                ry = float("nan")
            if isinstance(hit, bool):
                slot["n_valid"] = int(slot["n_valid"]) + 1
                if hit:
                    slot["hits"] = int(slot["hits"]) + 1
                if math.isfinite(yh) and math.isfinite(ry):
                    pred_up = yh > 0.0
                    act_up = ry > 0.0
                    if pred_up and act_up:
                        slot["tp_pos"] = int(slot["tp_pos"]) + 1
                    elif pred_up and not act_up:
                        slot["fp_pos"] = int(slot["fp_pos"]) + 1
                    elif (not pred_up) and act_up:
                        slot["fn_pos"] = int(slot["fn_pos"]) + 1
            if math.isfinite(yh) and math.isfinite(ry) and yh > eps:
                slot["sum_y_pos"] = float(slot["sum_y_pos"]) + ry
                slot["n_y_pos"] = int(slot["n_y_pos"]) + 1
    out: Dict[str, Dict[str, Any]] = {}
    for code, slot in slots.items():
        n_valid = int(slot.get("n_valid") or 0)
        hits = int(slot.get("hits") or 0)
        hit_rate = round(hits / n_valid, 4) if n_valid > 0 else None
        n_y_pos = max(0, int(slot.get("n_y_pos") or 0))
        sum_y_pos = float(slot.get("sum_y_pos") or 0.0)
        mean_y_pos_raw = (sum_y_pos / n_y_pos) if n_y_pos > 0 else None
        mean_y_pos = (
            round(float(mean_y_pos_raw), 4) if mean_y_pos_raw is not None else None
        )
        f1_pos, precision_pos, recall_pos = _f1_pos_from_counts(
            int(slot.get("tp_pos") or 0),
            int(slot.get("fp_pos") or 0),
            int(slot.get("fn_pos") or 0),
        )
        out[code] = {
            "hit_rate": hit_rate,
            "n_valid": n_valid,
            "n_days": int(slot.get("n_days") or 0),
            "mean_y_pos": mean_y_pos,
            "mean_y_pos_raw": mean_y_pos_raw,
            "n_y_pos": n_y_pos,
            "f1_pos": f1_pos,
            "precision_pos": precision_pos,
            "recall_pos": recall_pos,
        }
    return out


_OO_DISPLAY_KEYS = (
    "hit_rate",
    "n_valid",
    "n_days",
    "mean_y_pos",
    "mean_y_pos_raw",
    "n_y_pos",
    "f1_pos",
    "precision_pos",
    "recall_pos",
)
_OO_DISPLAY_CLEAR = {
    "hit_rate": None,
    "n_valid": 0,
    "n_days": 0,
    "mean_y_pos": None,
    "mean_y_pos_raw": None,
    "n_y_pos": 0,
    "f1_pos": None,
    "precision_pos": None,
    "recall_pos": None,
}
_TC_DISPLAY_CLEAR = {
    "hit_rate_tau": None,
    "n_valid_tau": 0,
    "mean_y_pos_tau": None,
    "mean_y_pos_tau_raw": None,
    "n_y_pos_tau": 0,
    "f1_pos_tau": None,
    "precision_pos_tau": None,
    "recall_pos_tau": None,
}


def attach_tier_head_display(
    rows: Sequence[Dict[str, Any]],
    oo_by: Optional[Mapping[str, Mapping[str, Any]]] = None,
    tc_by: Optional[Mapping[str, Mapping[str, Any]]] = None,
    *,
    write_tc: bool = True,
) -> None:
    """用定档窗 oo_rank / τc_rank 覆盖表内展示列；不碰 Holdout 标题指标。"""
    oo_map = oo_by if isinstance(oo_by, Mapping) else {}
    tc_map = tc_by if isinstance(tc_by, Mapping) else {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code") or "").strip()
        oo = oo_map.get(code) if code else None
        if isinstance(oo, dict):
            for key in _OO_DISPLAY_KEYS:
                if key in oo:
                    r[key] = oo[key]
        else:
            r.update(_OO_DISPLAY_CLEAR)
        if write_tc:
            tc = tc_map.get(code) if code else None
            if isinstance(tc, dict):
                # display_metrics 产出无 _tau 后缀；映射到行字段
                r["hit_rate_tau"] = tc.get("hit_rate")
                r["n_valid_tau"] = int(tc.get("n_valid") or 0)
                r["mean_y_pos_tau"] = tc.get("mean_y_pos")
                r["mean_y_pos_tau_raw"] = tc.get("mean_y_pos_raw")
                r["n_y_pos_tau"] = int(tc.get("n_y_pos") or 0)
                r["f1_pos_tau"] = tc.get("f1_pos")
                r["precision_pos_tau"] = tc.get("precision_pos")
                r["recall_pos_tau"] = tc.get("recall_pos")
            else:
                r.update(_TC_DISPLAY_CLEAR)
        else:
            r.update(_TC_DISPLAY_CLEAR)


def combine_head_lifts(
    maps: Optional[Mapping[str, Mapping[str, Mapping[str, Any]]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """有 lift 的头等权平均；都没有则不入结果。

    ``maps`` 键用 ``oo_rank`` / ``oo`` / ``tc_rank`` / ``tc``（也可扩展）。
    """
    srcs: List[Tuple[str, Mapping[str, Mapping[str, Any]]]] = []
    for prefix, raw in (maps or {}).items():
        key = str(prefix or "").strip()
        if not key or not isinstance(raw, Mapping):
            continue
        srcs.append((key, raw))
    combined: Dict[str, Dict[str, Any]] = {}
    if not srcs:
        return combined
    codes: set = set()
    for _prefix, src in srcs:
        codes |= set(src.keys())
    for code in codes:
        parts: List[float] = []
        meta: Dict[str, Any] = {}
        for prefix, src in srcs:
            pack = src.get(code)
            if not isinstance(pack, dict) or pack.get("lift") is None:
                continue
            try:
                lift = float(pack["lift"])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(lift):
                continue
            parts.append(lift)
            meta[f"skill_{prefix}"] = round(lift, 6)
            if pack.get("precision") is not None:
                meta[f"precision_{prefix}"] = round(float(pack["precision"]), 6)
            if pack.get("base_rate") is not None:
                meta[f"base_{prefix}"] = round(float(pack["base_rate"]), 6)
            if pack.get("n_up") is not None:
                meta[f"n_up_{prefix}"] = int(pack["n_up"])
        if not parts:
            continue
        meta["score"] = float(sum(parts) / len(parts))
        combined[str(code)] = meta
    return combined


def combine_head_lift(
    oo: Optional[Mapping[str, Mapping[str, Any]]] = None,
    tc: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """兼容旧双参：映射为 ``oo_rank`` / ``tc_rank`` 再等权。"""
    return combine_head_lifts({"oo_rank": dict(oo or {}), "tc_rank": dict(tc or {})})


def sort_rows_by_tier_score(rows: Sequence[Dict[str, Any]]) -> None:
    """档内按 Rank_Score 降序。没有分数的排在该档最后。"""
    tier_rank = {"A": 0, "B": 1, "C": 2}

    def _key(r: Dict[str, Any]):
        raw = r.get("score") if isinstance(r, dict) else None
        try:
            score = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        if score is None or not math.isfinite(score):
            score = None
        return (
            tier_rank.get(str((r or {}).get("tier") or "C"), 9),
            -(score if score is not None else -1.0),
            -int((r or {}).get("n_valid") or 0),
            str((r or {}).get("code") or ""),
        )

    if isinstance(rows, list):
        rows.sort(key=_key)


def assign_tiers_by_lift(
    rows: Sequence[Dict[str, Any]],
    combined: Mapping[str, Mapping[str, Any]],
    *,
    a_count: int = DEFAULT_A_COUNT,
    b_count: int = DEFAULT_B_COUNT,
) -> None:
    """按猜涨技能降序取 top ``a_count``→A、接着 ``b_count``→B，没有分数的进 C。"""
    n_a = clamp_a_count(a_count)
    n_b = clamp_b_count(b_count)
    scored: List[Tuple[float, str, Dict[str, Any]]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code") or "")
        meta = combined.get(code) if code else None
        r["tier"] = "C"
        r["score"] = None
        for key in _SKILL_ROW_KEYS:
            r.pop(key, None)
        if not isinstance(meta, dict) or meta.get("score") is None:
            continue
        try:
            score = float(meta["score"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score):
            continue
        r["score"] = round(score, 6)
        for key in _SKILL_ROW_KEYS:
            if meta.get(key) is not None:
                r[key] = meta.get(key)
        scored.append((score, code, r))
    scored.sort(key=lambda t: (-t[0], t[1]))
    a_left = n_a
    b_left = n_b
    for _score, _code, r in scored:
        if a_left > 0:
            r["tier"] = "A"
            a_left -= 1
        elif b_left > 0:
            r["tier"] = "B"
            b_left -= 1
        else:
            r["tier"] = "C"


def _predict_rank_days(
    model: Dict[str, Any],
    days: Sequence[Dict[str, Any]],
) -> List[Tuple[str, List[Tuple[str, Any, Any]]]]:
    from core.research.oo_rank_lambdarank import _predict_ridge_matrix

    out: List[Tuple[str, List[Tuple[str, Any, Any]]]] = []
    for day in days or []:
        if not isinstance(day, dict):
            continue
        codes = list(day.get("codes") or [])
        ys = list(day.get("ys") or [])
        preds = _predict_ridge_matrix(model, day)
        rows: List[Tuple[str, Any, Any]] = []
        for i, code in enumerate(codes):
            y = ys[i] if i < len(ys) else None
            pred = preds[i] if i < len(preds) else None
            rows.append((str(code), pred, y))
        out.append((str(day.get("date") or "")[:10], rows))
    return out


def _ridge_model_from_days(days: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    from core.research.oo_rank_lambdarank import _fit_ridge_baseline

    names: List[str] = []
    for day in days or []:
        raw = day.get("names") if isinstance(day, dict) else None
        if isinstance(raw, (list, tuple)) and raw:
            names = [str(n) for n in raw]
            break
    if not names:
        return {"success": False, "error": "定档模型没有特征名"}
    report = _fit_ridge_baseline(
        days,
        feature_names=names,
        ridge_lambda=1.0,
        cross_section_zscore=True,
    )
    if not isinstance(report, dict) or not report.get("success"):
        return report if isinstance(report, dict) else {"success": False, "error": "定档模型拟合失败"}
    # Ridge 报告本身就是打分模型（coefficients / intercept），没有单独的 return_model。
    if not isinstance(report.get("coefficients"), dict) or not report.get("coefficients"):
        nested = report.get("return_model") or report.get("model")
        if isinstance(nested, dict) and isinstance(nested.get("coefficients"), dict):
            report = nested
        else:
            return {"success": False, "error": "定档模型没有系数"}
    return {"success": True, "return_model": report}


def _skill_from_panels(
    panels: Sequence[Dict[str, Any]],
    fit_dates: Sequence[str],
    tier_dates: Sequence[str],
    *,
    eval_dates: Sequence[str] = (),
    progress_phase: str = "",
) -> Dict[str, Any]:
    fit_set = {str(d)[:10] for d in fit_dates}
    tier_set = {str(d)[:10] for d in tier_dates}
    eval_set = {str(d)[:10] for d in eval_dates} - fit_set
    fit_days = [
        d for d in panels if str(d.get("date") or "")[:10] in fit_set
    ]
    tier_days = [
        d for d in panels if str(d.get("date") or "")[:10] in tier_set
    ]
    eval_days = [
        d for d in panels if str(d.get("date") or "")[:10] in eval_set
    ]
    if progress_phase:
        set_tier_progress(progress_phase, "只在内存，不写研究套")
    if len(fit_days) < 8 or len(tier_days) < 1:
        return {
            "ok": False,
            "error": (
                f"定档面板不足（拟合 {len(fit_days)} 日，定档 {len(tier_days)} 日）"
            ),
        }
    report = _ridge_model_from_days(fit_days)
    model = report.get("return_model") if isinstance(report, dict) else None
    if not isinstance(model, dict):
        return {
            "ok": False,
            "error": (report or {}).get("error") if isinstance(report, dict) else "定档模型拟合失败",
        }
    scored = _predict_rank_days(model, tier_days)
    holdout_scored: List[Tuple[str, List[Tuple[str, Any, Any]]]] = []
    if eval_days:
        holdout_scored = _predict_rank_days(model, eval_days)
    by_abs = up_precision_lift_by_code(scored, call_mode="abs")
    by_cs = up_precision_lift_by_code(scored, call_mode="cs")
    holdout_abs = (
        up_precision_lift_by_code(holdout_scored, call_mode="abs")
        if holdout_scored
        else {}
    )
    holdout_cs = (
        up_precision_lift_by_code(holdout_scored, call_mode="cs")
        if holdout_scored
        else {}
    )
    return {
        "ok": True,
        # 默认给排序头（cs）；收益头读 by_code_abs
        "by_code": by_cs,
        "by_code_abs": by_abs,
        "by_code_cs": by_cs,
        "holdout_by_code": holdout_cs,
        "holdout_by_code_abs": holdout_abs,
        "holdout_by_code_cs": holdout_cs,
        "model": model,
    }


def _build_oo_return_day_panels(
    items: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 1,
) -> List[Dict[str, Any]]:
    """研究套口径 oo 特征日截面（FAMILY_OO / assemble 路径），供内存 oo Ridge 定档。"""
    import numpy as np

    from core.research.materialize.coverage import enforce_consumer_coverage
    from core.research.oo_rank_panel import group_day_matrices
    from core.research.oo_ridge_compact import (
        _oo_stock_job,
        _stack_blocks,
    )
    from core.signal.factors.alpha_oo import ALPHA_OO_PANEL_WINDOW

    enforce_consumer_coverage(
        list(items or []),
        families=["oo"],
        clocks=["open"],
        min_history=int(ALPHA_OO_PANEL_WINDOW),
    )
    jobs: List[Tuple[str, Sequence[dict]]] = []
    parts: List[Any] = []
    codes_parts: List[List[str]] = []
    horizon_i = max(1, int(horizon_days or 1))
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if not code or not bars:
            continue
        jobs.append((code, bars))
        x, y, names_i, dates = _oo_stock_job((code, bars, horizon_i, False))
        parts.append((x, y, names_i, dates))
        n_rows = int(getattr(x, "shape", (0,))[0] or 0)
        codes_parts.append([code] * n_rows if n_rows > 0 else [])
    if not parts:
        return []
    x_mat, y_mat, names, dates = _stack_blocks(parts, ridge_core_only=False)
    codes_all: List[str] = []
    for chunk in codes_parts:
        codes_all.extend(chunk)
    if int(x_mat.shape[0]) != len(codes_all):
        # 对齐失败时退回空名，group 仍可按日切
        codes_all = [""] * int(x_mat.shape[0])
    if int(x_mat.shape[0]) == 0:
        return []
    return group_day_matrices(
        np.ascontiguousarray(x_mat),
        np.asarray(y_mat, dtype=np.float64),
        names,
        dates,
        codes_all,
        min_names=8,
    )


def _build_tau_day_panels_for_tier(
    items: Sequence[Dict[str, Any]],
    fit_dates: Sequence[str],
    *,
    head: str,
    cache_head: str,
) -> List[Dict[str, Any]]:
    """τ 开盘钟日截面：``head`` 为 ``y_tc_rank`` 或 ``y_tc``。"""
    import numpy as np

    from core.research.oo_rank_panel import group_day_matrices
    from core.research.panel_matrix import collect_tau_compact, raw_atc_finite
    from core.research.tc_ridge_compact import TAU_FIT_DROP_ALIASES, TAU_Z_FEATURES
    from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS

    X, col_names, ys, dates, metas, _n_stocks = collect_tau_compact(
        list(items or []),
        list(TAU_Z_FEATURES),
        min_history=12,
        tau_hm="open",
        include_alpha_tc=True,
        head=str(head),
        cache_head=str(cache_head),
    )
    drop = set(Z_TC_MINUTE_KEYS) | set(TAU_FIT_DROP_ALIASES)
    col_set = set(col_names)
    feat_names = [k for k in TAU_Z_FEATURES if k not in drop and k in col_set]
    seen = set(feat_names)
    fit_set = {str(d)[:10] for d in fit_dates}
    train_row_idx = [i for i, d in enumerate(dates) if str(d)[:10] in fit_set]
    for key in raw_atc_finite(X, col_names, train_row_idx):
        if key not in seen:
            seen.add(key)
            feat_names.append(key)
    col_index = {str(n): i for i, n in enumerate(col_names)}
    cols = [col_index[n] for n in feat_names if n in col_index]
    feat_names = [n for n in feat_names if n in col_index]
    x_fit = (
        np.ascontiguousarray(X[:, cols])
        if cols
        else np.zeros((int(X.shape[0]), 0), dtype=np.float64)
    )
    codes_all = [
        str((metas[i] or {}).get("stock_code") or "") if i < len(metas) else ""
        for i in range(len(ys))
    ]
    return group_day_matrices(
        x_fit,
        np.asarray(ys, dtype=np.float64),
        feat_names,
        dates,
        codes_all,
        min_names=8,
    )


def collect_tier_skill(
    stock_bars: Mapping[str, Sequence[dict]],
    fit_dates: Sequence[str],
    tier_dates: Sequence[str],
    *,
    eval_dates: Sequence[str] = (),
    horizon_days: int = 1,
) -> Dict[str, Any]:
    """定档窗之前当场拟合 oo_rank / oo / τc_rank / τc，给定档窗打猜涨技能。

    同一模型再打 Holdout，只供标题。不落盘、不覆盖研究套。
    oo_rank 失败则整次失败；其余头失败软跳过并写 note。
    """
    items = [
        {"code": str(code), "bars": list(bars or [])}
        for code, bars in (stock_bars or {}).items()
        if str(code or "").strip() and bars
    ]
    if len(items) < 2:
        return {"ok": False, "error": "定档日线不足 2 只"}
    try:
        from core.research.horizon_l1 import release_allocator_cache

        release_allocator_cache()
    except Exception:  # noqa: BLE001
        logger.debug("tier skill release cache failed", exc_info=True)
    from core.research.oo_rank_panel import build_oo_rank_day_panels

    horizon_i = max(1, int(horizon_days or 1))
    set_tier_progress("准备 oo_rank", "只在内存，不写研究套")
    try:
        oo_rank_panels = build_oo_rank_day_panels(
            items,
            horizon_days=horizon_i,
        )
        oo_rank = _skill_from_panels(
            oo_rank_panels,
            fit_dates,
            tier_dates,
            eval_dates=eval_dates,
            progress_phase="拟合 oo_rank",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tier oo_rank panel failed", exc_info=True)
        return {"ok": False, "error": f"定档 oo_rank 面板失败：{exc}"}
    if not oo_rank.get("ok"):
        return oo_rank

    # 收益头 oo：优先独立 oo 面板（abs 说涨）；失败则回退 oo_rank 同预估的 abs 技能
    oo_map: Dict[str, Dict[str, Any]] = {}
    oo_pack: Dict[str, Any] = {}
    oo_note = ""
    oo_holdout: Dict[str, Dict[str, Any]] = {}
    set_tier_progress("准备 oo", "只在内存，不写研究套")
    try:
        oo_panels = _build_oo_return_day_panels(items, horizon_days=horizon_i)
        oo_pack = _skill_from_panels(
            oo_panels,
            fit_dates,
            tier_dates,
            eval_dates=eval_dates,
            progress_phase="拟合 oo",
        )
        if oo_pack.get("ok"):
            oo_map = oo_pack.get("by_code_abs") or {}
            oo_holdout = oo_pack.get("holdout_by_code_abs") or {}
        else:
            oo_note = str(oo_pack.get("error") or "oo 定档失败")
    except Exception as exc:  # noqa: BLE001
        logger.info("tier oo skill skipped: %s", exc)
        oo_note = f"oo 未入定档：{exc}"
    if not oo_map:
        # 同预估、不同说涨口径：abs vs 截面中位数
        oo_map = oo_rank.get("by_code_abs") or {}
        oo_holdout = oo_rank.get("holdout_by_code_abs") or {}
        if oo_map and not oo_note:
            oo_note = "oo 回退 oo_rank 预估（abs 说涨）"

    # 开盘钟：tc_rank L1 只拟合一次；τc_rank=截面说涨，τc=绝对阈值说涨（不再镜像同一技能）
    tc_rank_map: Dict[str, Dict[str, Any]] = {}
    tc_map: Dict[str, Dict[str, Any]] = {}
    tc_rank_pack: Dict[str, Any] = {}
    tc_rank_note = ""
    tc_note = ""
    tc_holdout_rank: Dict[str, Dict[str, Any]] = {}
    tc_holdout_abs: Dict[str, Dict[str, Any]] = {}
    set_tier_progress("准备 τc_rank / τc", "只在内存，不写研究套")
    try:
        tau_days = _build_tau_day_panels_for_tier(
            items,
            fit_dates,
            head="y_tc_rank",
            cache_head="tc_rank",
        )
        if not tau_days:
            tau_days = _build_tau_day_panels_for_tier(
                items,
                fit_dates,
                head="y_tc",
                cache_head="tc",
            )
            if tau_days:
                tc_note = "τc 回退 cache_head=tc 面板"
        tc_rank_pack = _skill_from_panels(
            tau_days,
            fit_dates,
            tier_dates,
            eval_dates=eval_dates,
            progress_phase="拟合 τc_rank / τc",
        )
        if tc_rank_pack.get("ok"):
            tc_rank_map = tc_rank_pack.get("by_code_cs") or {}
            tc_map = tc_rank_pack.get("by_code_abs") or {}
            tc_holdout_rank = tc_rank_pack.get("holdout_by_code_cs") or {}
            tc_holdout_abs = tc_rank_pack.get("holdout_by_code_abs") or {}
            if not tc_note:
                tc_note = "τc / τc_rank 同预估，说涨口径不同（abs / 截面中位）"
        else:
            tc_rank_note = str(tc_rank_pack.get("error") or "τc_rank 定档失败")
            tc_note = tc_note or tc_rank_note
    except Exception as exc:  # noqa: BLE001
        logger.info("tier tc skill skipped: %s", exc)
        tc_rank_note = f"τc_rank 未入定档：{exc}"
        tc_note = tc_note or tc_rank_note

    set_tier_progress("计算猜涨技能", "")
    oo_rank_map = oo_rank.get("by_code_cs") or oo_rank.get("by_code") or {}
    head_maps = {
        "oo_rank": oo_rank_map,
        "oo": oo_map,
        "tc_rank": tc_rank_map,
        "tc": tc_map,
    }
    combined = combine_head_lifts(head_maps)
    if not combined:
        return {"ok": False, "error": "定档窗内没有够 8 次说涨的样本"}
    holdout_maps = {
        "oo_rank": oo_rank.get("holdout_by_code_cs")
        or oo_rank.get("holdout_by_code")
        or {},
        "oo": oo_holdout,
        "tc_rank": tc_holdout_rank,
        "tc": tc_holdout_abs,
    }
    holdout = combine_head_lifts(holdout_maps)
    notes = [
        n
        for n in (oo_note, tc_rank_note, tc_note)
        if n
        and n
        not in (
            "τc / τc_rank 同预估，说涨口径不同（abs / 截面中位）",
            "oo 回退 oo_rank 预估（abs 说涨）",
        )
    ]
    return {
        "ok": True,
        "by_code": combined,
        "holdout_by_code": holdout,
        "n_codes": len(combined),
        "oo_note": oo_note,
        "tc_rank_note": tc_rank_note,
        "tc_note": tc_note,
        "notes": notes,
        "used_oo": bool(oo_map),
        "used_tc_rank": bool(tc_rank_map),
        "used_tc": bool(tc_map),
        "used_tau": bool(tc_rank_map) or bool(tc_map),
        "call_modes": {
            "oo": "abs",
            "oo_rank": "cs",
            "tc": "abs",
            "tc_rank": "cs",
        },
    }


def _normalize_head(head: Optional[str]) -> str:
    h = str(head or HEAD_OO).strip().lower()
    if h in ("tau", "y_τc", "ŷ_τc"):
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


def _f1_pos_from_counts(
    tp: int, fp: int, fn: int
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """正类=上涨：返回 ``(f1, precision, recall)``；无看多或无上涨日时对应项为 None。"""
    tp_i = max(0, int(tp or 0))
    fp_i = max(0, int(fp or 0))
    fn_i = max(0, int(fn or 0))
    precision = (tp_i / (tp_i + fp_i)) if (tp_i + fp_i) > 0 else None
    recall = (tp_i / (tp_i + fn_i)) if (tp_i + fn_i) > 0 else None
    if precision is None or recall is None:
        return None, precision, recall
    s = float(precision) + float(recall)
    f1 = (2.0 * float(precision) * float(recall) / s) if s > 0 else 0.0
    return round(f1, 4), round(float(precision), 4), round(float(recall), 4)


def _watching_name_map(
    watching_codes: Optional[Sequence[str]] = None,
) -> Dict[str, str]:
    """观察池代码→名称：落盘/static 优先，缺名用 a_code_name 离线码表（不打行情）。"""
    try:
        from core.watching.store import read_watching, watchlist_names_for
    except Exception:  # noqa: BLE001
        logger.debug("predictability_tiers: name map import failed", exc_info=True)
        return {}
    try:
        data = read_watching() or {}
    except Exception:  # noqa: BLE001
        logger.debug("predictability_tiers: read_watching failed", exc_info=True)
        data = {}
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    try:
        # allow_live=False：空名不逐只 get_quote，避免分档被拖慢
        names = watchlist_names_for(data if isinstance(data, dict) else {}, allow_live=False)
    except Exception:  # noqa: BLE001
        logger.debug("predictability_tiers: watchlist_names_for failed", exc_info=True)
        names = [""] * len(wl)
    out: Dict[str, str] = {}
    for c, n in zip(wl, names):
        nm = str(n or "").strip()
        if c and nm and nm != c:
            out[c] = nm
    need = []
    for raw in list(wl) + list(watching_codes or []):
        code = str(raw or "").strip()
        if code and code not in out and code not in need:
            need.append(code)
    if need:
        # 一次加载码表（a_code_name → spot_a_em），避免逐只 resolve
        cmap: Dict[str, str] = {}
        try:
            from core.t0.intraday import _load_a_code_name_map

            cmap = dict(_load_a_code_name_map() or {})
        except Exception:  # noqa: BLE001
            logger.debug("predictability_tiers: code name map failed", exc_info=True)
            cmap = {}
        for code in need:
            nm = str(cmap.get(code) or "").strip()
            if nm and nm != code:
                out[code] = nm
    return out


def _normalize_pool(pool: Optional[str]) -> str:
    p = str(pool or DEFAULT_POOL).strip().lower()
    if p in ("research", "research_universe", "ru", "universe", "日线", "研究宇宙"):
        return POOL_RESEARCH
    # ledger 池已废弃：等同 watching
    return POOL_WATCHING


def holdout_eval_dates(split: Optional[dict]) -> List[str]:
    """Holdout 评测日：优先 ``eval_dates``，否则整段 ``test_days``。"""
    src = split if isinstance(split, dict) else {}
    explicit = [
        str(d).strip()[:10]
        for d in (src.get("eval_dates") or [])
        if str(d or "").strip()[:10]
    ]
    if explicit:
        return explicit
    return [
        str(d).strip()[:10]
        for d in (src.get("test_days") or [])
        if str(d or "").strip()[:10]
    ]


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


def _load_research_oo_model() -> Tuple[Optional[Any], Dict[str, Any]]:
    """只读研究套 ŷ_oo；不回落执行套/草稿（避免 Holdout 泄漏进 β）。"""
    from core.paths import RETURN_SCORE_MODEL_RESEARCH_PATH
    from core.signal.return_score import ReturnScoreModel
    from core.research.oo_ridge import load_return_model_payload

    path = RETURN_SCORE_MODEL_RESEARCH_PATH
    raw = load_return_model_payload(path)
    if not raw:
        return None, {"ok": False, "reason": "no_research_model", "path": path}
    model = ReturnScoreModel.from_dict(raw.get("model") or raw)
    if model is None:
        return None, {"ok": False, "reason": "invalid_research_model", "path": path}
    return model, {
        "ok": True,
        "role": raw.get("role") or "research",
        "path": path,
        "saved_at": raw.get("saved_at") or raw.get("promoted_at"),
        "meta": raw.get("meta") or {},
        "lookback": (raw.get("meta") or {}).get("lookback"),
        "horizon_days": (raw.get("meta") or {}).get("horizon_days")
        or getattr(model, "horizon_days", None),
    }


def _absorb_holdout_row(
    acc: Dict[str, Dict[str, Any]],
    *,
    code: str,
    date: str,
    pred: Any,
    y: Any,
    tier_set: set,
    yhat_eps: float,
    metric_set: Optional[set] = None,
) -> None:
    """``tier_set``（训练窗）记命中、OO_R+ 与 OO_F1；``metric_set``（Holdout）记 ŷ/实现成对。

    标题 Rank IC / IC / ICIR / ACC / F1 在汇总时按评测日切开，不在这里切。

    OO_R+ = mean(y_oo | ŷ_oo>ε)；
    OO_F1 正类=上涨，与命中同死区；只在 ``tier_set`` 累计。
    """
    d = str(date or "")[:10]
    code_s = str(code or "").strip()
    if not d or not code_s:
        return
    in_tier = d in tier_set
    in_metric = d in (metric_set if metric_set is not None else tier_set)
    if not in_tier and not in_metric:
        return
    hit = _sign_hit_pair(pred, y, yhat_eps=yhat_eps)
    slot = acc.get(code_s)
    if slot is None:
        slot = {
            "hits": 0,
            "n_valid": 0,
            "n_days": 0,
            "yhats": [],
            "realized": [],
            "dates": [],
            "sum_y_pos": 0.0,
            "n_y_pos": 0,
            "tp_pos": 0,
            "fp_pos": 0,
            "fn_pos": 0,
        }
        acc[code_s] = slot
    try:
        yh = float(pred) if pred is not None else float("nan")
        ry = float(y) if y is not None else float("nan")
    except (TypeError, ValueError):
        yh = float("nan")
        ry = float("nan")
    if in_tier:
        slot["n_days"] = int(slot["n_days"]) + 1
        if isinstance(hit, bool):
            slot["n_valid"] = int(slot["n_valid"]) + 1
            if hit:
                slot["hits"] = int(slot["hits"]) + 1
            # OO_F1 混淆：与 n_valid 同日（|ŷ|≥ε 且 |y|>0）
            if math.isfinite(yh) and math.isfinite(ry):
                pred_up = yh > 0.0
                act_up = ry > 0.0
                if pred_up and act_up:
                    slot["tp_pos"] = int(slot.get("tp_pos") or 0) + 1
                elif pred_up and not act_up:
                    slot["fp_pos"] = int(slot.get("fp_pos") or 0) + 1
                elif (not pred_up) and act_up:
                    slot["fn_pos"] = int(slot.get("fn_pos") or 0) + 1
        slot["dates"].append(d)
        # OO_R+：定档窗且 ŷ_oo>ε
        if math.isfinite(yh) and math.isfinite(ry) and yh > float(yhat_eps):
            slot["sum_y_pos"] = float(slot.get("sum_y_pos") or 0.0) + ry
            slot["n_y_pos"] = int(slot.get("n_y_pos") or 0) + 1
    if not in_metric or not math.isfinite(yh) or not math.isfinite(ry):
        return
    slot["yhats"].append(yh)
    slot["realized"].append(ry)
    slot.setdefault("pair_dates", []).append(d)


def _absorb_tau_holdout_row(
    acc: Dict[str, Dict[str, Any]],
    *,
    code: str,
    date: str,
    pred: Any,
    y: Any,
    yhat_eps: float,
    tier_set: Optional[set] = None,
    metric_set: Optional[set] = None,
) -> None:
    """``tier_set``（训练窗）记 ŷ_τc 命中、TC_R+ / TC_F1；成对序列记 ``metric_set``（Holdout）。"""
    d = str(date or "")[:10]
    code_s = str(code or "").strip()
    if not d or not code_s:
        return
    in_tier = tier_set is None or d in tier_set
    in_metric = metric_set is None or d in metric_set
    if not in_tier and not in_metric:
        return
    hit = _sign_hit_pair(pred, y, yhat_eps=yhat_eps)
    slot = acc.get(code_s)
    if slot is None:
        slot = {
            "hits": 0,
            "n_valid": 0,
            "n_days": 0,
            "yhats": [],
            "realized": [],
            "dates": [],
            "hits_tau": 0,
            "n_valid_tau": 0,
            "tau_yhats": [],
            "tau_realized": [],
            "tau_pair_dates": [],
            "sum_y_pos_tau": 0.0,
            "n_y_pos_tau": 0,
            "tp_pos_tau": 0,
            "fp_pos_tau": 0,
            "fn_pos_tau": 0,
        }
        acc[code_s] = slot
    slot.setdefault("hits_tau", 0)
    slot.setdefault("n_valid_tau", 0)
    slot.setdefault("tau_yhats", [])
    slot.setdefault("tau_realized", [])
    slot.setdefault("tau_pair_dates", [])
    slot.setdefault("sum_y_pos_tau", 0.0)
    slot.setdefault("n_y_pos_tau", 0)
    slot.setdefault("tp_pos_tau", 0)
    slot.setdefault("fp_pos_tau", 0)
    slot.setdefault("fn_pos_tau", 0)
    try:
        yh = float(pred) if pred is not None else float("nan")
        ry = float(y) if y is not None else float("nan")
    except (TypeError, ValueError):
        yh = float("nan")
        ry = float("nan")
    if in_tier and isinstance(hit, bool):
        slot["n_valid_tau"] = int(slot["n_valid_tau"]) + 1
        if hit:
            slot["hits_tau"] = int(slot["hits_tau"]) + 1
        # TC_F1 混淆：与 n_valid_tau 同日
        if math.isfinite(yh) and math.isfinite(ry):
            pred_up = yh > 0.0
            act_up = ry > 0.0
            if pred_up and act_up:
                slot["tp_pos_tau"] = int(slot.get("tp_pos_tau") or 0) + 1
            elif pred_up and not act_up:
                slot["fp_pos_tau"] = int(slot.get("fp_pos_tau") or 0) + 1
            elif (not pred_up) and act_up:
                slot["fn_pos_tau"] = int(slot.get("fn_pos_tau") or 0) + 1
        # TC_R+：定档窗且 |ŷ_τc|>ε
        if math.isfinite(yh) and math.isfinite(ry) and yh > float(yhat_eps):
            slot["sum_y_pos_tau"] = float(slot.get("sum_y_pos_tau") or 0.0) + ry
            slot["n_y_pos_tau"] = int(slot.get("n_y_pos_tau") or 0) + 1
    if not in_metric or not math.isfinite(yh) or not math.isfinite(ry):
        return
    slot["tau_yhats"].append(yh)
    slot["tau_realized"].append(ry)
    slot["tau_pair_dates"].append(d)


def _oo_predictions(
    model: Any,
    rows: Sequence[Dict[str, Any]],
    dates: Sequence[str],
) -> List[Optional[float]]:
    """截面模型先按决策日 z-score，再 ``predict``。训练窗 μ/σ 模型仍由 ``predict`` 自己套。"""
    from core.research.feature_standardize import (
        cross_section_zscore_dicts,
        is_cross_section_zscore,
    )

    raws = [dict(r) if isinstance(r, dict) else {} for r in rows]
    if not raws or model is None:
        return [None] * len(raws)
    if not is_cross_section_zscore(model):
        return [model.predict(row) for row in raws]
    from core.signal.factors.z_oo import stamp_z_oo_aliases

    for row in raws:
        stamp_z_oo_aliases(row)
    names = [str(k) for k in (getattr(model, "coefficients", None) or {})]
    groups = [str(d or "")[:10] for d in dates]
    if len(groups) != len(raws):
        groups = [""] * len(raws)
    z_rows = cross_section_zscore_dicts(raws, names, groups)
    return [model.predict(row) for row in z_rows]


def _tau_predictions(
    model_doc: Dict[str, Any],
    rows: Sequence[Dict[str, Any]],
    dates: Sequence[str],
) -> List[Optional[float]]:
    """截面 ŷ_τc：按决策日 z-score 后打分。非截面模型原样交给 ``predict_tau_from_features``。"""
    from core.research.feature_standardize import (
        cross_section_zscore_dicts,
        is_cross_section_zscore,
    )
    from core.research.tc_ridge import predict_tau_from_features

    raws = [dict(r) if isinstance(r, dict) else {} for r in rows]
    if not raws:
        return []
    if not is_cross_section_zscore(model_doc):
        return [predict_tau_from_features(row, model_doc=model_doc) for row in raws]
    from core.signal.factors.z_tc import stamp_z_tc_aliases

    rm = model_doc.get("return_model") if isinstance(model_doc, dict) else {}
    coefs = (rm or {}).get("coefficients") if isinstance(rm, dict) else {}
    names = [str(k) for k, v in (coefs or {}).items() if v is not None]
    for row in raws:
        stamp_z_tc_aliases(row)
    groups = [str(d or "")[:10] for d in dates]
    if len(groups) != len(raws):
        groups = [""] * len(raws)
    z_rows = cross_section_zscore_dicts(raws, names, groups)
    return [predict_tau_from_features(row, model_doc=model_doc) for row in z_rows]


def _load_research_tau_model() -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """研究套 ŷ_τc；缺则 (None, meta)。"""
    try:
        from core.research.tc_ridge import load_tau_model
    except Exception as exc:  # noqa: BLE001
        return None, {"ok": False, "reason": "import_failed", "error": str(exc)}
    try:
        doc = load_tau_model(role="research")
    except Exception as exc:  # noqa: BLE001
        logger.debug("load research tau model failed", exc_info=True)
        return None, {"ok": False, "reason": "load_failed", "error": str(exc)}
    if not isinstance(doc, dict) or not isinstance(doc.get("return_model"), dict):
        return None, {"ok": False, "reason": "no_tau_research_model"}
    return doc, {
        "ok": True,
        "source": "tau_research_model",
        "path": "tau_ridge_model_research.json",
        "saved_at": doc.get("saved_at") or doc.get("promoted_at"),
    }


def _score_tau_holdout_into_acc(
    acc: Dict[str, Dict[str, Any]],
    stock_bars: Dict[str, List[dict]],
    *,
    tier_set: set,
    metric_set: Optional[set] = None,
    yhat_eps: float = _YHAT_EPS,
) -> Dict[str, Any]:
    """研究套 ŷ_τc：命中看定档窗，成对序列看 Holdout（τ=open，标签 close/open−1）。"""
    keep = set(tier_set or ())
    if metric_set is not None:
        keep |= set(metric_set)
    if not keep or not stock_bars:
        return {"ok": False, "reason": "empty", "n_rows": 0}
    doc, meta = _load_research_tau_model()
    if doc is None:
        return {**meta, "n_rows": 0}
    try:
        from core.research.tc_ridge_compact import build_tau_panels_from_bars
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "import_failed", "error": str(exc), "n_rows": 0}

    items = [
        {"code": str(c), "bars": list(bars or [])}
        for c, bars in (stock_bars or {}).items()
        if str(c or "").strip() and bars
    ]
    if len(items) < 2:
        return {"ok": False, "reason": "stock_lt2", "n_rows": 0, **meta}
    try:
        panels = build_tau_panels_from_bars(
            items,
            tau_hm="open",
            include_alpha_tc=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("build_tau_panels_from_bars for tiers failed", exc_info=True)
        return {"ok": False, "reason": "panel_failed", "error": str(exc), "n_rows": 0, **meta}

    pending_codes: List[str] = []
    pending_dates: List[str] = []
    pending_xs: List[dict] = []
    pending_ys: List[Any] = []
    for pan in panels or []:
        if not isinstance(pan, dict):
            continue
        code = str(pan.get("code") or "").strip()
        xs = list(pan.get("xs") or [])
        ys = list(pan.get("ys") or [])
        dates = list(pan.get("dates") or [])
        n = min(len(xs), len(ys), len(dates))
        for i in range(n):
            d = str(dates[i] or "")[:10]
            if d not in keep:
                continue
            pending_codes.append(code)
            pending_dates.append(d)
            pending_xs.append(xs[i] if isinstance(xs[i], dict) else {})
            pending_ys.append(ys[i])
    try:
        preds = _tau_predictions(doc, pending_xs, pending_dates)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tau bars predict failed", exc_info=True)
        return {
            "ok": False,
            "reason": "predict_failed",
            "error": str(exc),
            "n_rows": 0,
            **meta,
        }
    if not isinstance(preds, (list, tuple)):
        return {"ok": False, "reason": "predict_bad_shape", "n_rows": 0, **meta}
    tier_metric = set(metric_set) if metric_set is not None else set(tier_set or ())
    n_pair = min(
        len(pending_codes), len(pending_dates), len(preds), len(pending_ys)
    )
    for i in range(n_pair):
        _absorb_tau_holdout_row(
            acc,
            code=pending_codes[i],
            date=pending_dates[i],
            pred=preds[i],
            y=pending_ys[i],
            yhat_eps=yhat_eps,
            tier_set=set(tier_set or ()),
            metric_set=tier_metric,
        )
    if n_pair < 1:
        return {"ok": False, "reason": "empty_pairs", "n_rows": 0, **meta}
    return {**meta, "ok": True, "n_rows": int(n_pair)}


def _oo_y_by_code(
    stock_bars: Dict[str, List[dict]],
    *,
    hz: int,
) -> Dict[str, Dict[str, float]]:
    from core.research.oo_panel import _forward_return

    out: Dict[str, Dict[str, float]] = {}
    h = max(1, int(hz))
    for code, bars in (stock_bars or {}).items():
        m: Dict[str, float] = {}
        for i, b in enumerate(bars or []):
            d = str((b or {}).get("date") or "")[:10]
            if not d:
                continue
            fr = _forward_return(bars, i, h)
            if fr is None:
                continue
            try:
                y = float(fr)
            except (TypeError, ValueError):
                continue
            if math.isfinite(y):
                m[d] = y
        if m:
            out[str(code)] = m
    return out


def _tau_y_by_code(stock_bars: Dict[str, List[dict]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for code, bars in (stock_bars or {}).items():
        m: Dict[str, float] = {}
        for b in bars or []:
            d = str((b or {}).get("date") or "")[:10]
            if not d:
                continue
            try:
                o = float(b.get("open"))
                c = float(b.get("close"))
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(o) and math.isfinite(c)) or o == 0.0:
                continue
            m[d] = c / o - 1.0
        if m:
            out[str(code)] = m
    return out


def _lake_coverage_ok(family: str, codes: Sequence[str], min_ready: float = 0.5) -> bool:
    try:
        from core.research.feature_lake import read_stock_meta
    except Exception:  # noqa: BLE001
        return False
    fam = str(family or "").strip()
    if not fam:
        return False
    try:
        floor = float(min_ready)
    except (TypeError, ValueError):
        floor = 0.5
    if not math.isfinite(floor):
        floor = 0.5
    floor = max(0.0, min(1.0, floor))
    total = 0
    ready = 0
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code:
            continue
        total += 1
        try:
            meta = read_stock_meta(fam, code) or {}
            n_rows = int(meta.get("n_rows") or 0)
        except Exception:  # noqa: BLE001
            continue
        if n_rows >= 200:
            ready += 1
    if total < 2:
        return False
    return (ready / float(total)) + 1e-12 >= floor


def _score_lake_days_into_acc(
    acc: Dict[str, Dict[str, Any]],
    *,
    family: str,
    codes: Sequence[str],
    dates: Sequence[str],
    y_by: Dict[str, Dict[str, float]],
    predict_fn,
    absorb_fn,
    tier_set: set,
    metric_set: set,
    yhat_eps: float,
) -> int:
    """Feature Lake 一次 slice，按日截面打分写入 ``acc``。返回成对行数。

    任何 slice / 预测异常都返回 0（调用方应清空 ``acc`` 再降级 bars）。
    """
    from collections import defaultdict

    try:
        from core.research.feature_lake import finite_feature_dict, slice_stock_panels
    except Exception:  # noqa: BLE001
        logger.debug("feature_lake import failed for tiers", exc_info=True)
        return 0

    want = [str(d)[:10] for d in dates if str(d or "").strip()[:10]]
    if not want or not codes:
        return 0
    try:
        sliced = slice_stock_panels(family, codes, want)
    except Exception:  # noqa: BLE001
        logger.debug(
            "slice_stock_panels failed family=%s codes=%s",
            family,
            len(list(codes or [])),
            exc_info=True,
        )
        return 0
    if not sliced:
        return 0
    by_day: Dict[str, List[Tuple[str, Sequence[str], Any]]] = defaultdict(list)
    for code, pack in sliced.items():
        if not pack or len(pack) < 2:
            continue
        names, day_map = pack[0], pack[1]
        if not isinstance(day_map, dict):
            continue
        for day, row in day_map.items():
            by_day[str(day)[:10]].append((str(code), names, row))
    del sliced
    n_rows = 0
    for day in want:
        rows = by_day.pop(day, [])
        if len(rows) < 2:
            continue
        day_codes: List[str] = []
        day_xs: List[dict] = []
        day_ys: List[float] = []
        for code, names, row in rows:
            ymap = y_by.get(code) or {}
            if day not in ymap:
                continue
            try:
                feats = finite_feature_dict(names, row)
            except Exception:  # noqa: BLE001
                continue
            if not feats:
                continue
            day_codes.append(code)
            day_xs.append(feats)
            day_ys.append(float(ymap[day]))
        if len(day_codes) < 2:
            continue
        try:
            preds = predict_fn(day_xs, [day] * len(day_codes))
        except Exception:  # noqa: BLE001
            logger.debug("lake day predict failed date=%s", day, exc_info=True)
            continue
        if not isinstance(preds, (list, tuple)):
            continue
        n = min(len(day_codes), len(preds), len(day_ys))
        for i in range(n):
            absorb_fn(
                acc,
                code=day_codes[i],
                date=day,
                pred=preds[i],
                y=day_ys[i],
                tier_set=tier_set,
                metric_set=metric_set,
                yhat_eps=yhat_eps,
            )
            n_rows += 1
    return n_rows


def _count_tier_sample_codes(acc: Dict[str, Dict[str, Any]]) -> int:
    n = 0
    for slot in (acc or {}).values():
        if not isinstance(slot, dict):
            continue
        if int(slot.get("n_valid") or 0) > 0:
            n += 1
    return n


def _lake_rows_enough(
    n_rows: int,
    *,
    n_codes: int,
    n_dates: int,
) -> bool:
    """湖打分行数过稀则视为失败，避免半残面板定档。"""
    if int(n_rows or 0) < 1:
        return False
    # 至少覆盖约 30% 的「票×日」下界，或绝对不少于 2 票×2 日量级
    floor = max(8, int(0.3 * max(1, int(n_codes)) * max(1, min(int(n_dates), 40))))
    return int(n_rows) >= floor


_TAU_ACC_KEYS = (
    "hits_tau",
    "n_valid_tau",
    "tau_yhats",
    "tau_realized",
    "tau_pair_dates",
    "sum_y_pos_tau",
    "n_y_pos_tau",
    "tp_pos_tau",
    "fp_pos_tau",
    "fn_pos_tau",
)


def _strip_tau_from_acc(acc: Dict[str, Dict[str, Any]]) -> None:
    """去掉半残湖写入的 τc 字段，避免 bars 回退时双计。"""
    for slot in (acc or {}).values():
        if not isinstance(slot, dict):
            continue
        for key in _TAU_ACC_KEYS:
            if key in ("tau_yhats", "tau_realized", "tau_pair_dates"):
                slot[key] = []
            elif key.startswith("sum_"):
                slot[key] = 0.0
            else:
                slot[key] = 0


def accumulate_holdout_oos_by_code(
    codes: Sequence[str],
    *,
    holdout_n: int = DEFAULT_HOLDOUT_N,
    lookback: int = DEFAULT_PANEL_LOOKBACK,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    yhat_eps: float = _YHAT_EPS,
    use_research_model: bool = True,
    tier_window_days: int = DEFAULT_TIER_WINDOW_DAYS,
    embargo_days: int = DEFAULT_TIER_EMBARGO_DAYS,
) -> Dict[str, Any]:
    """用研究套 ŷ_oo（缺则 Holdout 前现训）按票聚合。

    命中 / F1 只记 **定档窗**（展示）；ŷ/实现成对记 **Holdout 整段**（供标题测试指标）。
    面板拉 lookback 全窗；有研究套且 Feature Lake 覆盖够时按日流式打分。

    返回 ``ok`` / ``acc`` / ``tier_dates`` / ``split_meta`` 等。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from core.research.holdout import (
        calendar_dates_from_stock_bars,
        resolve_ridge_split,
    )
    from core.research.oo_panel import collect_oo_panel
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
    lookback_req = max(100, min(1000, int(lookback or DEFAULT_PANEL_LOOKBACK)))
    panel_lb = lookback_req

    model_meta: Dict[str, Any] = {}
    research_model = None
    model_source = "oo_pre_holdout_refit"
    set_tier_progress("读取研究套", "不改研究套 ŷ_oo / ŷ_τc")
    if use_research_model:
        research_model, model_meta = _load_research_oo_model()
        if research_model is not None:
            model_source = "oo_research_model"
            try:
                mh = int(getattr(research_model, "horizon_days", 0) or 0)
            except (TypeError, ValueError):
                mh = 0
            if mh > 0 and mh != hz:
                logger.info(
                    "predictability_tiers: research ŷ_oo horizon=%s vs panel=%s",
                    mh,
                    hz,
                )

    set_tier_progress("读取日线", "不改研究套 ŷ_oo / ŷ_τc")
    stock_bars, failures, _fund = load_portfolio_stock_bars(
        use_codes,
        lookback=panel_lb,
        fetch_fundamentals=False,
    )
    if len(stock_bars) < 2:
        return {
            "ok": False,
            "error": f"有效日线不足（失败 {len(failures)}）",
            "failures": (failures or [])[:8],
            "acc": {},
            "tier_dates": [],
            "lookback": panel_lb,
            "lookback_requested": lookback_req,
        }

    cal = calendar_dates_from_stock_bars(
        [{"bars": bars} for bars in stock_bars.values()]
    )
    _train_idx, _test_idx, split_meta = resolve_ridge_split(
        cal,
        holdout_trading_days=h_req,
        label_horizon_days=hz,
        calendar_dates=cal,
    )
    train_days_meta = [str(d)[:10] for d in (split_meta.get("train_days") or [])]
    test_days_meta = [str(d)[:10] for d in (split_meta.get("test_days") or [])]
    if not test_days_meta or not train_days_meta:
        return {
            "ok": False,
            "error": "Holdout 切不出训练/测试窗",
            "split_meta": split_meta,
            "acc": {},
            "tier_dates": [],
            "lookback": panel_lb,
            "lookback_requested": lookback_req,
        }
    split = build_lookback_holdout_split(
        train_days_meta,
        test_days_meta,
        holdout_n=h_req,
        tier_window_days=tier_window_days,
        embargo_days=embargo_days,
    )
    if not split.get("ok"):
        return {
            "ok": False,
            "error": split.get("error") or "训练/Holdout 切分失败",
            "holdout_split": split,
            "split_meta": split_meta,
            "acc": {},
            "tier_dates": [],
            "lookback": panel_lb,
            "lookback_requested": lookback_req,
        }
    tier_set = set(split.get("tier_dates") or [])
    metric_set = set(split.get("eval_dates") or split.get("test_days") or [])
    keep_dates = tier_set | metric_set
    acc: Dict[str, Dict[str, Any]] = {}
    items = list(stock_bars.items())
    codes_used = [c for c, _ in items]
    stock_count = len(items)
    workers = max(1, min(8, len(items)))
    n_train: Optional[int] = len(train_days_meta)
    n_test = 0
    panel_mode = "lookback_full"
    scored_via_lake = False

    set_tier_progress("打分 ŷ_oo", "研究套，只读")
    if research_model is not None:
        try:
            from core.research.lake_bridge import FAMILY_OO
        except Exception:  # noqa: BLE001
            FAMILY_OO = ""
        keep_sorted = sorted(keep_dates)
        if FAMILY_OO and _lake_coverage_ok(FAMILY_OO, codes_used):
            try:
                oo_y = _oo_y_by_code(stock_bars, hz=hz)
                n_lake = _score_lake_days_into_acc(
                    acc,
                    family=FAMILY_OO,
                    codes=codes_used,
                    dates=keep_sorted,
                    y_by=oo_y,
                    predict_fn=lambda xs, ds: _oo_predictions(
                        research_model, xs, ds
                    ),
                    absorb_fn=_absorb_holdout_row,
                    tier_set=tier_set,
                    metric_set=metric_set,
                    yhat_eps=yhat_eps,
                )
            except Exception:  # noqa: BLE001
                logger.debug("lake OO score failed; fallback bars", exc_info=True)
                acc.clear()
                n_lake = 0
            if _lake_rows_enough(
                n_lake, n_codes=len(codes_used), n_dates=len(keep_sorted)
            ) and _count_tier_sample_codes(acc) >= 2:
                n_test = int(n_lake)
                scored_via_lake = True
                panel_mode = "lake_lookback"
            else:
                # 半残湖面板不得定档：清空后走 bars
                if acc:
                    logger.info(
                        "predictability_tiers: lake OO sparse "
                        "(rows=%s tier_codes=%s); fallback bars",
                        n_lake,
                        _count_tier_sample_codes(acc),
                    )
                acc.clear()
                scored_via_lake = False

        if not scored_via_lake:
            def _panel_keep(code: str, bars: List[dict]) -> Tuple[str, list, list, list]:
                xs, ys, dates = collect_oo_panel(
                    bars,
                    horizon_days=hz,
                    stock_code=code,
                    pit_fundamentals=False,
                    keep_dates=keep_dates,
                )
                return code, xs, ys, dates

            panels: List[Tuple[str, list, list, list]] = []
            if workers == 1:
                for c, b in items:
                    try:
                        panels.append(_panel_keep(c, b))
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "collect_oo_panel failed code=%s", c, exc_info=True
                        )
            else:
                panel_slots: List[Optional[Tuple[str, list, list, list]]] = [
                    None
                ] * len(items)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = {
                        pool.submit(_panel_keep, c, b): i
                        for i, (c, b) in enumerate(items)
                    }
                    for fut in as_completed(futs):
                        idx = futs[fut]
                        try:
                            panel_slots[idx] = fut.result()
                        except Exception:  # noqa: BLE001
                            logger.debug(
                                "collect_oo_panel worker failed idx=%s",
                                idx,
                                exc_info=True,
                            )
                panels = [row for row in panel_slots if row is not None]
            flat_codes: List[str] = []
            flat_xs: List[dict] = []
            flat_ys: List[Any] = []
            flat_dates: List[str] = []
            for code, xs, ys, dates in panels:
                n = min(len(xs), len(ys), len(dates))
                for i in range(n):
                    flat_codes.append(code)
                    flat_xs.append(xs[i] if isinstance(xs[i], dict) else {})
                    flat_ys.append(ys[i])
                    flat_dates.append(str(dates[i] or "")[:10])
            del panels
            if not flat_xs:
                return {
                    "ok": False,
                    "error": "面板样本不足（0）",
                    "acc": {},
                    "tier_dates": [],
                    "lookback": panel_lb,
                    "lookback_requested": lookback_req,
                    "holdout_split": split,
                    "split_meta": split_meta,
                }
            try:
                preds = _oo_predictions(research_model, flat_xs, flat_dates)
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "error": f"ŷ_oo 打分失败：{exc}",
                    "acc": {},
                    "tier_dates": [],
                    "lookback": panel_lb,
                    "lookback_requested": lookback_req,
                    "holdout_split": split,
                    "split_meta": split_meta,
                }
            n_pair = min(len(flat_codes), len(preds), len(flat_ys), len(flat_dates))
            n_test = n_pair
            for i in range(n_pair):
                _absorb_holdout_row(
                    acc,
                    code=flat_codes[i],
                    date=flat_dates[i],
                    pred=preds[i],
                    y=flat_ys[i],
                    tier_set=tier_set,
                    metric_set=metric_set,
                    yhat_eps=yhat_eps,
                )
            del flat_xs, preds
            panel_mode = "bars_lookback"

        if n_test < 1 or _count_tier_sample_codes(acc) < 2:
            return {
                "ok": False,
                "error": (
                    "训练窗有效样本不足"
                    f"（打分行 {n_test}，有命中票 {_count_tier_sample_codes(acc)}）"
                ),
                "acc": {},
                "tier_dates": [],
                "lookback": panel_lb,
                "lookback_requested": lookback_req,
                "holdout_split": split,
                "split_meta": split_meta,
            }
    else:
        def _panel_one(code: str, bars: List[dict]) -> Tuple[str, list, list, list]:
            xs, ys, dates = collect_oo_panel(
                bars,
                horizon_days=hz,
                stock_code=code,
                pit_fundamentals=False,
            )
            return code, xs, ys, dates

        all_xs: List[Dict[str, Any]] = []
        all_ys: List[float] = []
        all_dates: List[str] = []
        all_codes: List[str] = []
        panels: List[Tuple[str, list, list, list]] = []
        if workers == 1:
            panels = [_panel_one(c, b) for c, b in items]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(_panel_one, c, b) for c, b in items]
                for fut in as_completed(futs):
                    panels.append(fut.result())
            order = {c: i for i, (c, _) in enumerate(items)}
            panels.sort(key=lambda t: order.get(t[0], 0))
        for code, xs, ys, dates in panels:
            all_xs.extend(xs)
            all_ys.extend(ys)
            all_dates.extend(dates)
            all_codes.extend([code] * len(dates))
        del panels
        if len(all_ys) < 8:
            return {
                "ok": False,
                "error": f"面板样本不足（{len(all_ys)}）",
                "acc": {},
                "tier_dates": [],
                "lookback": panel_lb,
                "lookback_requested": lookback_req,
            }
        train_idx, test_idx, split_meta = resolve_ridge_split(
            all_dates,
            holdout_trading_days=h_req,
            label_horizon_days=hz,
            calendar_dates=cal,
        )
        if not test_idx or not train_idx:
            return {
                "ok": False,
                "error": "Holdout 切不出训练/测试窗",
                "split_meta": split_meta,
                "acc": {},
                "tier_dates": [],
                "lookback": panel_lb,
                "lookback_requested": lookback_req,
            }
        train_days_meta = [str(d)[:10] for d in (split_meta.get("train_days") or [])]
        test_days_meta = [str(d)[:10] for d in (split_meta.get("test_days") or [])]
        split = build_lookback_holdout_split(
            train_days_meta,
            test_days_meta,
            holdout_n=h_req,
            tier_window_days=tier_window_days,
            embargo_days=embargo_days,
        )
        if not split.get("ok"):
            return {
                "ok": False,
                "error": split.get("error") or "训练/Holdout 切分失败",
                "holdout_split": split,
                "split_meta": split_meta,
                "acc": {},
                "tier_dates": [],
                "lookback": panel_lb,
                "lookback_requested": lookback_req,
            }
        tier_set = set(split.get("tier_dates") or [])
        metric_set = set(split.get("eval_dates") or split.get("test_days") or [])
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
            ridge_lambda=float(
                ridge_lambda if ridge_lambda is not None else DEFAULT_RIDGE_LAMBDA
            ),
            fitted_as_of=as_of,
            min_samples=24,
        )
        del xs_tr, ys_tr
        if research_model is None:
            return {
                "ok": False,
                "error": (research_report or {}).get("error")
                or "Holdout 前训练失败（且无研究套 ŷ_oo）",
                "report": research_report,
                "split_meta": split_meta,
                "acc": {},
                "tier_dates": [],
                "model_meta": model_meta,
                "lookback": panel_lb,
                "lookback_requested": lookback_req,
            }
        model_source = "oo_pre_holdout_refit"
        model_meta = {
            "ok": True,
            "role": "refit_train",
            "path": None,
            "fallback_from": model_meta.get("reason")
            if isinstance(model_meta, dict)
            else None,
        }
        n_train = len(train_idx)
        score_idx = list(train_idx) + list(test_idx)
        n_test = len(score_idx)
        preds = _oo_predictions(
            research_model,
            [all_xs[i] if isinstance(all_xs[i], dict) else {} for i in score_idx],
            [all_dates[i] for i in score_idx],
        )
        for i, pred in zip(score_idx, preds):
            _absorb_holdout_row(
                acc,
                code=all_codes[i],
                date=all_dates[i],
                pred=pred,
                y=all_ys[i],
                tier_set=tier_set,
                metric_set=metric_set,
                yhat_eps=yhat_eps,
            )
        del all_xs, all_ys, all_dates, all_codes, preds
        panel_mode = "refit_lookback"

    # ŷ_τc：优先 Feature Lake；否则 bars 面板
    set_tier_progress("打分 ŷ_τc", "研究套，只读")
    tau_meta: Dict[str, Any] = {"ok": False, "n_rows": 0}
    tau_doc, tau_load = _load_research_tau_model()
    if tau_doc is not None and bool(tau_load.get("ok", True)):
        try:
            from core.research.lake_bridge import family_for_tc

            tc_family = family_for_tc(tau_hm="open")
        except Exception:  # noqa: BLE001
            tc_family = ""
        tau_rows = 0
        tau_dates = sorted(tier_set | metric_set)
        if tc_family and _lake_coverage_ok(tc_family, codes_used):
            try:
                tau_y = _tau_y_by_code(stock_bars)
                tau_rows = _score_lake_days_into_acc(
                    acc,
                    family=tc_family,
                    codes=codes_used,
                    dates=tau_dates,
                    y_by=tau_y,
                    predict_fn=lambda xs, ds: _tau_predictions(tau_doc, xs, ds),
                    absorb_fn=_absorb_tau_holdout_row,
                    tier_set=tier_set,
                    metric_set=metric_set,
                    yhat_eps=yhat_eps,
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "lake TC score failed; fallback bars", exc_info=True
                )
                _strip_tau_from_acc(acc)
                tau_rows = 0
            if _lake_rows_enough(
                tau_rows, n_codes=len(codes_used), n_dates=len(tau_dates)
            ):
                tau_meta = {
                    **tau_load,
                    "ok": True,
                    "n_rows": int(tau_rows),
                    "source": "tau_research_lake",
                }
            else:
                if tau_rows > 0:
                    logger.info(
                        "predictability_tiers: lake TC sparse "
                        "(rows=%s); fallback bars",
                        tau_rows,
                    )
                _strip_tau_from_acc(acc)
                tau_rows = 0
        if not tau_meta.get("ok"):
            tau_meta = _score_tau_holdout_into_acc(
                acc,
                stock_bars,
                tier_set=tier_set,
                metric_set=metric_set,
                yhat_eps=yhat_eps,
            )
    else:
        tau_meta = {**tau_load, "n_rows": 0}

    return {
        "ok": True,
        "acc": acc,
        "tier_dates": list(split.get("tier_dates") or []),
        "tier_n": int(split.get("tier_n") or 0),
        "holdout_split": split,
        "split_meta": split_meta,
        "n_train": n_train,
        "n_test": n_test,
        "stock_count": stock_count,
        "codes_used": codes_used,
        "failures": (failures or [])[:8],
        "horizon_days": hz,
        "lookback": panel_lb,
        "lookback_requested": lookback_req,
        "panel_mode": panel_mode,
        "ridge_lambda": float(
            ridge_lambda if ridge_lambda is not None else DEFAULT_RIDGE_LAMBDA
        ),
        "source": model_source,
        "model_meta": model_meta,
        "tau_meta": tau_meta,
        "stock_bars": stock_bars,
    }


def tier_code_set(
    report: Optional[Dict[str, Any]],
    allowed: Optional[Sequence[str]] = None,
) -> set:
    """从分档报告取出允许档内的代码集合。默认 A。"""
    allow = {
        str(t).strip().upper()
        for t in (allowed if allowed is not None else DEFAULT_LIVE_ALLOWED)
        if str(t or "").strip()
    }
    if not allow:
        allow = set(DEFAULT_LIVE_ALLOWED)
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


def _window_cls_metrics(
    preds: Sequence[float],
    ys: Sequence[float],
    *,
    yhat_eps: float = _YHAT_EPS,
) -> Dict[str, Any]:
    """给定成对样本上的符号命中 ACC 与上涨正类 P / R / F1。

    死区与表内定档窗口径相同（``|ŷ|<ε`` 或 ``|y|≈0`` 不计入）。日期窗由调用方切开。
    """
    hits = 0
    n_valid = 0
    tp = 0
    fp = 0
    fn = 0
    for yh, ry in zip(preds or [], ys or []):
        hit = _sign_hit_pair(yh, ry, yhat_eps=yhat_eps)
        if not isinstance(hit, bool):
            continue
        n_valid += 1
        if hit:
            hits += 1
        if not (math.isfinite(float(yh)) and math.isfinite(float(ry))):
            continue
        pred_up = float(yh) > 0.0
        act_up = float(ry) > 0.0
        if pred_up and act_up:
            tp += 1
        elif pred_up and not act_up:
            fp += 1
        elif (not pred_up) and act_up:
            fn += 1
    f1, precision, recall = _f1_pos_from_counts(tp, fp, fn)
    return {
        "acc": round(hits / n_valid, 4) if n_valid > 0 else None,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "n_cls": int(n_valid),
    }


def summarize_tier_window_cs(
    acc: Dict[str, Dict[str, Any]],
    *,
    codes: Optional[Sequence[str]] = None,
    dates: Optional[Sequence[str]] = None,
    min_names: int = 5,
    head: str = HEAD_OO,
) -> Dict[str, Any]:
    """日截面 Rank IC / IC / ICIR，以及同窗池化 ACC / P / R / F1。

    ``head=oo`` 用 ŷ_oo 成对；``head=tau`` 用 ŷ_τc 成对。
    ``dates`` 给定时只留这些决策日（标题测试指标传 Holdout）；缺省用成对序列里的全部日期。
    不足 3 个有效日则 ICIR 为空。ACC / P / R / F1 不看截面最少票数，有有效日即出。
    """
    from core.research.daily_cs_ic import summarize_daily_cs_ic

    use_tau = _normalize_head(head) == HEAD_TAU
    yh_key = "tau_yhats" if use_tau else "yhats"
    ry_key = "tau_realized" if use_tau else "realized"
    ds_key = "tau_pair_dates" if use_tau else "pair_dates"

    allow = None
    if codes is not None:
        allow = {str(c).strip() for c in codes if str(c or "").strip()}
    allow_dates = None
    if dates is not None:
        allow_dates = {str(d).strip()[:10] for d in dates if str(d or "").strip()[:10]}
    preds: List[float] = []
    ys: List[float] = []
    metas: List[dict] = []
    for code, slot in (acc or {}).items():
        code_s = str(code or "").strip()
        if not code_s or not isinstance(slot, dict):
            continue
        if allow is not None and code_s not in allow:
            continue
        yh = list(slot.get(yh_key) or [])
        ry = list(slot.get(ry_key) or [])
        ds = list(slot.get(ds_key) or [])
        n = min(len(yh), len(ry), len(ds))
        for i in range(n):
            day = str(ds[i])[:10]
            if allow_dates is not None and day not in allow_dates:
                continue
            preds.append(float(yh[i]))
            ys.append(float(ry[i]))
            metas.append({"date": day, "code": code_s})
    pack = summarize_daily_cs_ic(preds, ys, metas, min_names=min_names)
    cls = _window_cls_metrics(preds, ys)
    return {
        "cs_rank_ic": pack.get("cs_rank_ic"),
        "cs_icir": pack.get("cs_icir"),
        "cs_ic": pack.get("cs_ic"),
        "cs_day_count": pack.get("cs_day_count"),
        "cs_min_names": pack.get("cs_min_names"),
        "acc": cls.get("acc"),
        "precision": cls.get("precision"),
        "recall": cls.get("recall"),
        "f1": cls.get("f1"),
        "n_cls": cls.get("n_cls"),
        "head": "tau" if use_tau else "oo",
    }


def code_holdout_ret_pct(
    bars: Optional[Sequence[dict]],
    *,
    dates: Optional[Sequence[str]] = None,
) -> Optional[float]:
    """单票 Holdout 窗内 close 首尾买持收益%（与档内等权买持同口径）。"""
    from core.backtest.topk_benchmark import _close_by_date, _period_return_from_bars

    if not bars:
        return None
    date_list = sorted(
        {str(d).strip()[:10] for d in (dates or []) if str(d or "").strip()[:10]}
    )
    if date_list:
        by = _close_by_date(list(bars))
        trimmed = [{"date": d, "close": by[d]} for d in date_list if d in by]
        return _period_return_from_bars(trimmed)
    return _period_return_from_bars(list(bars))


def attach_code_holdout_rets(
    rows: Sequence[Dict[str, Any]],
    stock_bars: Optional[Dict[str, List[dict]]],
    *,
    dates: Optional[Sequence[str]] = None,
) -> None:
    """给分档行写入 ``realized_pct``（Holdout 整段买持%）。"""
    bars_by = stock_bars or {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code") or "").strip()
        r["realized_pct"] = code_holdout_ret_pct(bars_by.get(code), dates=dates)


def summarize_tier_ew_hold(
    stock_bars: Dict[str, List[dict]],
    *,
    codes: Optional[Sequence[str]] = None,
    dates: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """档内 Holdout 整段等权买持收益%，口径对齐回测「观察池A档等权」。

    优先按 ``dates`` 走等权净值曲线（与 ``topk_benchmark._pool_ew_equity_curve`` 同构）；
    单票或曲线不足时退回各票窗内 close 首尾买持再等权平均。
    字段名沿用 ``mean_realized_pct`` / ``n_realized``（n=参与票数），供观察池分档区 KPI。
    """
    from core.backtest.topk_benchmark import (
        _close_by_date,
        _period_return_from_bars,
        _pool_ew_equity_curve,
        _pool_return_from_equity_curve,
    )

    allow = None
    if codes is not None:
        allow = {str(c).strip() for c in codes if str(c or "").strip()}
    date_list = sorted(
        {str(d).strip()[:10] for d in (dates or []) if str(d or "").strip()[:10]}
    )
    subset: Dict[str, List[dict]] = {}
    for code, bars in (stock_bars or {}).items():
        code_s = str(code or "").strip()
        if not code_s or not bars:
            continue
        if allow is not None and code_s not in allow:
            continue
        subset[code_s] = list(bars)

    empty = {
        "mean_realized": None,
        "mean_realized_pct": None,
        "n_realized": 0,
        "ret_method": "ew_hold",
    }
    if not subset:
        return empty

    def _trim(bars: List[dict]) -> List[dict]:
        if not date_list:
            return list(bars or [])
        by = _close_by_date(bars)
        return [{"date": d, "close": by[d]} for d in date_list if d in by]

    ret: Optional[float] = None
    if date_list and len(subset) >= 2:
        curve = _pool_ew_equity_curve(subset, date_list)
        ret = _pool_return_from_equity_curve(curve)
    if ret is None:
        rets: List[float] = []
        for bars in subset.values():
            r = _period_return_from_bars(_trim(bars))
            if r is not None:
                rets.append(float(r))
        if rets:
            ret = round(sum(rets) / len(rets), 2)

    n_used = sum(1 for bars in subset.values() if len(_trim(bars)) >= 2)
    if ret is None:
        return {**empty, "n_realized": int(n_used)}
    return {
        "mean_realized": round(float(ret) / 100.0, 6),
        "mean_realized_pct": float(ret),
        "n_realized": int(n_used),
        "ret_method": "ew_hold",
    }


def _rows_from_acc(
    acc: Dict[str, Dict[str, Any]],
    *,
    pool_codes: Sequence[str],
    pad_missing: bool,
    name_by: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
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
                    "sum_y_pos": 0.0,
                    "n_y_pos": 0,
                    "tp_pos": 0,
                    "fp_pos": 0,
                    "fn_pos": 0,
                    "hits_tau": 0,
                    "n_valid_tau": 0,
                    "tau_yhats": [],
                    "tau_realized": [],
                    "tau_pair_dates": [],
                    "sum_y_pos_tau": 0.0,
                    "n_y_pos_tau": 0,
                    "tp_pos_tau": 0,
                    "fp_pos_tau": 0,
                    "fn_pos_tau": 0,
                }

    rows: List[Dict[str, Any]] = []
    for code, slot in acc.items():
        n_valid = int(slot.get("n_valid") or 0)
        hits = int(slot.get("hits") or 0)
        hit_rate = round(hits / n_valid, 4) if n_valid > 0 else None
        n_valid_tau = int(slot.get("n_valid_tau") or 0)
        hits_tau = int(slot.get("hits_tau") or 0)
        hit_rate_tau = (
            round(hits_tau / n_valid_tau, 4) if n_valid_tau > 0 else None
        )
        n_y_pos = max(0, int(slot.get("n_y_pos") or 0))
        sum_y_pos = float(slot.get("sum_y_pos") or 0.0)
        # 门槛用未 round 均值；展示仍四位小数
        mean_y_pos_raw = (sum_y_pos / n_y_pos) if n_y_pos > 0 else None
        mean_y_pos = (
            round(float(mean_y_pos_raw), 4) if mean_y_pos_raw is not None else None
        )
        f1_pos, precision_pos, recall_pos = _f1_pos_from_counts(
            int(slot.get("tp_pos") or 0),
            int(slot.get("fp_pos") or 0),
            int(slot.get("fn_pos") or 0),
        )
        n_y_pos_tau = max(0, int(slot.get("n_y_pos_tau") or 0))
        sum_y_pos_tau = float(slot.get("sum_y_pos_tau") or 0.0)
        mean_y_pos_tau_raw = (
            (sum_y_pos_tau / n_y_pos_tau) if n_y_pos_tau > 0 else None
        )
        mean_y_pos_tau = (
            round(float(mean_y_pos_tau_raw), 4)
            if mean_y_pos_tau_raw is not None
            else None
        )
        f1_pos_tau, precision_pos_tau, recall_pos_tau = _f1_pos_from_counts(
            int(slot.get("tp_pos_tau") or 0),
            int(slot.get("fp_pos_tau") or 0),
            int(slot.get("fn_pos_tau") or 0),
        )
        rows.append(
            {
                "code": code,
                "name": name_by.get(code),
                "tier": "C",
                "score": None,
                "hit_rate": hit_rate,
                "n_valid": n_valid,
                "n_days": int(slot.get("n_days") or 0),
                "hit_rate_tau": hit_rate_tau,
                "n_valid_tau": n_valid_tau,
                "mean_y_pos": mean_y_pos,
                "mean_y_pos_raw": mean_y_pos_raw,
                "n_y_pos": n_y_pos,
                "f1_pos": f1_pos,
                "precision_pos": precision_pos,
                "recall_pos": recall_pos,
                "mean_y_pos_tau": mean_y_pos_tau,
                "mean_y_pos_tau_raw": mean_y_pos_tau_raw,
                "n_y_pos_tau": n_y_pos_tau,
                "f1_pos_tau": f1_pos_tau,
                "precision_pos_tau": precision_pos_tau,
                "recall_pos_tau": recall_pos_tau,
            }
        )

    # 档位由后续 assign_tiers_by_lift 按猜涨技能写入；此处仅搭行骨架
    counts = {"A": 0, "B": 0, "C": len(rows)}
    for r in rows:
        r["tier"] = "C"
    return rows, counts


def build_predictability_tiers(
    holdout_n: int = DEFAULT_HOLDOUT_N,
    *,
    pool: str = DEFAULT_POOL,
    a_count: int = DEFAULT_A_COUNT,
    b_count: int = DEFAULT_B_COUNT,
    a_f1: Optional[float] = None,
    tau_a_f1: Optional[float] = None,
    require_oo_r_pos: bool = DEFAULT_REQUIRE_OO_R_POS,
    require_tc_r_pos: bool = DEFAULT_REQUIRE_TC_R_POS,
    head: str = HEAD_OO,
    watching_codes: Optional[Sequence[str]] = None,
    codes: Optional[Sequence[str]] = None,
    persist: bool = True,
    lookback: int = DEFAULT_PANEL_LOOKBACK,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    use_research_model: bool = True,
) -> Dict[str, Any]:
    """研究枢纽协议：Holdout 前 60 日猜涨技能分档。

    技能 = 说涨精度 − 该票上涨日比例（收益头 abs / 排序头截面中位）。
    表内四头技能 + Rank_Score；档标题 Rank_Score 与等权买持看 Holdout。
    ``a_f1`` / ``tau_a_f1`` / ``require_*_r_pos`` 仍接受，不再参与排队。
    有分数者按技能降序取 top ``a_count``→A、接着 ``b_count``→B、其余→C。
    落盘 last；回测 / live：截面 z 不缩小，ŷ 与名次只写允许档，档外不开仓。
    ``lookback``：面板长度；``holdout_n``：评测窗。
    """
    if a_f1 is None:
        a_f1 = DEFAULT_A_F1
    if tau_a_f1 is None:
        tau_a_f1 = DEFAULT_TAU_A_F1
    head_n = _normalize_head(head)
    if head_n == HEAD_TAU:
        return {
            "success": False,
            "error": "观察池分档仅支持 ŷ_oo（head=oo；60 日猜涨技能定档 + Holdout 评测）",
            "protocol": PROTOCOL_HOLDOUT_OOS,
        }

    pool_codes, pool_source, resolved, watch_set = _resolve_pool_codes(
        codes=codes,
        pool=pool,
        watching_codes=watching_codes,
    )
    pool_mode = _normalize_pool(pool) if codes is None else "explicit"
    n_a = clamp_a_count(a_count)
    n_b = clamp_b_count(b_count)
    if len(pool_codes) < 2:
        return {
            "success": False,
            "error": "池内标的不足 2 只",
            "protocol": PROTOCOL_HOLDOUT_OOS,
            "pool": pool_mode,
        }

    set_tier_progress("读取日线", "不改研究套 ŷ_oo / ŷ_τc")
    try:
        return _emit_predictability_tiers(
            pool_codes=pool_codes,
            pool_mode=pool_mode,
            pool_source=pool_source,
            resolved=resolved,
            watch_set=watch_set,
            watching_codes=watching_codes,
            holdout_n=holdout_n,
            lookback=lookback,
            horizon_days=horizon_days,
            ridge_lambda=ridge_lambda,
            use_research_model=use_research_model,
            n_a=n_a,
            n_b=n_b,
            persist=persist,
        )
    finally:
        finish_tier_progress()


def _emit_predictability_tiers(
    *,
    pool_codes,
    pool_mode,
    pool_source,
    resolved,
    watch_set,
    watching_codes,
    holdout_n,
    lookback,
    horizon_days,
    ridge_lambda,
    use_research_model,
    n_a,
    n_b,
    persist,
):
    packed = accumulate_holdout_oos_by_code(
        pool_codes,
        holdout_n=holdout_n,
        lookback=lookback,
        horizon_days=horizon_days,
        ridge_lambda=ridge_lambda,
        use_research_model=use_research_model,
    )
    if not packed.get("ok"):
        return {
            "success": False,
            "error": packed.get("error") or "观察池分档失败",
            "protocol": PROTOCOL_HOLDOUT_OOS,
            "holdout_split": packed.get("holdout_split"),
            "split_meta": packed.get("split_meta"),
            "failures": packed.get("failures"),
            "model_meta": packed.get("model_meta"),
            "tau_meta": packed.get("tau_meta"),
        }

    acc = dict(packed.get("acc") or {})
    tier_dates = list(packed.get("tier_dates") or [])
    split = packed.get("holdout_split") or {}
    tier_n = int(packed.get("tier_n") or len(tier_dates))
    tau_meta = packed.get("tau_meta") if isinstance(packed.get("tau_meta"), dict) else {}
    require_tau = bool(tau_meta.get("ok"))
    name_by = _watching_name_map(
        watching_codes if watching_codes is not None else list(watch_set or pool_codes)
    )
    rows, counts = _rows_from_acc(
        acc,
        pool_codes=pool_codes,
        pad_missing=True,
        name_by=name_by,
    )
    stock_bars = packed.get("stock_bars") if isinstance(packed.get("stock_bars"), dict) else {}
    eval_dates = holdout_eval_dates(split) or list(split.get("test_days") or [])
    supplied = packed.get("skill_by_code")
    if isinstance(supplied, dict):
        combined = supplied
        skill_meta = {
            "ok": True,
            "supplied": True,
            "used_oo": False,
            "used_tc_rank": False,
            "used_tc": False,
            "used_tau": False,
            "oo_note": "",
            "tc_rank_note": "",
            "tc_note": "",
        }
        holdout_supplied = packed.get("skill_holdout_by_code")
        holdout_combined = holdout_supplied if isinstance(holdout_supplied, dict) else {}
    else:
        fit_dates = list(split.get("fit_dates") or [])
        skill_meta = collect_tier_skill(
            stock_bars,
            fit_dates,
            tier_dates,
            eval_dates=eval_dates,
            horizon_days=int(packed.get("horizon_days") or horizon_days or 1),
        )
        if not skill_meta.get("ok"):
            return {
                "success": False,
                "error": skill_meta.get("error") or "定档猜涨技能计算失败",
                "protocol": PROTOCOL_HOLDOUT_OOS,
                "holdout_split": split,
                "split_meta": packed.get("split_meta"),
                "model_meta": packed.get("model_meta"),
                "tau_meta": tau_meta,
            }
        combined = skill_meta.get("by_code") if isinstance(skill_meta.get("by_code"), dict) else {}
        holdout_raw = skill_meta.get("holdout_by_code")
        holdout_combined = holdout_raw if isinstance(holdout_raw, dict) else {}
    set_tier_progress("写入分档", "")
    assign_tiers_by_lift(rows, combined, a_count=n_a, b_count=n_b)
    attach_holdout_scores(rows, holdout_combined)
    sort_rows_by_tier_score(rows)
    n_with_sample = sum(1 for r in rows if int(r.get("n_valid") or 0) > 0)
    counts = {"A": 0, "B": 0, "C": 0}
    for r in rows:
        tier_name = str(r.get("tier") or "C")
        if tier_name not in counts:
            r["tier"] = "C"
            tier_name = "C"
        counts[tier_name] += 1
    # 等权买持与标题指标均看 Holdout 整段
    metric_dates = list(eval_dates)
    attach_code_holdout_rets(rows, stock_bars, dates=metric_dates)
    cs_pool = summarize_tier_window_cs(acc, dates=eval_dates, head=HEAD_OO)
    cs_pool_tau = summarize_tier_window_cs(acc, dates=eval_dates, head=HEAD_TAU)
    cs_by_tier: Dict[str, Dict[str, Any]] = {}
    for tier in ("A", "B", "C"):
        tier_codes = [
            str(r.get("code") or "")
            for r in rows
            if str(r.get("tier") or "") == tier
        ]
        pack = summarize_tier_window_cs(
            acc, codes=tier_codes, dates=eval_dates, head=HEAD_OO
        )
        pack.update(
            summarize_tier_ew_hold(
                stock_bars,
                codes=tier_codes,
                dates=metric_dates,
            )
        )
        cs_by_tier[tier] = pack
    cs_by_tier_tau = {
        tier: summarize_tier_window_cs(
            acc,
            codes=[
                str(r.get("code") or "")
                for r in rows
                if str(r.get("tier") or "") == tier
            ],
            dates=eval_dates,
            head=HEAD_TAU,
        )
        for tier in ("A", "B", "C")
    }
    model_source = str(packed.get("source") or "oo_research_model")
    panel_lb = int(packed.get("lookback") or lookback or DEFAULT_PANEL_LOOKBACK)
    lookback_req = int(packed.get("lookback_requested") or lookback or panel_lb)
    panel_mode = str(packed.get("panel_mode") or "")
    src_note = (
        "研究套 ŷ_oo"
        if model_source == "oo_research_model"
        else "Holdout 前现训 ŷ_oo（无研究套）"
    )
    heads_used = ["oo_rank"]
    if skill_meta.get("used_oo"):
        heads_used.append("oo")
    if skill_meta.get("used_tc_rank"):
        heads_used.append("τc_rank")
    if skill_meta.get("used_tc"):
        heads_used.append("τc")
    if len(heads_used) > 1:
        score_note = f"Score={' / '.join(heads_used)} 猜涨技能等权"
    else:
        score_note = "Score=oo_rank 猜涨技能"
    skip_soft = {
        "开盘 τc 与 τc_rank 同面板，计分只入一次",
        "τc / τc_rank 同预估，说涨口径不同（abs / 截面中位）",
        "oo 回退 oo_rank 预估（abs 说涨）",
    }
    soft_notes = [
        str(skill_meta.get(k) or "").strip()
        for k in ("oo_note", "tc_rank_note", "tc_note")
        if str(skill_meta.get(k) or "").strip()
        and str(skill_meta.get(k) or "").strip() not in skip_soft
    ]
    if soft_notes:
        score_note = f"{score_note}（{'；'.join(soft_notes)}）"
    rep: Dict[str, Any] = {
        "success": True,
        "schema": SCHEMA,
        "task": "predictability_tiers",
        "protocol": PROTOCOL_HOLDOUT_OOS,
        "source": model_source,
        "head": HEAD_OO,
        "head_label": "ŷ_oo",
        "lookback": panel_lb,
        "lookback_requested": lookback_req,
        "panel_mode": panel_mode or "lookback_full",
        "thresholds": {
            "a_count": int(n_a),
            "b_count": int(n_b),
            "require_tau": bool(require_tau),
            "tier_window_days": int(
                split.get("tier_window_days") or DEFAULT_TIER_WINDOW_DAYS
            ),
            "embargo_days": int(split.get("embargo_days") or DEFAULT_TIER_EMBARGO_DAYS),
            "min_up_calls": DEFAULT_TIER_MIN_DAYS,
            "min_days": DEFAULT_TIER_MIN_DAYS,
            "yhat_eps": _YHAT_EPS,
            "score": "up_precision_lift_quad",
            "used_oo": bool(skill_meta.get("used_oo")),
            "used_tc_rank": bool(skill_meta.get("used_tc_rank")),
            "used_tc": bool(skill_meta.get("used_tc")),
            "used_tau": bool(skill_meta.get("used_tau")),
        },
        "tier_skill": {
            "used_oo": bool(skill_meta.get("used_oo")),
            "used_tc_rank": bool(skill_meta.get("used_tc_rank")),
            "used_tc": bool(skill_meta.get("used_tc")),
            "used_tau": bool(skill_meta.get("used_tau")),
            "oo_note": str(skill_meta.get("oo_note") or ""),
            "tc_rank_note": str(skill_meta.get("tc_rank_note") or ""),
            "tc_note": str(skill_meta.get("tc_note") or ""),
            "call_modes": skill_meta.get("call_modes")
            or {
                "oo": "abs",
                "oo_rank": "cs",
                "tc": "abs",
                "tc_rank": "cs",
            },
        },
        "pool": pool_mode,
        "pool_source": pool_source,
        "pool_count": len(pool_codes),
        "pool_codes_fp": pool_codes_fingerprint(pool_codes),
        "n_with_sample": n_with_sample,
        "tier_dates": tier_dates,
        "tier_n": tier_n,
        "holdout_n": split.get("holdout_n") or max(2, min(int(holdout_n or DEFAULT_HOLDOUT_N), 90)),
        "as_of_tier_last": split.get("as_of_tier_last"),
        "as_of_eval_last": (eval_dates[-1] if eval_dates else None),
        "holdout_split": split,
        "split_meta": packed.get("split_meta"),
        "n_train": packed.get("n_train"),
        "n_test": packed.get("n_test"),
        "horizon_days": packed.get("horizon_days"),
        "model_meta": packed.get("model_meta"),
        "tau_meta": tau_meta,
        "counts": counts,
        "selection_window": SELECTION_WINDOW,
        "title_metric_window": TITLE_METRIC_WINDOW,
        "cs_rank_ic": cs_pool.get("cs_rank_ic"),
        "cs_icir": cs_pool.get("cs_icir"),
        "cs_ic": cs_pool.get("cs_ic"),
        "cs_day_count": cs_pool.get("cs_day_count"),
        "cs_by_tier": cs_by_tier,
        "cs_rank_ic_tau": cs_pool_tau.get("cs_rank_ic"),
        "cs_icir_tau": cs_pool_tau.get("cs_icir"),
        "cs_ic_tau": cs_pool_tau.get("cs_ic"),
        "cs_day_count_tau": cs_pool_tau.get("cs_day_count"),
        "cs_by_tier_tau": cs_by_tier_tau,
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
            f"{src_note} · 定档窗 {tier_n} 日"
            f"（Holdout 前 {int(split.get('tier_window_days') or DEFAULT_TIER_WINDOW_DAYS)} 日，"
            f"交界空 {int(split.get('embargo_days') or DEFAULT_TIER_EMBARGO_DAYS)} 日）"
            f" · Holdout {int(split.get('holdout_n') or holdout_n or DEFAULT_HOLDOUT_N)} 日"
            f"（{len(eval_dates)} 日）评测标题 Rank_Score 与等权买持"
            f" · 面板 {panel_lb} 日"
            f"；{score_note}；说涨至少 {DEFAULT_TIER_MIN_DAYS} 次才入队"
            f"；技能 = 说涨精度 − 上涨日比例；按技能降序 top {n_a}→A、接着 {n_b}→B、其余→C"
            "；技能 = 有样本头等权（oo / oo_rank / τc / τc_rank；abs / 截面中位）；"
            "表内：四头技能 / Rank_Score；"
            "落盘 last；live 档位跟随历史回测「分档」勾选；回测天数用独立 lookback。"
        ),
    }
    if persist:
        try:
            save_predictability_tiers_last(rep)
        except Exception:  # noqa: BLE001
            logger.debug("persist tier60_quad_upskill_holdout_eval tiers failed", exc_info=True)
    return rep
