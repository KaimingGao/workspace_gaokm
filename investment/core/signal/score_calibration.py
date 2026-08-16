"""ŷ 校准层：单调 g(ŷ)≈E[r|ŷ]（Isotonic / PAV）。

不改因子与 Ridge；人审 persist 后写入 live。
方案 A：g 仅供 tip / 复盘对照；排序键与买卖/入簿闸始终读原始 ŷ。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.io_atomic import atomic_write_json

SCHEMA = "score_calibration_v1"
_MIN_PAIRS = 40
_MIN_HOLD_PAIRS = 15


def calibration_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "score_calibration.json")


def calibration_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "score_calibration_last_report.json")


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def isotonic_pav(
    xs: Sequence[float],
    ys: Sequence[float],
) -> Tuple[List[float], List[float]]:
    """Pool Adjacent Violators：非降 g。返回 (x_knots, y_hat) 与 xs 等长排序后压缩。"""
    pairs = sorted(
        ((float(x), float(y)) for x, y in zip(xs, ys)),
        key=lambda t: t[0],
    )
    if not pairs:
        return [], []
    # 同 x 先平均
    merged_x: List[float] = []
    merged_y: List[float] = []
    merged_n: List[float] = []
    for x, y in pairs:
        if merged_x and abs(x - merged_x[-1]) < 1e-12:
            n = merged_n[-1]
            merged_y[-1] = (merged_y[-1] * n + y) / (n + 1.0)
            merged_n[-1] = n + 1.0
        else:
            merged_x.append(x)
            merged_y.append(y)
            merged_n.append(1.0)
    # PAV blocks: (sum_y, weight, x_left, x_right)
    blocks: List[List[float]] = []
    for x, y, w in zip(merged_x, merged_y, merged_n):
        blocks.append([y * w, w, x, x])
        while len(blocks) >= 2:
            b1 = blocks[-2]
            b2 = blocks[-1]
            m1 = b1[0] / b1[1]
            m2 = b2[0] / b2[1]
            if m1 <= m2 + 1e-15:
                break
            blocks.pop()
            blocks[-1] = [
                b1[0] + b2[0],
                b1[1] + b2[1],
                b1[2],
                b2[3],
            ]
    kx: List[float] = []
    ky: List[float] = []
    for s, w, xl, xr in blocks:
        m = s / w
        kx.append(xl)
        ky.append(m)
        if xr > xl + 1e-15:
            kx.append(xr)
            ky.append(m)
    # 去重连续相同点
    out_x: List[float] = []
    out_y: List[float] = []
    for x, y in zip(kx, ky):
        if out_x and abs(x - out_x[-1]) < 1e-12 and abs(y - out_y[-1]) < 1e-12:
            continue
        out_x.append(round(x, 6))
        out_y.append(round(y, 6))
    return out_x, out_y


def apply_isotonic(
    yhat: float,
    knots_x: Sequence[float],
    knots_y: Sequence[float],
) -> float:
    """分段线性插值；区间外钳到端点。"""
    xs = list(knots_x)
    ys = list(knots_y)
    if not xs or not ys or len(xs) != len(ys):
        return float(yhat)
    v = float(yhat)
    if v <= xs[0]:
        return float(ys[0])
    if v >= xs[-1]:
        return float(ys[-1])
    for i in range(1, len(xs)):
        if v <= xs[i] or i == len(xs) - 1:
            x0, x1 = xs[i - 1], xs[i]
            y0, y1 = ys[i - 1], ys[i]
            if abs(x1 - x0) < 1e-12:
                return float(y1)
            t = (v - x0) / (x1 - x0)
            return float(y0 + t * (y1 - y0))
    return float(ys[-1])


def _mae(preds: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(preds) < 3:
        return None
    return round(
        sum(abs(float(p) - float(y)) for p, y in zip(preds, ys)) / float(len(preds)),
        4,
    )


def _spearman(preds: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(preds)
    if n < 5:
        return None
    # rank average for ties
    def ranks(vals: Sequence[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: float(vals[i]))
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and float(vals[order[j + 1]]) == float(vals[order[i]]):
                j += 1
            avg = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(preds), ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx < 1e-12 or dy < 1e-12:
        return None
    return round(num / (dx * dy), 4)


def collect_calibration_pairs(
    *,
    lookback_dates: int = 90,
    head: str = "eod",
) -> List[Dict[str, Any]]:
    """从账本+outcomes 收集 (yhat, realized, as_of, code)。"""
    from core.score_ledger import list_ledger_dates, load_ledger, load_outcomes

    head_k = str(head or "eod").strip().lower()
    dates = list_ledger_dates(limit=max(5, int(lookback_dates or 90)))
    # 时间正序便于切 holdout
    dates = list(reversed(dates))
    rows_out: List[Dict[str, Any]] = []
    for d in dates:
        led = load_ledger(d)
        if not led.get("success"):
            continue
        oc = load_outcomes(d)
        by_code = oc.get("by_code") if oc.get("success") else None
        if not isinstance(by_code, dict):
            continue
        for r in led.get("rows") or []:
            if not isinstance(r, dict):
                continue
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            oc_r = by_code.get(code) if isinstance(by_code.get(code), dict) else None
            if not oc_r:
                # try unpadded
                oc_r = by_code.get(code.lstrip("0") or code)
            if not isinstance(oc_r, dict):
                continue
            if head_k == "tau":
                yhat = _f(r.get("yhat_tau"))
                if yhat is None:
                    yhat = _f(oc_r.get("yhat_tau"))
                realized = _f(oc_r.get("realized_tau"))
            else:
                # 只用收益分 ŷ%（yhat_eod）；禁止回退到 heuristic 的 yhat(0–100)
                yhat = _f(r.get("yhat_eod"))
                if yhat is None:
                    yhat = _f(r.get("predicted_score"))
                if yhat is None:
                    yhat = _f(oc_r.get("yhat_eod"))
                realized = _f(oc_r.get("realized_h"))
            if yhat is None or realized is None:
                continue
            # 收益分口径：排除混入的规则分 / 异常值
            if head_k == "eod" and (yhat < -20.0 or yhat > 20.0):
                continue
            if head_k == "tau" and (yhat < -20.0 or yhat > 20.0):
                continue
            rows_out.append(
                {
                    "as_of": str(r.get("as_of") or d)[:10],
                    "code": code,
                    "yhat": float(yhat),
                    "realized": float(realized),
                    "head": head_k,
                }
            )
    return rows_out


def _fit_one_head(
    pairs: Sequence[Dict[str, Any]],
    *,
    head: str,
    train_frac: float = 0.75,
) -> Dict[str, Any]:
    if len(pairs) < _MIN_PAIRS:
        return {
            "success": False,
            "head": head,
            "error": f"样本不足 n={len(pairs)}（需≥{_MIN_PAIRS}）",
            "n": len(pairs),
        }
    # 按日切分：前 train_frac 日训练
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for p in pairs:
        by_date.setdefault(str(p["as_of"])[:10], []).append(p)
    days = sorted(by_date.keys())
    cut = max(2, int(len(days) * float(train_frac)))
    cut = min(cut, max(2, len(days) - 1))
    train_days = set(days[:cut])
    hold_days = set(days[cut:])
    tr = [p for p in pairs if p["as_of"][:10] in train_days]
    ho = [p for p in pairs if p["as_of"][:10] in hold_days]
    if len(tr) < _MIN_PAIRS // 2:
        tr, ho = list(pairs), []
        train_days = set(days)
        hold_days = set()

    xs = [float(p["yhat"]) for p in tr]
    ys = [float(p["realized"]) for p in tr]
    kx, ky = isotonic_pav(xs, ys)
    if len(kx) < 2:
        return {
            "success": False,
            "head": head,
            "error": "isotonic 结点不足",
            "n": len(pairs),
        }

    def _eval(chunk: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        if len(chunk) < 3:
            return {"n": len(chunk)}
        raw = [float(p["yhat"]) for p in chunk]
        real = [float(p["realized"]) for p in chunk]
        cal = [apply_isotonic(v, kx, ky) for v in raw]
        return {
            "n": len(chunk),
            "mae_raw": _mae(raw, real),
            "mae_cal": _mae(cal, real),
            "ic_raw": _spearman(raw, real),
            "ic_cal": _spearman(cal, real),
        }

    full_xs = [float(p["yhat"]) for p in pairs]
    full_ys = [float(p["realized"]) for p in pairs]
    kx_full, ky_full = isotonic_pav(full_xs, full_ys)

    # S1：部署 knots 改用 train-only 拟合，避免 holdout 泄露；kx_full/ky_full 留存仅作研究对照
    _kx_deploy = list(kx)
    _ky_deploy = list(ky)
    if not _kx_deploy:
        _kx_deploy = list(kx_full)
        _ky_deploy = list(ky_full)
    out = {
        "success": True,
        "head": head,
        "method": "isotonic_pav",
        "n": len(pairs),
        "n_train": len(tr),
        "n_holdout": len(ho),
        "n_days": len(days),
        "train_days": sorted(train_days),
        "holdout_days": sorted(hold_days),
        "knots_x": _kx_deploy,
        "knots_y": _ky_deploy,
        "knots_x_full": list(kx_full),
        "knots_y_full": list(ky_full),
        "knots_x_train": kx,
        "knots_y_train": ky,
        "train_metrics": _eval(tr),
        "holdout_metrics": _eval(ho) if ho else {"n": 0, "note": "无 holdout 日"},
        "y_spec": (
            {"formula": "realized_tau", "unit": "pct", "note": "open→close"}
            if head == "tau"
            else {"formula": "realized_h", "unit": "pct", "note": "账本前瞻 h 日"}
        ),
    }
    # 单头是否过对应门槛（软警告；不挡 tip）
    ok_h, err_h = _head_max_g_clears_floor(
        {head: out},
        head=head,
        floor=None,
        floor_label=(
            "min_predicted_score_tau" if head == "tau" else "min_predicted_score"
        ),
        default_floor=0.0,
        resolve_floor=(_resolve_tau_floor if head == "tau" else _resolve_eod_floor),
    )
    out["promote_ok"] = bool(ok_h)
    out["promote_block_reason"] = None if ok_h else err_h
    if ky_full:
        out["max_g"] = round(max(float(y) for y in ky_full), 6)
        out["min_g"] = round(min(float(y) for y in ky_full), 6)
    return out


def fit_score_calibration_report(
    *,
    lookback_dates: int = 90,
    train_frac: float = 0.75,
    heads: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """拟合 EOD / τ 校准映射；写 last_report，不自动写盘。"""
    want = [str(h).strip().lower() for h in (heads or ("eod", "tau"))]
    head_docs: Dict[str, Any] = {}
    errors: List[str] = []
    for h in want:
        pairs = collect_calibration_pairs(lookback_dates=lookback_dates, head=h)
        doc = _fit_one_head(pairs, head=h, train_frac=train_frac)
        head_docs[h] = doc
        if not doc.get("success"):
            errors.append(f"{h}:{doc.get('error')}")

    ok_heads = {k: v for k, v in head_docs.items() if v.get("success")}
    if not ok_heads:
        return {
            "success": False,
            "error": "；".join(errors) or "无可用校准头",
            "task": "score_calibration",
            "heads": head_docs,
            "schema": SCHEMA,
        }

    promote_ok, promote_block = calibration_promote_safe(ok_heads)
    report = {
        "success": True,
        "task": "score_calibration",
        "schema": SCHEMA,
        "lookback_dates": int(lookback_dates),
        "train_frac": float(train_frac),
        "heads": head_docs,
        "enabled": False,
        "promote_ok": bool(promote_ok),
        "promote_block_reason": promote_block or None,
        "note": (
            "单调 g(ŷ)≈E[r|ŷ]；人审写入 live 后 tip/复盘可读对照。"
            "排序与买卖/入簿闸仍用原始 ŷ；不改 Ridge β。"
            + (
                ""
                if not promote_block
                else (
                    f" {promote_block}"
                    if promote_ok
                    else f" 软警告（仍可写入 tip）：{promote_block}"
                )
            )
        ),
    }
    save_calibration_last_report(report)
    return report


def save_calibration_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    path = calibration_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_calibration_last_report() -> Optional[Dict[str, Any]]:
    path = calibration_last_report_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    return doc


def tau_calibration_gate_safe(
    heads: Optional[Dict[str, Any]],
    *,
    floor: Optional[float] = None,
) -> Tuple[bool, str]:
    """诊断：若 g 进决策，max g(ŷ_τ) 能否过 τ 闸（tip 对照不挡）。"""
    return _head_max_g_clears_floor(
        heads,
        head="tau",
        floor=floor,
        floor_label="min_predicted_score_tau",
        default_floor=0.0,
        resolve_floor=_resolve_tau_floor,
    )


def eod_calibration_floor_safe(
    heads: Optional[Dict[str, Any]],
    *,
    floor: Optional[float] = None,
) -> Tuple[bool, str]:
    """EOD 校准是否可进买入/入簿门槛。

    仅 ``max g ≥ 门槛`` 不够：若门槛附近的 g(floor) 仍低于门槛，
    典型入选票会被整批挡掉（Top-K 回测 0 笔）。
    """
    ok, err = _head_max_g_clears_floor(
        heads,
        head="eod",
        floor=floor,
        floor_label="min_predicted_score",
        default_floor=0.0,
        resolve_floor=_resolve_eod_floor,
    )
    if not ok:
        return ok, err
    if not isinstance(heads, dict):
        return True, ""
    h = heads.get("eod")
    if not isinstance(h, dict):
        return True, ""
    kx = h.get("knots_x") or []
    ky = h.get("knots_y") or []
    if len(kx) < 2 or len(kx) != len(ky):
        return True, ""
    if floor is None:
        try:
            fl = float(_resolve_eod_floor())
        except Exception:
            fl = 0.0
    else:
        fl = float(floor)
    # 无正门槛时只靠 max g
    if fl <= 0:
        return True, ""
    try:
        g_at = float(apply_isotonic(fl, list(kx), list(ky)))
    except Exception:
        return False, "eod 校准在门槛处无法求值"
    if g_at + 1e-9 < fl:
        return (
            False,
            f"eod 校准 g(门槛 {fl:g})={g_at:.4f}% < 门槛，典型入选会被挡掉",
        )
    return True, ""


def _resolve_tau_floor() -> float:
    try:
        from core.signal.dual_score import get_dual_score_cfg

        return float(get_dual_score_cfg().get("min_predicted_score_tau") or 0.0)
    except Exception:
        return 0.0


def _resolve_eod_floor() -> float:
    try:
        from core.signal.config import load_signal_config

        scoring = (load_signal_config() or {}).get("scoring") or {}
        if scoring.get("min_predicted_score") is not None:
            return float(scoring.get("min_predicted_score"))
    except Exception:
        pass
    return 0.0


def _head_max_g_clears_floor(
    heads: Optional[Dict[str, Any]],
    *,
    head: str,
    floor: Optional[float],
    floor_label: str,
    default_floor: float,
    resolve_floor,
) -> Tuple[bool, str]:
    if not isinstance(heads, dict):
        return True, ""
    h = heads.get(head)
    if not isinstance(h, dict):
        return True, ""
    ky = h.get("knots_y") or []
    if len(ky) < 1:
        return True, ""
    try:
        mx = max(float(y) for y in ky)
    except (TypeError, ValueError):
        return False, f"{head} 校准 knots_y 无效"
    if floor is None:
        try:
            fl = float(resolve_floor())
        except Exception:
            fl = float(default_floor)
    else:
        fl = float(floor)
    if mx < fl:
        return (
            False,
            f"{head} 校准 max g={mx:.4f}% < {floor_label}({fl})，若进决策会挡住全部入选（tip 对照仍可用）",
        )
    return True, ""


def filter_promotable_heads(
    heads: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[str]]:
    """去掉过不了门槛的头；返回 (kept, drop_reasons)。"""
    if not isinstance(heads, dict):
        return {}, []
    kept: Dict[str, Any] = {}
    dropped: List[str] = []
    for name, h in heads.items():
        if not isinstance(h, dict):
            continue
        key = str(name).strip().lower()
        if key == "tau":
            ok, err = tau_calibration_gate_safe({key: h})
        elif key == "eod":
            ok, err = eod_calibration_floor_safe({key: h})
        else:
            ok, err = True, ""
        if ok:
            kept[key] = h
        else:
            dropped.append(err or f"{key} 不可用")
    return kept, dropped


def calibration_promote_safe(
    heads: Optional[Dict[str, Any]],
) -> Tuple[bool, str]:
    """软警告诊断：至少有一个头 g 可过对应门槛；病理信息仅提示。"""
    kept, dropped = filter_promotable_heads(heads)
    if not kept:
        return False, "；".join(dropped) or "无可用校准头"
    # 有可用头即过；剔除信息留给调用方展示
    if dropped:
        return True, "将跳过：" + "；".join(dropped)
    return True, ""


def sync_enable_calibration_flag(enable: bool) -> bool:
    """镜像「live 是否有校准映射」到 signal_config.scoring.enable_calibration（展示用，非决策）。"""
    try:
        from core.paths import SIGNAL_CONFIG_PATH
        from core.signal.config import SIGNAL_CONFIG_PATH as CFG_PATH
        import core.signal.config as cfg_mod

        path_cfg = os.environ.get(
            "INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH or CFG_PATH
        )
        raw_cfg: Dict[str, Any] = {}
        if path_cfg and os.path.isfile(path_cfg):
            with open(path_cfg, encoding="utf-8") as f:
                raw_cfg = json.load(f) or {}
        scoring = dict(raw_cfg.get("scoring") or {})
        want = bool(enable)
        if scoring.get("enable_calibration") is want and path_cfg:
            return False
        scoring["enable_calibration"] = want
        raw_cfg["scoring"] = scoring
        if path_cfg:
            atomic_write_json(path_cfg, raw_cfg)
        cfg_mod._cached = None
        from core.signal.config import load_signal_config

        load_signal_config(reload=True)
        return True
    except Exception:
        return False


def persist_score_calibration(
    report: Optional[Dict[str, Any]] = None,
    *,
    note: str = "",
    enable: bool = True,
) -> Dict[str, Any]:
    """人审把拟合报告写入 live；有 knots 即 tip/校准列可读 g（不进排序/闸）。

    ``enable`` 已废弃（始终视为写入 live）；缺报告时回退 last_report，再回退已有 live。
    """
    from core.numbers import now_iso_utc

    _ = enable  # 兼容旧 API；不再支持停用开关
    src = report if isinstance(report, dict) else None
    if not src or not src.get("success"):
        live = load_calibration_model()
        last = load_calibration_last_report()
        # 优先上次拟合报告（覆盖写入）；再回退已有 live（重写时间戳）
        if last and last.get("success"):
            src = last
        elif isinstance(live, dict) and isinstance(live.get("heads"), dict) and live.get("heads"):
            src = live
        else:
            return {"success": False, "error": "无成功校准报告，请先拟合"}
    heads_in = src.get("heads") if isinstance(src.get("heads"), dict) else {}
    heads_out: Dict[str, Any] = {}
    for name, h in heads_in.items():
        if not isinstance(h, dict):
            continue
        # 拟合报告带 success；live 模型通常已是 knots 结构
        if h.get("success") is False:
            continue
        kx = h.get("knots_x") or []
        ky = h.get("knots_y") or []
        if len(kx) < 2 or len(kx) != len(ky):
            continue
        heads_out[str(name)] = {
            "method": h.get("method") or "isotonic_pav",
            "knots_x": list(kx),
            "knots_y": list(ky),
            "n": h.get("n"),
            "holdout_metrics": h.get("holdout_metrics"),
            "train_metrics": h.get("train_metrics"),
            "y_spec": h.get("y_spec"),
        }
    if not heads_out:
        return {"success": False, "error": "报告中无可用 knots"}

    # 方案 A：对照层可保留全部有效 knots；门槛安全仅作软警告
    _kept, dropped_notes = filter_promotable_heads(heads_out)
    del _kept

    doc = {
        "success": True,
        "schema": SCHEMA,
        "promoted_at": now_iso_utc(),
        "note": note or "score calibration promote",
        "enabled": True,  # 兼容旧字段；实际以 heads 有无 knots 为准
        "heads": heads_out,
        "lookback_dates": src.get("lookback_dates"),
        "contract_note": (
            "g(ŷ) 单调校准对照层；不覆盖 predicted_score；"
            "排序/买卖闸读 raw ŷ；tip 读 *_cal"
        ),
        "dropped_heads_note": "；".join(dropped_notes) if dropped_notes else None,
    }
    path = calibration_model_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)

    # 同步到 signal_config（人审可感；非决策开关）
    sync_enable_calibration_flag(True)

    return {
        "success": True,
        "path": path,
        "promoted_at": doc["promoted_at"],
        "enabled": doc["enabled"],
        "heads": sorted(heads_out.keys()),
        "schema": SCHEMA,
        "dropped_heads_note": doc.get("dropped_heads_note"),
    }


def load_calibration_model() -> Optional[Dict[str, Any]]:
    path = calibration_model_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("heads"), dict):
        return None
    return doc


def _heads_have_valid_knots(heads: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(heads, dict):
        return False
    for h in heads.values():
        if not isinstance(h, dict):
            continue
        kx = h.get("knots_x") or []
        ky = h.get("knots_y") or []
        if len(kx) >= 2 and len(kx) == len(ky):
            return True
    return False


def calibration_enabled(*, model_doc: Optional[Dict[str, Any]] = None) -> bool:
    """live 是否有可用校准映射（校准列 / tip）。排序与买卖闸不读此标志。

    有有效 knots 即 True；不再依赖 ``enabled`` 开关或 signal_config。
    """
    doc = model_doc if isinstance(model_doc, dict) else load_calibration_model()
    if not isinstance(doc, dict):
        return False
    return _heads_have_valid_knots(doc.get("heads"))


def reconcile_calibration_switch() -> Dict[str, Any]:
    """同步「有 live 映射」→ signal_config；并把旧 enabled=false 迁为 true。"""
    doc = load_calibration_model()
    effective = bool(
        isinstance(doc, dict) and calibration_enabled(model_doc=doc)
    )
    migrated = False
    if isinstance(doc, dict) and effective and doc.get("enabled") is not True:
        try:
            fixed = dict(doc)
            fixed["enabled"] = True
            atomic_write_json(calibration_model_path(), fixed)
            migrated = True
            doc = fixed
        except Exception:
            pass
    synced = sync_enable_calibration_flag(effective)
    kept, dropped = filter_promotable_heads(
        doc.get("heads") if isinstance(doc, dict) else None
    )
    return {
        "success": True,
        "enabled": effective,
        "config_synced": synced,
        "enabled_migrated": migrated,
        "scrub": {
            "success": True,
            "changed": False,
            "enabled": effective,
            "unsafe_reason": "；".join(dropped) if dropped else None,
            "heads_kept": sorted(kept.keys()) if kept else [],
            "note": "tip-only：有 knots 即对照，无启用开关",
        },
    }


def disable_unsafe_calibration(*, note: str = "") -> Dict[str, Any]:
    """方案 A：g 不进决策，不再自动剔除头；仅返回诊断。"""
    del note
    doc = load_calibration_model()
    if not isinstance(doc, dict):
        return {"success": True, "changed": False, "enabled": False}
    heads = doc.get("heads") if isinstance(doc.get("heads"), dict) else {}
    kept, dropped = filter_promotable_heads(heads)
    return {
        "success": True,
        "changed": False,
        "enabled": calibration_enabled(model_doc=doc),
        "unsafe_reason": "；".join(dropped) if dropped else None,
        "heads_kept": sorted(kept.keys()) if kept else [],
        "note": "tip-only：有 knots 即对照，无启用开关",
    }


def _head_knots(
    model_doc: Optional[Dict[str, Any]], head: str
) -> Tuple[List[float], List[float]]:
    if not isinstance(model_doc, dict):
        return [], []
    heads = model_doc.get("heads") if isinstance(model_doc.get("heads"), dict) else {}
    h = heads.get(str(head or "eod").strip().lower())
    if not isinstance(h, dict):
        return [], []
    kx = h.get("knots_x") or []
    ky = h.get("knots_y") or []
    if len(kx) < 2 or len(kx) != len(ky):
        return [], []
    try:
        return [float(x) for x in kx], [float(y) for y in ky]
    except (TypeError, ValueError):
        return [], []


def _head_has_knots(model_doc: Optional[Dict[str, Any]], head: str) -> bool:
    kx, ky = _head_knots(model_doc, head)
    return len(kx) >= 2 and len(kx) == len(ky)


def _yhat_out_of_domain(yhat: Optional[float], knots_x: List[float]) -> bool:
    v = _f(yhat)
    if v is None or len(knots_x) < 2:
        return False
    lo, hi = knots_x[0], knots_x[-1]
    if lo > hi:
        lo, hi = hi, lo
    return v < lo - 1e-9 or v > hi + 1e-9


def apply_calibration(
    yhat: Optional[float],
    *,
    head: str = "eod",
    model_doc: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> Optional[float]:
    """返回 g(ŷ)；无 live 映射或缺头时原样返回（兼容旧调用）。

    tip/对照写入请走 ``attach_calibrated_scores``：缺头时不写恒等 ``*_cal``。
    """
    v = _f(yhat)
    if v is None:
        return None
    doc = model_doc if isinstance(model_doc, dict) else load_calibration_model()
    if not force and not calibration_enabled(model_doc=doc):
        return v
    if not isinstance(doc, dict):
        return v
    if not _head_has_knots(doc, head):
        return v
    heads = doc.get("heads") if isinstance(doc.get("heads"), dict) else {}
    h = heads.get(str(head or "eod").strip().lower()) or {}
    kx = h.get("knots_x") or []
    ky = h.get("knots_y") or []
    return round(apply_isotonic(v, kx, ky), 6)


def attach_calibrated_scores(
    item: Dict[str, Any],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """写入 predicted_score_cal / predicted_score_tau_cal / blend_cal（不改主字段）。

    ``force=True``：无 live 时也尝试（通常仍无 knots）；有 knots 时默认即写对照。
    仅对**有有效 knots**的头写 ``*_cal``（缺头不写恒等，避免假对照）。
    方案 A：``score_calibration_applied`` 恒为 False（g 不进决策）。
    """
    if not isinstance(item, dict):
        return item
    doc = model_doc if isinstance(model_doc, dict) else load_calibration_model()
    enabled = calibration_enabled(model_doc=doc)
    if not force and not enabled:
        return item
    if not isinstance(doc, dict):
        return item
    heads = doc.get("heads") if isinstance(doc.get("heads"), dict) else {}
    if not heads:
        return item
    use_force = bool(force or enabled)
    has_eod = _head_has_knots(doc, "eod")
    has_tau = _head_has_knots(doc, "tau")
    if not has_eod and not has_tau:
        item["score_calibration_enabled"] = bool(enabled)
        item["score_calibration_applied"] = False
        return item

    for k in (
        "predicted_score_cal",
        "predicted_score_tau_cal",
        "predicted_score_eod_rem_cal",
        "predicted_score_blend_cal",
        "score_calibration_eod_oor",
        "score_calibration_eod_rem_oor",
        "score_calibration_tau_oor",
        "score_calibration_partial",
        "score_calibration_note",
    ):
        item.pop(k, None)

    y_eod = _f(item.get("predicted_score_eod"))
    if y_eod is None:
        y_eod = _f(item.get("predicted_score"))
    # 禁止把 heuristic 0–100 / 表列脏分当成 ŷ_EOD 做 g(·)
    try:
        from core.signal.dual_score import is_heuristic_score_scale

        if is_heuristic_score_scale(item):
            y_eod = None
    except Exception:
        pass
    if y_eod is None:
        y_score = _f(item.get("score"))
        if y_score is not None and abs(float(y_score)) <= 20:
            y_eod = y_score
    if y_eod is not None and abs(float(y_eod)) > 20:
        y_eod = None
    y_tau = _f(item.get("predicted_score_tau"))
    if y_tau is None:
        y_tau = _f(item.get("score_rem"))

    eod_kx, _ = _head_knots(doc, "eod") if has_eod else ([], [])
    tau_kx, _ = _head_knots(doc, "tau") if has_tau else ([], [])

    y_eod_cal = (
        apply_calibration(y_eod, head="eod", model_doc=doc, force=use_force)
        if has_eod
        else None
    )
    y_tau_cal = (
        apply_calibration(y_tau, head="tau", model_doc=doc, force=use_force)
        if has_tau
        else None
    )
    if y_eod_cal is not None:
        item["predicted_score_cal"] = y_eod_cal
        if _yhat_out_of_domain(y_eod, eod_kx):
            item["score_calibration_eod_oor"] = True
    if y_tau_cal is not None:
        item["predicted_score_tau_cal"] = y_tau_cal
        if _yhat_out_of_domain(y_tau, tau_kx):
            item["score_calibration_tau_oor"] = True

    # blend 对照列：域内用 g，域外保留 raw 再融——避免大量 ŷ 钳到同一端点后 blend_cal 撞车
    # （*_cal 字段仍写端点 g，供 tip 展示钳制值）
    eod_rem = _f(item.get("predicted_score_eod_rem"))
    eod_rem_cal = None
    eod_rem_oor = False
    if has_eod:
        if eod_rem is not None:
            eod_rem_oor = _yhat_out_of_domain(eod_rem, eod_kx)
            eod_rem_cal = apply_calibration(
                eod_rem, head="eod", model_doc=doc, force=use_force
            )
            if eod_rem_oor:
                item["score_calibration_eod_rem_oor"] = True
        else:
            eod_rem_cal = y_eod_cal
            eod_rem_oor = bool(item.get("score_calibration_eod_oor"))
            if eod_rem_oor:
                item["score_calibration_eod_rem_oor"] = True
        if eod_rem_cal is not None:
            item["predicted_score_eod_rem_cal"] = eod_rem_cal
    try:
        from core.signal.dual_score import fuse_remaining_heads, get_dual_score_cfg

        cfg = get_dual_score_cfg()
        tau_oor = bool(item.get("score_calibration_tau_oor"))
        # 域内：g；域外：raw（保留截面区分度）；缺头：raw
        if has_eod and eod_rem_cal is not None and not eod_rem_oor:
            left = eod_rem_cal
        elif eod_rem is not None:
            left = eod_rem
        elif has_eod and y_eod_cal is not None and not item.get("score_calibration_eod_oor"):
            left = y_eod_cal
        else:
            left = y_eod

        if has_tau and y_tau_cal is not None and not tau_oor:
            right = y_tau_cal
        else:
            right = y_tau

        if left is not None or right is not None:
            blend_cal = fuse_remaining_heads(
                left,
                right,
                w_eod=float(cfg.get("w_eod") or 0.5),
                w_tau=float(cfg.get("w_tau") or 0.5),
            )
            if blend_cal is not None and (has_eod or has_tau):
                item["predicted_score_blend_cal"] = blend_cal
                partial = None
                if has_eod and not has_tau and y_tau is not None:
                    partial = "tau_raw"
                elif has_tau and not has_eod and (
                    eod_rem is not None or y_eod is not None
                ):
                    partial = "eod_raw"
                elif eod_rem_oor and has_eod and (y_tau is not None or has_tau):
                    partial = "eod_rem_oor_raw"
                elif tau_oor and has_tau and (
                    eod_rem is not None or eod_rem_cal is not None
                ):
                    partial = "tau_oor_raw"
                if partial:
                    item["score_calibration_partial"] = partial
                else:
                    item.pop("score_calibration_partial", None)
    except Exception:
        pass
    item["score_calibration_enabled"] = bool(enabled)
    # 方案 A：决策永不吃 g
    item["score_calibration_applied"] = False
    soft = doc.get("dropped_heads_note") if isinstance(doc, dict) else None
    if soft:
        item["score_calibration_note"] = str(soft)[:160]
    else:
        item.pop("score_calibration_note", None)
    return item
