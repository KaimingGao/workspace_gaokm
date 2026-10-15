"""滚动可预测性票档：按 Holdout 前半上涨正类 F1 合成 Score 定档。

用**已落盘研究套** ŷ_oo（``return_score_model_research.json``）打分，与日线实现标签按票聚合。
分档用的命中率与 F1 只看 Holdout **前半**。截面 Rank IC / IC / ICIR 与档内平均收益看 Holdout **整段**。
有研究套 ŷ_τc（``tau_ridge_model_research.json``）时同样：命中与 F1 看前半，截面指标看整段：
- hit_rate / hit_rate_tau = 前半符号命中率（表「OO_ACC」/「TC_ACC」；不进 Score / A 档）
- n_valid / n_valid_tau = 前半有效决策日（表内不展示）
- Score = 0.5*OO_F1 + 0.5*TC_F1（``f1_pos`` / ``f1_pos_tau``；缺一侧按 0；两侧都缺 → 无 Score）
  无 ŷ_τc 研究套时 Score = OO_F1
- A 档另过 F1 门槛（默认 OO_F1 ≥ 20%、有 ŷ_τc 研究套时 TC_F1 ≥ 20%；可配 ``a_f1`` / ``tau_a_f1``）；
  可选 **OO_R+ > 0** / **TC_R+ > 0**（``require_oo_r_pos`` / ``require_tc_r_pos``，默认皆开；勾选的各自独立须过线）
  （无 ŷ_τc 研究套时 TC_F1 / TC_R+ 门槛不生效）
- 过门槛者按 Score 降序取 top ``a_count``→A（未过门槛不占 A 名额），接着 ``b_count``→B，其余→C（默认 100 / 300）；无 Score→C
- 分档后另报各档在整段 Holdout 上的日截面 Rank IC / IC / ICIR；
  档内「平均收益率」= Holdout 整段 **等权买持**（close，对齐回测「观察池A档等权」）
- 每票 ``mean_y_pos`` = Holdout **前半** mean(y_oo | ŷ_oo>ε)（分档表「OO_R+」；勾选时进 A 档门槛）
- 每票 ``f1_pos`` / ``precision_pos`` / ``recall_pos`` = Holdout **前半** ŷ_oo 上涨正类
  （表「OO_F1」进 Score 与 A 档；「OO_P」/「OO_R」仅展示）
- 有 ŷ_τc 研究套时：``mean_y_pos_tau``（TC_R+）、``f1_pos_tau``（TC_F1，进 Score 与 A 档）、
  ``precision_pos_tau`` / ``recall_pos_tau``（TC_P / TC_R）同口径只换头

研究套缺失时回退：ŷ_oo 在 Holdout 前现训（不读执行套）；ŷ_τc 缺研究套则 Score = OO_F1。
有研究套时面板只拉 **Holdout + 因子暖窗**；无 ŷ_oo 研究套现训才用 ``lookback`` 全窗。

默认池 = 观察池；可选 research_universe（pool=ledger 已废弃，等同 watching）。

落盘：
- ``predictability_tiers_last.json``：影子报告（研究枢纽）
- ``predictability_tiers_active.json``：live 调仓闸；档位跟随历史回测「分档」勾选
  （有勾选且有 last → 过滤；不勾 → 不过滤）。持仓始终保留。
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
DEFAULT_MIN_N = 20  # 兼容旧调用；不再参与定档
# Score 序截取：过 A 档 F1 门槛者 top a_count→A，接着 b_count→B，其余→C
DEFAULT_A_COUNT = 100
DEFAULT_B_COUNT = 300
DEFAULT_A_F1 = 0.20  # A 档 OO_F1
DEFAULT_TAU_A_F1 = 0.20  # A 档 TC_F1（有研究套时）
DEFAULT_A_HIT = DEFAULT_A_F1  # 旧名兼容
DEFAULT_TAU_A_HIT = DEFAULT_TAU_A_F1  # 旧名兼容
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


def clamp_hit_rate(raw: Any, default: float = DEFAULT_A_F1) -> float:
    """比例门槛，返回 0–1。``0.2`` 与 ``20`` 都是 20%（F1 / 旧命中率共用）。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(default if default is not None else DEFAULT_A_F1)
    if not math.isfinite(v):
        v = float(default if default is not None else DEFAULT_A_F1)
    if v > 1.0:
        v = v / 100.0
    return max(0.0, min(1.0, v))


clamp_f1_floor = clamp_hit_rate


def _meets_hit_floor(raw: Any, floor: float) -> bool:
    """比例是否过线（≥）；缺/非有限 → 否。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v + 1e-12 >= float(floor)


_meets_f1_floor = _meets_hit_floor


DEFAULT_LIVE_ALLOWED = ("A",)
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


def _score_panel_lookback(holdout_n: int, horizon_days: int = 1) -> int:
    """有研究套时的日线窗：Holdout 测试段 + 余量（``load_portfolio_stock_bars`` 另加 alpha_oo pad）。

    分档只记 Holdout 前半，面板仍盖住整个 Holdout 测试段；不必按 ŷ_oo 训练窗（常 600）整池建面板。
    """
    h = max(2, min(int(holdout_n or DEFAULT_HOLDOUT_N), 90))
    hz = max(1, min(int(horizon_days or DEFAULT_HORIZON_DAYS), 10))
    # 余量覆盖缺口日 / 切分边界；因子暖窗由 load 侧 alpha_oo pad 提供
    return max(40, min(200, h + hz + 16))


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
    # live 已开时刷新报告内容，保留回测勾选的 allowed_tiers
    try:
        active = load_predictability_tiers_active()
        if active:
            promote_predictability_tiers_to_live(
                report,
                allowed_tiers=active.get("allowed_tiers") or DEFAULT_LIVE_ALLOWED,
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
    "ic",
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
        "tier_n": src.get("tier_n") if src.get("tier_n") is not None else src.get("n_ledger_dates"),
        "as_of_last": (
            src.get("as_of_tier_last")
            or ((src.get("tier_dates") or src.get("ledger_dates") or [None])[-1])
        ),
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
        "tier_n": active.get("tier_n") if active.get("tier_n") is not None else active.get("n_ledger_dates"),
    }


def filter_codes_by_predictability_live(
    codes: Sequence[str],
    *,
    keep: Sequence[str] = (),
) -> Tuple[List[str], Dict[str, Any]]:
    """Live 分档闸。无 active 时原样返回，``tradable_codes`` 为 None。

    有 active 时返回列表含允许档，并保留 ``keep``（持仓）供旧调用方估价。
    截面 z 用调用方传入的整段观察池。``tradable_codes`` 是允许开仓的代码；
    掉出允许档的持仓在 ``force_exit_held``。
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
    force_exit_held = sorted(c for c in keep_set if c not in allow_codes)
    return out, {
        "predictability_live": True,
        "unrestricted": False,
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
    a_hit: Optional[float] = None,
    tau_a_hit: Optional[float] = None,
) -> None:
    """就地定档：Score = 0.5*OO_F1 + 0.5*TC_F1（无 ŷ_τc 时 Score = OO_F1）。

    - 有 ŷ_τc（``require_tau``）：缺一侧 F1 按 0；两侧都缺 → 无 Score，档 C
    - 无 ŷ_τc：Score = OO_F1；缺 OO_F1 → C（TC_F1 不计入）
    - A：OO_F1 ≥ ``a_f1``（默认 20%）；``require_tau`` 时还须 TC_F1 ≥ ``tau_a_f1``；
      ``require_oo_r_pos`` 时还须 ``mean_y_pos``（OO_R+）> 0；
      ``require_tc_r_pos`` 且 ``require_tau`` 时还须 ``mean_y_pos_tau``（TC_R+）> 0；
      （缺/非有限/不过线不占 A；两侧勾选则须都过）。命中率（OO_ACC/TC_ACC）不进门槛
    - 过门槛者按 Score 降序取 top ``a_count``（默认 100）→ A；未过门槛不占 A 名额
    - 其余按 Score 接着 ``b_count``（默认 300）→ B；再其余 → C
    - ``a_hit`` / ``tau_a_hit``：旧参数名，等同 ``a_f1`` / ``tau_a_f1``
    """
    n_a = clamp_a_count(a_count)
    n_b = clamp_b_count(b_count)
    oo_raw = a_f1 if a_f1 is not None else a_hit
    tau_raw = tau_a_f1 if tau_a_f1 is not None else tau_a_hit
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
            oo_r = _r_pos("mean_y_pos")
            if not (math.isfinite(oo_r) and oo_r > 0.0):
                return False
        if need_tc_r_pos:
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


def assign_tier(
    hit_rate: Optional[float],
    n_valid: int,
    *,
    hit_rate_tau: Optional[float] = None,
    n_valid_tau: int = 0,
    require_tau: bool = False,
    **_legacy: Any,
) -> str:
    """单票兼容入口：有有效命中→B，否则→C。不按 F1 合成 Score。

    正式分档请用 ``assign_tiers_by_rank_score``。
    """
    del _legacy
    if max(0, int(n_valid or 0)) <= 0:
        return "C"
    if hit_rate is None:
        return "C"
    try:
        if not math.isfinite(float(hit_rate)):
            return "C"
    except (TypeError, ValueError):
        return "C"
    if require_tau:
        if max(0, int(n_valid_tau or 0)) <= 0:
            return "C"
        if hit_rate_tau is None:
            return "C"
        try:
            if not math.isfinite(float(hit_rate_tau)):
                return "C"
        except (TypeError, ValueError):
            return "C"
    return "B"


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


def split_holdout_half_dates(
    test_days: Sequence[str],
    *,
    holdout_n: int,
) -> Dict[str, Any]:
    """Holdout 测试日升序，取最近 ``holdout_n`` 日中的前半作分档窗。后半不进分档。"""
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
        "test_days": block,
        "tier_dates": tier_dates,
        "tier_n": len(tier_dates),
        "as_of_tier_last": tier_dates[-1] if tier_dates else None,
    }


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
    """前半记命中、OO_R+ 与 OO_F1 混淆计数；``metric_set`` 记 ŷ/实现（截面指标，默认同前半）。

    OO_R+ = mean(y_oo | ŷ_oo>ε)；
    OO_F1 正类=上涨，与命中同死区；只在 ``tier_set``（Holdout 前半）累计。
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
        # OO_R+：前半且 ŷ_oo>ε
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
    """前半记 ŷ_τc 命中、TC_R+ / TC_F1；整段 Holdout 写入成对序列（日截面 IC 用）。"""
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
        # TC_R+：前半且 |ŷ_τc|>ε
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
    """研究套 ŷ_τc：命中看分档前半，成对序列看 Holdout 整段（τ=open，标签 close/open−1）。"""
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
    preds = _tau_predictions(doc, pending_xs, pending_dates)
    tier_metric = set(metric_set) if metric_set is not None else set(tier_set or ())
    for code, d, pred, y in zip(pending_codes, pending_dates, preds, pending_ys):
        _absorb_tau_holdout_row(
            acc,
            code=code,
            date=d,
            pred=pred,
            y=y,
            yhat_eps=yhat_eps,
            tier_set=set(tier_set or ()),
            metric_set=tier_metric,
        )
    return {**meta, "ok": True, "n_rows": len(preds)}


def accumulate_holdout_oos_by_code(
    codes: Sequence[str],
    *,
    holdout_n: int = DEFAULT_HOLDOUT_N,
    lookback: int = DEFAULT_PANEL_LOOKBACK,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    yhat_eps: float = _YHAT_EPS,
    use_research_model: bool = True,
) -> Dict[str, Any]:
    """用研究套 ŷ_oo（缺则 Holdout 前现训）按票聚合。命中只记前半；ŷ/实现记整段。

    有研究套：日线窗收成 Holdout+余量（``_score_panel_lookback``），只物化测试日特征行。
    截面模型按决策日对全体票 z-score 后再打分，打完即丢。
    无研究套：仍用传入 ``lookback`` 全窗以便 Holdout 前现训。

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

    model_meta: Dict[str, Any] = {}
    research_model = None
    model_source = "oo_holdout_oos_refit"
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

    # 有研究套只需评 Holdout → 短窗；现训才吃满训练窗
    if research_model is not None:
        panel_lb = _score_panel_lookback(h_req, hz)
    else:
        panel_lb = lookback_req

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
    test_days_meta = list(split_meta.get("test_days") or [])
    if not test_days_meta or not _test_idx:
        return {
            "ok": False,
            "error": "Holdout 切不出测试窗",
            "split_meta": split_meta,
            "acc": {},
            "tier_dates": [],
            "lookback": panel_lb,
            "lookback_requested": lookback_req,
        }
    half = split_holdout_half_dates(test_days_meta, holdout_n=h_req)
    if not half.get("ok"):
        return {
            "ok": False,
            "error": half.get("error") or "Holdout 切分失败",
            "holdout_split": half,
            "split_meta": split_meta,
            "acc": {},
            "tier_dates": [],
            "lookback": panel_lb,
            "lookback_requested": lookback_req,
        }
    tier_set = set(half.get("tier_dates") or [])
    metric_set = set(half.get("test_days") or []) or set(tier_set)
    test_days = set(test_days_meta)
    acc: Dict[str, Dict[str, Any]] = {}
    items = list(stock_bars.items())
    codes_used = [c for c, _ in items]
    stock_count = len(items)
    workers = max(1, min(8, len(items)))
    n_train: Optional[int] = None
    n_test = 0

    if research_model is not None:
        # 只物化 Holdout 测试日。截面 z 要当天全体票一起算，特征行收回后再打分。
        def _panel_holdout(code: str, bars: List[dict]) -> Tuple[str, list, list, list]:
            xs, ys, dates = collect_oo_panel(
                bars,
                horizon_days=hz,
                stock_code=code,
                pit_fundamentals=False,
                keep_dates=test_days,
            )
            return code, xs, ys, dates

        panels: List[Tuple[str, list, list, list]] = []
        if workers == 1:
            panels = [_panel_holdout(c, b) for c, b in items]
        else:
            panel_slots: List[Optional[Tuple[str, list, list, list]]] = [None] * len(items)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {
                    pool.submit(_panel_holdout, c, b): i
                    for i, (c, b) in enumerate(items)
                }
                for fut in as_completed(futs):
                    panel_slots[futs[fut]] = fut.result()
            panels = [row for row in panel_slots if row is not None]
        del items
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
        preds = _oo_predictions(research_model, flat_xs, flat_dates)
        n_test = len(preds)
        for code, d, pred, y in zip(flat_codes, flat_dates, preds, flat_ys):
            _absorb_holdout_row(
                acc,
                code=code,
                date=d,
                pred=pred,
                y=y,
                tier_set=tier_set,
                metric_set=metric_set,
                yhat_eps=yhat_eps,
            )
        del flat_xs, preds
        if n_test < 1:
            return {
                "ok": False,
                "error": "面板样本不足（0）",
                "acc": {},
                "tier_dates": [],
                "lookback": panel_lb,
                "lookback_requested": lookback_req,
                "holdout_split": half,
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
        if not test_idx:
            return {
                "ok": False,
                "error": "Holdout 切不出测试窗",
                "split_meta": split_meta,
                "acc": {},
                "tier_dates": [],
                "lookback": panel_lb,
                "lookback_requested": lookback_req,
            }
        test_days_meta = list(split_meta.get("test_days") or [])
        half = split_holdout_half_dates(test_days_meta, holdout_n=h_req)
        if not half.get("ok"):
            return {
                "ok": False,
                "error": half.get("error") or "Holdout 切分失败",
                "holdout_split": half,
                "split_meta": split_meta,
                "acc": {},
                "tier_dates": [],
                "lookback": panel_lb,
                "lookback_requested": lookback_req,
            }
        tier_set = set(half.get("tier_dates") or [])
        metric_set = set(half.get("test_days") or []) or set(tier_set)
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
        model_source = "oo_holdout_oos_refit"
        model_meta = {
            "ok": True,
            "role": "refit_train",
            "path": None,
            "fallback_from": model_meta.get("reason") if isinstance(model_meta, dict) else None,
        }
        n_train = len(train_idx)
        n_test = len(test_idx)
        preds = _oo_predictions(
            research_model,
            [all_xs[i] if isinstance(all_xs[i], dict) else {} for i in test_idx],
            [all_dates[i] for i in test_idx],
        )
        for i, pred in zip(test_idx, preds):
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

    tau_meta = _score_tau_holdout_into_acc(
        acc,
        stock_bars,
        tier_set=set(half.get("tier_dates") or []),
        metric_set=set(half.get("test_days") or []) or set(half.get("tier_dates") or []),
        yhat_eps=yhat_eps,
    )

    return {
        "ok": True,
        "acc": acc,
        "tier_dates": list(half.get("tier_dates") or []),
        "tier_n": int(half.get("tier_n") or 0),
        "holdout_split": half,
        "split_meta": split_meta,
        "n_train": n_train,
        "n_test": n_test,
        "stock_count": stock_count,
        "codes_used": codes_used,
        "failures": (failures or [])[:8],
        "horizon_days": hz,
        "lookback": panel_lb,
        "lookback_requested": lookback_req,
        "panel_mode": "score_short" if model_source == "oo_research_model" else "refit_full",
        "ridge_lambda": float(
            ridge_lambda if ridge_lambda is not None else DEFAULT_RIDGE_LAMBDA
        ),
        "source": model_source,
        "model_meta": model_meta,
        "tau_meta": tau_meta,
        # 仅进程内给档内等权买持用，不落盘报告
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


def summarize_tier_window_cs(
    acc: Dict[str, Dict[str, Any]],
    *,
    codes: Optional[Sequence[str]] = None,
    min_names: int = 5,
    head: str = HEAD_OO,
) -> Dict[str, Any]:
    """Holdout 整段日截面：Rank IC = Spearman 均值，ICIR = Pearson IC 的 mean/std。

    ``head=oo`` 用 ŷ_oo 成对；``head=tau`` 用 ŷ_τc 成对。不足 3 个有效日则 ICIR 为空。
    """
    from core.research.daily_cs_ic import summarize_daily_cs_ic

    use_tau = _normalize_head(head) == HEAD_TAU
    yh_key = "tau_yhats" if use_tau else "yhats"
    ry_key = "tau_realized" if use_tau else "realized"
    ds_key = "tau_pair_dates" if use_tau else "pair_dates"

    allow = None
    if codes is not None:
        allow = {str(c).strip() for c in codes if str(c or "").strip()}
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
            preds.append(float(yh[i]))
            ys.append(float(ry[i]))
            metas.append({"date": str(ds[i])[:10], "code": code_s})
    pack = summarize_daily_cs_ic(preds, ys, metas, min_names=min_names)
    return {
        "cs_rank_ic": pack.get("cs_rank_ic"),
        "cs_icir": pack.get("cs_icir"),
        "cs_ic": pack.get("cs_ic"),
        "cs_day_count": pack.get("cs_day_count"),
        "cs_min_names": pack.get("cs_min_names"),
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
    watch_set: set,
    pad_missing: bool,
    min_n: int = DEFAULT_MIN_N,
    name_by: Optional[Dict[str, str]] = None,
    require_tau: bool = False,
    a_count: int = DEFAULT_A_COUNT,
    b_count: int = DEFAULT_B_COUNT,
    a_f1: float = DEFAULT_A_F1,
    tau_a_f1: float = DEFAULT_TAU_A_F1,
    require_oo_r_pos: bool = DEFAULT_REQUIRE_OO_R_POS,
    require_tc_r_pos: bool = DEFAULT_REQUIRE_TC_R_POS,
    **_legacy: Any,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], int]:
    if "a_hit" in _legacy and a_f1 == DEFAULT_A_F1:
        a_f1 = _legacy.get("a_hit")
    if "tau_a_hit" in _legacy and tau_a_f1 == DEFAULT_TAU_A_F1:
        tau_a_f1 = _legacy.get("tau_a_hit")
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
    min_n_req = max(1, int(min_n or DEFAULT_MIN_N))
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
        mean_y_pos = (
            round(sum_y_pos / n_y_pos, 4) if n_y_pos > 0 else None
        )
        f1_pos, precision_pos, recall_pos = _f1_pos_from_counts(
            int(slot.get("tp_pos") or 0),
            int(slot.get("fp_pos") or 0),
            int(slot.get("fn_pos") or 0),
        )
        n_y_pos_tau = max(0, int(slot.get("n_y_pos_tau") or 0))
        sum_y_pos_tau = float(slot.get("sum_y_pos_tau") or 0.0)
        mean_y_pos_tau = (
            round(sum_y_pos_tau / n_y_pos_tau, 4) if n_y_pos_tau > 0 else None
        )
        f1_pos_tau, precision_pos_tau, recall_pos_tau = _f1_pos_from_counts(
            int(slot.get("tp_pos_tau") or 0),
            int(slot.get("fp_pos_tau") or 0),
            int(slot.get("fn_pos_tau") or 0),
        )
        ic = _spearman_ic(slot.get("yhats") or [], slot.get("realized") or [])
        ds = list(slot.get("dates") or [])
        rows.append(
            {
                "code": code,
                "name": name_by.get(code),
                "tier": "C",
                "score": None,
                "hit_rate": hit_rate,
                "hits": hits,
                "n_valid": n_valid,
                "n_days": int(slot.get("n_days") or 0),
                "hit_rate_tau": hit_rate_tau,
                "hits_tau": hits_tau,
                "n_valid_tau": n_valid_tau,
                "mean_y_pos": mean_y_pos,
                "n_y_pos": n_y_pos,
                "f1_pos": f1_pos,
                "precision_pos": precision_pos,
                "recall_pos": recall_pos,
                "mean_y_pos_tau": mean_y_pos_tau,
                "n_y_pos_tau": n_y_pos_tau,
                "f1_pos_tau": f1_pos_tau,
                "precision_pos_tau": precision_pos_tau,
                "recall_pos_tau": recall_pos_tau,
                "ic": ic,
                "in_watching": code in watch_set,
                "as_of_first": ds[0] if ds else None,
                "as_of_last": ds[-1] if ds else None,
            }
        )

    assign_tiers_by_rank_score(
        rows,
        require_tau=bool(require_tau),
        a_count=a_count,
        b_count=b_count,
        a_f1=a_f1,
        tau_a_f1=tau_a_f1,
        require_oo_r_pos=bool(require_oo_r_pos),
        require_tc_r_pos=bool(require_tc_r_pos),
    )
    counts = {"A": 0, "B": 0, "C": 0}
    for r in rows:
        tier = str(r.get("tier") or "C")
        if tier not in counts:
            tier = "C"
            r["tier"] = "C"
        counts[tier] = int(counts.get(tier) or 0) + 1

    tier_rank = {"A": 0, "B": 1, "C": 2}

    def _sort_key(r: dict):
        sc = r.get("score")
        return (
            tier_rank.get(str(r.get("tier") or "C"), 9),
            -(float(sc) if sc is not None else -1.0),
            -int(r.get("n_valid") or 0),
            str(r.get("code") or ""),
        )

    rows.sort(key=_sort_key)
    return rows, counts, min_n_req


def build_holdout_half_tiers(
    holdout_n: int = DEFAULT_HOLDOUT_N,
    *,
    pool: str = DEFAULT_POOL,
    min_n: int = DEFAULT_MIN_N,
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
    a_hit: Optional[float] = None,
    tau_a_hit: Optional[float] = None,
    **_legacy: Any,
) -> Dict[str, Any]:
    """研究枢纽协议：研究套 ŷ_oo 按票分档。F1/命中看 Holdout 前半；截面指标与等权买持看整段。

    Score = 0.5*OO_F1 + 0.5*TC_F1（无 ŷ_τc 研究套则 Score = OO_F1；缺一侧按 0）。
    A 档另过 F1 门槛（``a_f1`` / ``tau_a_f1``，默认 20% / 20%）；
    ``require_oo_r_pos`` / ``require_tc_r_pos``（默认皆开）时还须对应 R+>0
    （无 ŷ_τc 研究套时 TC 门槛不生效）。未过门槛不占 A 名额。
    过门槛者按 Score 降序取 top ``a_count``→A、接着 ``b_count``→B、其余→C。
    档内平均收益率 = Holdout 整段等权买持（对齐回测「观察池A档等权」）。
    落盘 last；回测/live 用档位限制开仓，不缩小截面 z。回测天数独立。
    live 档位跟随历史回测「分档」勾选。
    ``lookback``：仅无研究套现训时用的日线窗（对齐 ŷ_oo 卡训练窗）；
    有研究套时面板走 ``_score_panel_lookback`` 短窗。
    ``a_hit`` / ``tau_a_hit`` / ``**_legacy``：旧参数名兼容（等同 F1 门槛；a_frac 等忽略）。
    """
    del _legacy
    if a_f1 is None:
        a_f1 = a_hit if a_hit is not None else DEFAULT_A_F1
    if tau_a_f1 is None:
        tau_a_f1 = tau_a_hit if tau_a_hit is not None else DEFAULT_TAU_A_F1
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
    n_a = clamp_a_count(a_count)
    n_b = clamp_b_count(b_count)
    oo_floor = clamp_f1_floor(a_f1, DEFAULT_A_F1)
    tau_floor = clamp_f1_floor(tau_a_f1, DEFAULT_TAU_A_F1)
    need_oo_r_pos = bool(require_oo_r_pos)
    need_tc_r_pos = bool(require_tc_r_pos)
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
        use_research_model=use_research_model,
    )
    if not packed.get("ok"):
        return {
            "success": False,
            "error": packed.get("error") or "Holdout OOS 分档失败",
            "protocol": PROTOCOL_HOLDOUT_OOS,
            "holdout_split": packed.get("holdout_split"),
            "split_meta": packed.get("split_meta"),
            "failures": packed.get("failures"),
            "model_meta": packed.get("model_meta"),
            "tau_meta": packed.get("tau_meta"),
        }

    acc = dict(packed.get("acc") or {})
    tier_dates = list(packed.get("tier_dates") or [])
    half = packed.get("holdout_split") or {}
    tier_n = int(packed.get("tier_n") or len(tier_dates))
    tau_meta = packed.get("tau_meta") if isinstance(packed.get("tau_meta"), dict) else {}
    require_tau = bool(tau_meta.get("ok"))
    name_by = _watching_name_map(
        watching_codes if watching_codes is not None else list(watch_set or pool_codes)
    )
    rows, counts, min_n_req = _rows_from_acc(
        acc,
        pool_codes=pool_codes,
        watch_set=watch_set,
        pad_missing=True,
        min_n=min_n,
        name_by=name_by,
        require_tau=require_tau,
        a_count=n_a,
        b_count=n_b,
        a_f1=oo_floor,
        tau_a_f1=tau_floor,
        require_oo_r_pos=need_oo_r_pos,
        require_tc_r_pos=need_tc_r_pos,
    )
    n_with_sample = sum(1 for r in rows if int(r.get("n_valid") or 0) > 0)
    stock_bars = packed.get("stock_bars") if isinstance(packed.get("stock_bars"), dict) else {}
    metric_dates = list(half.get("test_days") or []) or list(tier_dates)
    attach_code_holdout_rets(rows, stock_bars, dates=metric_dates)
    cs_pool = summarize_tier_window_cs(acc, head=HEAD_OO)
    cs_pool_tau = summarize_tier_window_cs(acc, head=HEAD_TAU)
    cs_by_tier: Dict[str, Dict[str, Any]] = {}
    for tier in ("A", "B", "C"):
        tier_codes = [
            str(r.get("code") or "")
            for r in rows
            if str(r.get("tier") or "") == tier
        ]
        pack = summarize_tier_window_cs(acc, codes=tier_codes, head=HEAD_OO)
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
    score_note = (
        "Score=0.5*OO_F1+0.5*TC_F1"
        if require_tau
        else "Score=OO_F1（无 ŷ_τc 研究套）"
    )
    rep: Dict[str, Any] = {
        "success": True,
        "schema": SCHEMA,
        "task": "predictability_tiers",
        "live_hook": False,
        "protocol": PROTOCOL_HOLDOUT_OOS,
        "source": model_source,
        "head": HEAD_OO,
        "head_label": "ŷ_oo",
        "lookback": panel_lb,
        "lookback_requested": lookback_req,
        "panel_mode": panel_mode or (
            "score_short" if model_source == "oo_research_model" else "refit_full"
        ),
        "min_n": min_n_req,
        "thresholds": {
            "a_count": int(n_a),
            "b_count": int(n_b),
            "a_f1": oo_floor,
            "tau_a_f1": tau_floor,
            "require_tau": bool(require_tau),
            "require_oo_r_pos": bool(need_oo_r_pos),
            "require_tc_r_pos": bool(need_tc_r_pos),
            "score": (
                "0.5*OO_F1+0.5*TC_F1"
                if require_tau
                else "OO_F1"
            ),
            "min_n": min_n_req,
        },
        "pool": pool_mode,
        "pool_source": pool_source,
        "pool_count": len(pool_codes),
        "n_with_sample": n_with_sample,
        "tier_dates": tier_dates,
        "tier_n": tier_n,
        "holdout_n": half.get("holdout_n") or max(2, min(int(holdout_n or DEFAULT_HOLDOUT_N), 90)),
        "as_of_tier_last": half.get("as_of_tier_last"),
        "holdout_split": half,
        "split_meta": packed.get("split_meta"),
        "n_train": packed.get("n_train"),
        "n_test": packed.get("n_test"),
        "horizon_days": packed.get("horizon_days"),
        "model_meta": packed.get("model_meta"),
        "tau_meta": tau_meta,
        "counts": counts,
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
            f"{src_note} · Holdout {int(half.get('holdout_n') or holdout_n or DEFAULT_HOLDOUT_N)} 日前半 {tier_n} 日分档"
            f" · 截面指标与等权买持看整段 {int(half.get('n_test') or 0)} 日 · 面板 {panel_lb} 日"
            + (
                f"（短窗；请求训练窗 {lookback_req}）"
                if model_source == "oo_research_model" and lookback_req > panel_lb
                else ""
            )
            + f"；{score_note}；A 须 OO_F1≥{oo_floor:.0%}"
            + (
                f"、TC_F1≥{tau_floor:.0%}"
                if require_tau
                else "（无 ŷ_τc 研究套，不看 TC_F1 门槛）"
            )
            + ("、OO_R+>0" if need_oo_r_pos else "")
            + ("、TC_R+>0" if need_tc_r_pos and require_tau else "")
            + f"；过门槛按 Score 降序 top {n_a}→A、接着 {n_b}→B、其余→C"
            + "；落盘 last；live 档位跟随历史回测「分档」勾选；"
            "回测天数用独立 lookback。"
        ),
    }
    if persist:
        try:
            save_predictability_tiers_last(rep)
        except Exception:  # noqa: BLE001
            logger.debug("persist holdout_oos_half tiers failed", exc_info=True)
    return rep
