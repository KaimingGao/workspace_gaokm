"""风险预算轻量（模拟账户）：市场波动缩放 + 分数比例目标仓。

非 QP；不接实盘。高波时压低有效单票/行业上限，目标权重按 score 比例分配。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _realized_vol(closes: List[float]) -> Optional[float]:
    if len(closes) < 5:
        return None
    rets: List[float] = []
    for i in range(1, len(closes)):
        a, b = float(closes[i - 1]), float(closes[i])
        if a <= 0 or b <= 0:
            continue
        rets.append(b / a - 1.0)
    if len(rets) < 4:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    return var**0.5


def market_vol_scale(
    *,
    index_code: str = "000300",
    lookback: int = 40,
    short_win: int = 20,
    spike_ratio: float = 1.5,
    dampen: float = 0.8,
) -> Dict[str, Any]:
    """
    用指数近 short_win 日波动 vs 更早窗口：若 short/long ≥ spike_ratio → scale=dampen。
    取数失败时 scale=1.0（不挡调仓）。
    """
    out: Dict[str, Any] = {
        "ok": False,
        "scale": 1.0,
        "high_vol": False,
        "index_code": index_code,
        "vol_short": None,
        "vol_long": None,
        "spike_ratio": float(spike_ratio),
        "dampen": float(dampen),
        "message": "",
    }
    try:
        from core.data_service import get_bars

        pack = get_bars(index_code, limit=max(lookback, short_win * 2 + 5))
        bars = (pack or {}).get("bars") or (pack or {}).get("data") or []
        closes: List[float] = []
        for b in bars:
            c = b.get("close") if isinstance(b, dict) else None
            if c is None and isinstance(b, dict):
                c = b.get("Close")
            try:
                closes.append(float(c))
            except (TypeError, ValueError):
                continue
        if len(closes) < short_win + 5:
            out["message"] = "指数K线不足，波动缩放跳过"
            return out
        # 日线通常旧→新；取末尾
        tail = closes[-lookback:] if len(closes) >= lookback else closes
        short = tail[-short_win:]
        long = tail[:-short_win] if len(tail) > short_win else tail
        vs = _realized_vol(short)
        vl = _realized_vol(long) if len(long) >= 5 else vs
        out["vol_short"] = round(vs, 6) if vs is not None else None
        out["vol_long"] = round(vl, 6) if vl is not None else None
        out["ok"] = vs is not None and vl is not None and vl > 1e-12
        if not out["ok"]:
            out["message"] = "波动无法估计"
            return out
        ratio = float(vs) / float(vl)
        out["vol_ratio"] = round(ratio, 4)
        if ratio >= float(spike_ratio):
            out["high_vol"] = True
            out["scale"] = float(dampen)
            out["message"] = (
                f"市场波动抬升（近{short_win}日/基线={ratio:.2f}≥{spike_ratio}），"
                f"目标仓位上限 ×{dampen}"
            )
        else:
            out["message"] = f"市场波动正常（比值 {ratio:.2f}），目标仓位不缩放"
        return out
    except Exception as e:
        out["message"] = f"波动缩放不可用: {e}"
        return out


def score_budget_weights(
    ranked: List[dict],
    *,
    max_position_pct: float,
    max_sector_pct: float,
    max_positions: int,
) -> Tuple[Dict[str, float], Dict[str, float], List[Dict[str, Any]]]:
    """
    在限额内按 score 比例分配目标权重（%）。
    ranked 项含 stock_code / score / sector，已按 score 降序。
    """
    max_pos = float(max_position_pct)
    max_sec = float(max_sector_pct)
    max_n = int(max_positions)
    skipped: List[Dict[str, Any]] = []
    selected: List[dict] = []
    sector_cnt: Dict[str, int] = {}

    for row in ranked:
        if len(selected) >= max_n:
            skipped.append({**row, "reason": "max_positions"})
            continue
        sector = row["sector"]
        # 粗选：同行业过多则跳过（避免比例分配前塞满无法落地）
        if sector_cnt.get(sector, 0) >= max(1, int(max_sec / max(0.1, max_pos))):
            skipped.append({**row, "reason": "sector_slot_full"})
            continue
        selected.append(row)
        sector_cnt[sector] = int(sector_cnt.get(sector) or 0) + 1

    if not selected:
        return {}, {}, skipped

    score_sum = sum(max(0.0, float(r["score"])) for r in selected) or 1.0
    # 总预算：不超过只数×单票上限，也不超过「行业数 × 行业上限」的粗上界
    total_budget = min(100.0, max_n * max_pos)
    raw = {
        r["stock_code"]: total_budget * (max(0.0, float(r["score"])) / score_sum)
        for r in selected
    }

    weights: Dict[str, float] = {c: 0.0 for c in raw}
    sector_sum: Dict[str, float] = {}
    # 多轮裁剪：超单票/行业的部分回收再分配
    pending = dict(raw)
    for _ in range(8):
        if not pending:
            break
        progressed = False
        next_pending: Dict[str, float] = {}
        # 本轮按当前 pending 尝试落入
        codes = list(pending.keys())
        for code in codes:
            row = next(r for r in selected if r["stock_code"] == code)
            sector = row["sector"]
            room_pos = max_pos - float(weights.get(code) or 0.0)
            room_sec = max_sec - float(sector_sum.get(sector) or 0.0)
            room = min(room_pos, room_sec, pending[code])
            if room <= 0.05:
                if pending[code] > 0.05:
                    skipped.append({**row, "reason": "clip_no_room"})
                continue
            take = round(room, 4)
            weights[code] = round(float(weights.get(code) or 0.0) + take, 4)
            sector_sum[sector] = round(float(sector_sum.get(sector) or 0.0) + take, 4)
            left = pending[code] - take
            if left > 0.05:
                next_pending[code] = left
            progressed = True
        # 残余按仍有空间的标的再比例分
        residual = sum(next_pending.values())
        if residual <= 0.05:
            break
        open_codes = []
        for r in selected:
            c = r["stock_code"]
            sec = r["sector"]
            if max_pos - float(weights.get(c) or 0) > 0.05 and max_sec - float(
                sector_sum.get(sec) or 0
            ) > 0.05:
                open_codes.append(c)
        if not open_codes or not progressed:
            break
        ssum = sum(max(0.0, float(next(r for r in selected if r["stock_code"] == c)["score"])) for c in open_codes) or 1.0
        pending = {
            c: residual
            * (
                max(0.0, float(next(r for r in selected if r["stock_code"] == c)["score"]))
                / ssum
            )
            for c in open_codes
        }

    weights = {k: v for k, v in weights.items() if v > 0.05}
    return weights, sector_sum, skipped[:20]


def risk_parity_lite_weights(
    ranked: List[dict],
    *,
    max_position_pct: float,
    max_sector_pct: float,
    max_positions: int,
) -> Tuple[Dict[str, float], Dict[str, float], List[Dict[str, Any]]]:
    """
    R3.3 lite：先按 score 选 TopN，再在限额内做近似风险平价。

    - 若行内有 vol（或 volatility）>0：权重 ∝ 1/vol
    - 否则等权（无协方差矩阵时的可复现退化）
    超限裁剪后回退剩余预算不再扩面；失败时调用方应回退 greedy/score_budget。
    """
    max_pos = float(max_position_pct)
    max_sec = float(max_sector_pct)
    max_n = int(max_positions)
    skipped: List[Dict[str, Any]] = []
    selected: List[dict] = []
    sector_cnt: Dict[str, int] = {}

    for row in ranked:
        if len(selected) >= max_n:
            skipped.append({**row, "reason": "max_positions"})
            continue
        sector = row["sector"]
        if sector_cnt.get(sector, 0) >= max(1, int(max_sec / max(0.1, max_pos))):
            skipped.append({**row, "reason": "sector_slot_full"})
            continue
        selected.append(row)
        sector_cnt[sector] = int(sector_cnt.get(sector) or 0) + 1

    if not selected:
        return {}, {}, skipped

    inv: Dict[str, float] = {}
    for r in selected:
        code = r["stock_code"]
        vol = r.get("vol")
        if vol is None:
            vol = r.get("volatility")
        try:
            vf = float(vol) if vol is not None else None
        except (TypeError, ValueError):
            vf = None
        if vf is not None and vf > 1e-8:
            inv[code] = 1.0 / vf
        else:
            inv[code] = 1.0
    inv_sum = sum(inv.values()) or 1.0
    total_budget = min(100.0, max_n * max_pos)
    raw = {c: total_budget * (inv[c] / inv_sum) for c in inv}

    weights: Dict[str, float] = {c: 0.0 for c in raw}
    sector_sum: Dict[str, float] = {}
    for code, want in sorted(raw.items(), key=lambda x: -x[1]):
        row = next(r for r in selected if r["stock_code"] == code)
        sector = row["sector"]
        room_pos = max_pos - float(weights.get(code) or 0.0)
        room_sec = max_sec - float(sector_sum.get(sector) or 0.0)
        take = min(want, room_pos, room_sec)
        if take <= 0.05:
            skipped.append({**row, "reason": "clip_no_room"})
            continue
        take = round(take, 4)
        weights[code] = take
        sector_sum[sector] = round(float(sector_sum.get(sector) or 0.0) + take, 4)

    weights = {k: v for k, v in weights.items() if v > 0.05}
    return weights, sector_sum, skipped[:20]


def qp_lite_weights(
    ranked: List[dict],
    *,
    max_position_pct: float,
    max_sector_pct: float,
    max_positions: int,
) -> Tuple[Dict[str, float], Dict[str, float], List[Dict[str, Any]], Dict[str, Any]]:
    """
    V3.4 可选：最大化 score·w，约束单票/行业/仓位数上限（百分比空间）。

    依赖 cvxpy；未安装或求解失败 → available=False，调用方回退 score_budget。
    """
    meta: Dict[str, Any] = {
        "available": False,
        "solver": None,
        "status": "unavailable",
        "message": "",
    }
    max_pos = float(max_position_pct)
    max_sec = float(max_sector_pct)
    max_n = int(max_positions)
    skipped: List[Dict[str, Any]] = []
    selected = list(ranked[:max_n])
    for row in ranked[max_n:]:
        skipped.append({**row, "reason": "max_positions"})
    if not selected:
        meta["message"] = "无候选"
        return {}, {}, skipped, meta

    try:
        import cvxpy as cp  # type: ignore
    except ImportError:
        meta["message"] = "未安装 cvxpy；weight_mode=qp_lite 不可用"
        return {}, {}, skipped, meta

    codes = [r["stock_code"] for r in selected]
    scores = [max(0.0, float(r["score"])) for r in selected]
    sectors = [r["sector"] for r in selected]
    n = len(codes)
    w = cp.Variable(n, nonneg=True)
    constraints = [
        w <= max_pos,
        cp.sum(w) <= min(100.0, max_n * max_pos),
    ]
    for sec in sorted(set(sectors)):
        idx = [i for i, s in enumerate(sectors) if s == sec]
        if idx:
            constraints.append(cp.sum(w[idx]) <= max_sec)
    objective = cp.Maximize(scores @ w)
    problem = cp.Problem(objective, constraints)
    try:
        problem.solve(solver=cp.SCS, warm_start=True, verbose=False)
    except Exception:
        try:
            problem.solve(verbose=False)
        except Exception as e:
            meta["message"] = f"求解失败: {e}"
            return {}, {}, skipped, meta

    if w.value is None or problem.status not in ("optimal", "optimal_inaccurate"):
        meta["status"] = str(problem.status)
        meta["message"] = f"求解未最优: {problem.status}"
        return {}, {}, skipped, meta

    weights: Dict[str, float] = {}
    sector_sum: Dict[str, float] = {}
    for i, code in enumerate(codes):
        val = float(w.value[i])
        if val <= 0.05:
            skipped.append({**selected[i], "reason": "qp_near_zero"})
            continue
        val = round(val, 4)
        weights[code] = val
        sec = sectors[i]
        sector_sum[sec] = round(float(sector_sum.get(sec) or 0.0) + val, 4)

    solver_name = "cvxpy"
    try:
        stats = getattr(problem, "solver_stats", None)
        if stats is not None and getattr(stats, "solver_name", None):
            solver_name = str(stats.solver_name)
    except Exception:
        pass
    meta.update(
        {
            "available": True,
            "solver": solver_name,
            "status": str(problem.status),
            "message": "qp_lite ok",
        }
    )
    return weights, sector_sum, skipped[:20], meta
