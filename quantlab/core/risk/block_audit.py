"""拦截复核标注与有效率趋势（R3.2 增强）。

在 core/north_star.py 的 summarize_risk_blocks 基础上扩展：
- 标注辅助函数（写入 outcome 到独立 JSONL 复核日志）
- 有效率趋势（按周/按码分桶）
- 复核闭环（标注后汇总有效率/误拦率/规则调优建议）

不改动 north_star.py；复核记录独立落盘于 data/block_audit.jsonl。
"""


import logging

logger = logging.getLogger(__name__)
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.paths import DATA_DIR

__all__ = [
    "audit_path",
    "label_block",
    "load_block_audits",
    "match_audits_to_blocks",
    "block_effectiveness_trend",
    "block_reason_breakdown",
    "suggest_block_rule_tuning",
    "summarize_block_audit",
]

# 最近邻匹配最大时间窗口（秒）：block.ts 与审计 block_ts 超过该窗口不匹配
_NEAREST_MATCH_WINDOW_SEC = 24 * 3600


def audit_path() -> str:
    """返回复核日志路径（DATA_DIR/block_audit.jsonl），确保目录存在。"""
    p = os.path.join(DATA_DIR, "block_audit.jsonl")
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    return p


def _norm_outcome(raw: Any) -> Optional[str]:
    """归一化 outcome 为 true_positive / false_positive / uncertain / None。"""
    s = str(raw or "").strip().lower()
    if not s or s in ("none", "null"):
        return None
    if s in ("true_positive", "tp", "true", "effective"):
        return "true_positive"
    if s in ("false_positive", "fp", "false", "false_block"):
        return "false_positive"
    if s in ("uncertain", "unknown"):
        return "uncertain"
    return None


def _parse_dt(raw: Any) -> Optional[datetime]:
    """解析 ISO 时间戳（兼容 Z 后缀），失败返回 None。"""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        if len(s) >= 19:
            return datetime.fromisoformat(s[:19])
        return datetime.fromisoformat(s)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return None


def label_block(
    block_ts: str,
    *,
    outcome: str,
    code: str = "",
    reason_code: str = "",
    reviewer: str = "",
    note: str = "",
) -> Dict[str, Any]:
    """写入一条复核标注到 block_audit.jsonl。

    block_ts: 被标注的拦截事件时间戳。
    outcome: true_positive（真违规，拦对了）/ false_positive（误拦）/ uncertain（不确定）。
    reviewer: 复核人标识。
    返回写入的记录 dict。
    """
    norm = _norm_outcome(outcome)
    if norm is None:
        raise ValueError(
            "无效 outcome；允许 true_positive|false_positive|uncertain"
        )
    record: Dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "block_ts": str(block_ts or "").strip(),
        "code": str(code or "").strip(),
        "reason_code": str(reason_code or "").strip(),
        "outcome": norm,
        "reviewer": str(reviewer or "").strip(),
        "note": str(note or "").strip(),
    }
    p = audit_path()
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_block_audits(*, limit: int = 500) -> List[Dict[str, Any]]:
    """加载复核记录（最近 limit 条）。"""
    p = audit_path()
    if not os.path.isfile(p):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-max(1, int(limit or 500)):]


def _block_info(entry: dict) -> Dict[str, str]:
    """从拦截流水条目提取 ts / code / reason_code。"""
    e = entry or {}
    meta = e.get("meta") or {}
    ts = str(e.get("ts") or meta.get("ts") or "").strip()
    code = str(meta.get("code") or meta.get("block_code") or "").strip()
    reason = str(
        meta.get("reason_code")
        or meta.get("reason")
        or meta.get("block_reason")
        or ""
    ).strip()
    if not reason:
        reason = "unspecified"
    return {"ts": ts, "code": code, "reason_code": reason}


def _extract_blocks(operation_log: Sequence[dict]) -> List[Dict[str, Any]]:
    """从 operation_log 提取 risk_block 条目（附解析后的 ts/code/reason_code）。"""
    out: List[Dict[str, Any]] = []
    for e in operation_log or []:
        if (e or {}).get("type") != "risk_block":
            continue
        info = _block_info(e)
        info["entry"] = e
        info["dt"] = _parse_dt(info["ts"])
        out.append(info)
    return out


def _match_audits(
    blocks: List[Dict[str, Any]],
    audits: List[Dict[str, Any]],
) -> List[Optional[Dict[str, Any]]]:
    """将审计匹配到拦截：先精确匹配 block_ts，再最近邻回退。每个审计最多用一次。"""
    matches: List[Optional[Dict[str, Any]]] = [None] * len(blocks)
    used: set = set()

    by_block_ts: Dict[str, int] = {}
    for i, a in enumerate(audits):
        bts = str(a.get("block_ts") or "").strip()
        if bts and bts not in by_block_ts:
            by_block_ts[bts] = i
    for bi, b in enumerate(blocks):
        bts = b.get("ts", "")
        if bts and bts in by_block_ts:
            ai = by_block_ts[bts]
            if ai not in used:
                matches[bi] = audits[ai]
                used.add(ai)

    parsed_audits: List[Tuple[datetime, int]] = []
    for i, a in enumerate(audits):
        if i in used:
            continue
        dt = _parse_dt(a.get("block_ts"))
        if dt is not None:
            parsed_audits.append((dt, i))
    for bi, b in enumerate(blocks):
        if matches[bi] is not None:
            continue
        bdt = b.get("dt")
        if bdt is None:
            continue
        best_ai: Optional[int] = None
        best_delta: Optional[float] = None
        for dt, ai in parsed_audits:
            if ai in used:
                continue
            delta = abs((dt - bdt).total_seconds())
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_ai = ai
        if (
            best_ai is not None
            and best_delta is not None
            and best_delta <= _NEAREST_MATCH_WINDOW_SEC
        ):
            matches[bi] = audits[best_ai]
            used.add(best_ai)
    return matches


def _resolve_outcome(
    block: Dict[str, Any],
    audit: Optional[Dict[str, Any]],
) -> Optional[str]:
    """确定一条拦截的 outcome：优先审计记录，回退 block.meta.outcome。"""
    if audit is not None:
        o = _norm_outcome(audit.get("outcome"))
        if o is not None:
            return o
    meta = (block.get("entry") or {}).get("meta") or {}
    return _norm_outcome(meta.get("outcome") or meta.get("label"))


def match_audits_to_blocks(
    operation_log: Sequence[dict],
    audits: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    """将复核标注匹配到拦截流水，计算有效率/误拦率。

    算法：
      a. 加载拦截流水（type=risk_block 的条目）
      b. 加载复核记录（audits 为 None 时从 block_audit.jsonl 读取）
      c. 按 block_ts 匹配（精确匹配或最近邻）
      d. 统计 total_blocks / labeled / TP / FP / uncertain
      e. 有效率 = TP / labeled；误拦率 = FP / labeled
    """
    if audits is None:
        audits = load_block_audits()
    audits = list(audits or [])
    blocks = _extract_blocks(operation_log)
    matches = _match_audits(blocks, audits)

    total_blocks = len(blocks)
    tp = 0
    fp = 0
    uncertain = 0
    labeled = 0
    by_reason: Dict[str, Dict[str, int]] = {}

    def _reason_bucket(reason: str) -> Dict[str, int]:
        if reason not in by_reason:
            by_reason[reason] = {"total": 0, "labeled": 0, "tp": 0, "fp": 0}
        return by_reason[reason]

    for b, a in zip(blocks, matches):
        rb = _reason_bucket(b["reason_code"])
        rb["total"] += 1
        outcome = _resolve_outcome(b, a)
        if outcome is None:
            continue
        if outcome == "uncertain":
            uncertain += 1
            continue
        labeled += 1
        rb["labeled"] += 1
        if outcome == "true_positive":
            tp += 1
            rb["tp"] += 1
        elif outcome == "false_positive":
            fp += 1
            rb["fp"] += 1

    effective_rate = round(tp / labeled, 4) if labeled > 0 else None
    false_block_rate = round(fp / labeled, 4) if labeled > 0 else None
    label_rate = round(labeled / total_blocks, 4) if total_blocks > 0 else None

    by_reason_out: Dict[str, Dict[str, Any]] = {}
    for reason, rb in by_reason.items():
        lr = rb["labeled"]
        by_reason_out[reason] = {
            "total": rb["total"],
            "labeled": lr,
            "tp": rb["tp"],
            "fp": rb["fp"],
            "eff_rate": round(rb["tp"] / lr, 4) if lr > 0 else None,
        }

    if total_blocks == 0:
        status = "no_audits"
        note = "无拦截流水。"
    elif not audits:
        status = "no_audits"
        note = f"拦截 {total_blocks} 条，尚无复核记录；标注后可算有效率/误拦率。"
    elif labeled == 0:
        status = "partial"
        note = (
            f"拦截 {total_blocks} 条 · 复核记录 {len(audits)} 条未匹配到拦截；"
            "请核对 block_ts。"
        )
    else:
        status = "ok"
        note = (
            f"拦截 {total_blocks} 条 · 已标注 {labeled} 条 · "
            f"有效率={effective_rate} · 误拦率={false_block_rate}。"
        )

    return {
        "total_blocks": total_blocks,
        "labeled": labeled,
        "label_rate": label_rate,
        "true_positives": tp,
        "false_positives": fp,
        "uncertain": uncertain,
        "effective_rate": effective_rate,
        "false_block_rate": false_block_rate,
        "unlabeled": total_blocks - labeled,
        "status": status,
        "by_reason": by_reason_out,
        "note": note,
    }


def block_effectiveness_trend(
    operation_log: Sequence[dict],
    audits: Optional[Sequence[dict]] = None,
    *,
    bucket_days: int = 7,
) -> Dict[str, Any]:
    """有效率趋势（按 bucket_days 天分桶）。

    算法：
      a. 匹配标注到拦截
      b. 按日期分桶（每 bucket_days 天一个桶，自最早拦截日起算）
      c. 每桶计算有效率、误拦率、拦截数、标注数
    trend_direction: improving/declining/stable/insufficient（需≥2 桶且最新桶有效率非空）。
    """
    if audits is None:
        audits = load_block_audits()
    audits = list(audits or [])
    blocks = _extract_blocks(operation_log)
    matches = _match_audits(blocks, audits)

    bucket_days = max(1, int(bucket_days or 7))
    dated: List[Tuple[datetime, Optional[str]]] = []
    for b, a in zip(blocks, matches):
        dt = b.get("dt")
        if dt is None:
            continue
        dated.append((dt, _resolve_outcome(b, a)))

    if not dated:
        return {
            "trend": [],
            "current_effective_rate": None,
            "trend_direction": "insufficient",
            "note": "无可分桶的拦截流水（缺有效时间戳）。",
        }

    min_dt = min(dt for dt, _ in dated)
    buckets: Dict[int, Dict[str, Any]] = {}
    for dt, outcome in dated:
        idx = (dt - min_dt).days // bucket_days
        if idx not in buckets:
            start = min_dt + timedelta(days=idx * bucket_days)
            end = start + timedelta(days=bucket_days)
            buckets[idx] = {
                "bucket_start": start.strftime("%Y-%m-%d"),
                "bucket_end": end.strftime("%Y-%m-%d"),
                "total_blocks": 0,
                "labeled": 0,
                "tp": 0,
                "fp": 0,
            }
        bk = buckets[idx]
        bk["total_blocks"] += 1
        if outcome in ("true_positive", "false_positive"):
            bk["labeled"] += 1
            if outcome == "true_positive":
                bk["tp"] += 1
            else:
                bk["fp"] += 1

    trend: List[Dict[str, Any]] = []
    for idx in sorted(buckets.keys()):
        bk = buckets[idx]
        lr = bk["labeled"]
        trend.append({
            "bucket_start": bk["bucket_start"],
            "bucket_end": bk["bucket_end"],
            "total_blocks": bk["total_blocks"],
            "labeled": lr,
            "effective_rate": round(bk["tp"] / lr, 4) if lr > 0 else None,
            "false_block_rate": round(bk["fp"] / lr, 4) if lr > 0 else None,
        })

    current_eff = trend[-1]["effective_rate"] if trend else None
    direction = "insufficient"
    if len(trend) >= 2:
        prev = trend[-2]["effective_rate"]
        cur = trend[-1]["effective_rate"]
        if prev is not None and cur is not None:
            delta = cur - prev
            if delta > 0.02:
                direction = "improving"
            elif delta < -0.02:
                direction = "declining"
            else:
                direction = "stable"

    return {
        "trend": trend,
        "current_effective_rate": current_eff,
        "trend_direction": direction,
        "note": f"按 {bucket_days} 天分桶，共 {len(trend)} 桶。",
    }


def block_reason_breakdown(
    operation_log: Sequence[dict],
    audits: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    """按拦截原因码分解有效率。

    对每个 reason_code 计算 TP/FP/有效率/误拦率/precision，
    帮助识别“哪类风控规则误拦最多”。
    precision = TP / (TP + FP)。
    """
    if audits is None:
        audits = load_block_audits()
    audits = list(audits or [])
    blocks = _extract_blocks(operation_log)
    matches = _match_audits(blocks, audits)

    agg: Dict[str, Dict[str, int]] = {}
    for b, a in zip(blocks, matches):
        reason = b["reason_code"]
        if reason not in agg:
            agg[reason] = {"total": 0, "labeled": 0, "tp": 0, "fp": 0}
        r = agg[reason]
        r["total"] += 1
        outcome = _resolve_outcome(b, a)
        if outcome in ("true_positive", "false_positive"):
            r["labeled"] += 1
            if outcome == "true_positive":
                r["tp"] += 1
            else:
                r["fp"] += 1

    reasons: List[Dict[str, Any]] = []
    for reason, r in agg.items():
        lr = r["labeled"]
        tp_fp = r["tp"] + r["fp"]
        reasons.append({
            "reason_code": reason,
            "total": r["total"],
            "labeled": lr,
            "tp": r["tp"],
            "fp": r["fp"],
            "eff_rate": round(r["tp"] / lr, 4) if lr > 0 else None,
            "false_rate": round(r["fp"] / lr, 4) if lr > 0 else None,
            "precision": round(r["tp"] / tp_fp, 4) if tp_fp > 0 else None,
        })
    reasons.sort(key=lambda x: -x["total"])

    worst: Optional[str] = None
    best: Optional[str] = None
    worst_p: Optional[float] = None
    best_p: Optional[float] = None
    for r in reasons:
        p = r["precision"]
        if p is None:
            continue
        if worst_p is None or p < worst_p:
            worst_p = p
            worst = r["reason_code"]
        if best_p is None or p > best_p:
            best_p = p
            best = r["reason_code"]

    return {
        "reasons": reasons,
        "worst_precision_reason": worst,
        "best_precision_reason": best,
        "note": "precision = TP/(TP+FP)；仅对有标注的原因码比较最差/最佳。",
    }


def suggest_block_rule_tuning(
    operation_log: Sequence[dict],
    audits: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    """基于复核数据给出规则调优建议。

    对每个原因码：
      - 误拦率 > 30% → “放宽阈值/增加豁免”
      - 有效率 > 95% 且拦截量大 → “保持”
      - 标注率 < 50% → “需补充标注”
    汇总建议并给出总标注率。
    """
    if audits is None:
        audits = load_block_audits()
    audits = list(audits or [])
    blocks = _extract_blocks(operation_log)
    matches = _match_audits(blocks, audits)

    agg: Dict[str, Dict[str, int]] = {}
    for b, a in zip(blocks, matches):
        reason = b["reason_code"]
        if reason not in agg:
            agg[reason] = {"total": 0, "labeled": 0, "tp": 0, "fp": 0}
        r = agg[reason]
        r["total"] += 1
        outcome = _resolve_outcome(b, a)
        if outcome in ("true_positive", "false_positive"):
            r["labeled"] += 1
            if outcome == "true_positive":
                r["tp"] += 1
            else:
                r["fp"] += 1

    total_blocks = sum(r["total"] for r in agg.values())
    total_labeled = sum(r["labeled"] for r in agg.values())
    overall_label_rate = (
        round(total_labeled / total_blocks, 4) if total_blocks > 0 else None
    )

    large_volume = 3  # “拦截量大”阈值
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    suggestions: List[Dict[str, Any]] = []
    for reason, r in agg.items():
        lr = r["labeled"]
        total = r["total"]
        eff = round(r["tp"] / lr, 4) if lr > 0 else None
        fbr = round(r["fp"] / lr, 4) if lr > 0 else None
        label_rate = round(lr / total, 4) if total > 0 else None
        if lr == 0:
            suggestion = "需补充标注"
            priority = "high"
        elif fbr is not None and fbr > 0.30:
            suggestion = "放宽阈值/增加豁免"
            priority = "high"
        elif label_rate is not None and label_rate < 0.50:
            suggestion = "需补充标注"
            priority = "medium"
        elif eff is not None and eff > 0.95 and total >= large_volume:
            suggestion = "保持"
            priority = "low"
        else:
            suggestion = "观察"
            priority = "low"
        suggestions.append({
            "reason_code": reason,
            "current_eff_rate": eff,
            "current_false_rate": fbr,
            "labeled": lr,
            "suggestion": suggestion,
            "priority": priority,
        })
    suggestions.sort(
        key=lambda x: (priority_rank.get(x["priority"], 9), -agg[x["reason_code"]]["total"])
    )

    return {
        "suggestions": suggestions,
        "overall_label_rate": overall_label_rate,
        "note": "误拦率>30%建议放宽；有效率>95%且量大建议保持；标注率<50%建议补标。",
    }


def summarize_block_audit(
    operation_log: Sequence[dict],
    *,
    audits: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    """汇总复核审计（一键报告）。

    聚合 match_audits_to_blocks + 趋势 + 原因码分解 + 调优建议，
    附一句话 headline 摘要。
    """
    if audits is None:
        audits = load_block_audits()
    effectiveness = match_audits_to_blocks(operation_log, audits)
    trend = block_effectiveness_trend(operation_log, audits)
    reasons = block_reason_breakdown(operation_log, audits)
    suggestions = suggest_block_rule_tuning(operation_log, audits)

    total = effectiveness["total_blocks"]
    labeled = effectiveness["labeled"]
    eff = effectiveness["effective_rate"]
    if total == 0:
        headline = "无拦截流水"
    elif labeled == 0:
        headline = f"拦截 {total} 条，尚未复核标注"
    else:
        eff_pct = f"{eff * 100:.1f}%" if eff is not None else "—"
        worst = reasons.get("worst_precision_reason")
        tail = f"；误拦最多：{worst}" if worst else ""
        headline = f"拦截有效率 {eff_pct}（已标注 {labeled}/{total}）{tail}"

    return {
        "effectiveness": effectiveness,
        "trend": trend,
        "reasons": reasons,
        "suggestions": suggestions,
        "headline": headline,
        "note": "聚合有效率/趋势/原因码分解/调优建议；复核记录见 block_audit.jsonl。",
    }


if __name__ == "__main__":
    # 模拟拦截流水
    op_log = [
        {"type": "risk_block", "ts": "2024-01-15T09:00:00", "meta": {"code": "600001", "reason": "sector_limit", "outcome": None}},
        {"type": "risk_block", "ts": "2024-01-15T10:00:00", "meta": {"code": "000002", "reason": "position_limit", "outcome": None}},
        {"type": "risk_block", "ts": "2024-01-16T09:30:00", "meta": {"code": "600003", "reason": "sector_limit", "outcome": None}},
        {"type": "risk_block", "ts": "2024-01-17T14:00:00", "meta": {"code": "000004", "reason": "drawdown_break", "outcome": None}},
        {"type": "normal_op", "ts": "2024-01-15T11:00:00", "meta": {}},
    ]
    # 模拟复核标注（直接构造，不写文件）
    audits = [
        {"ts": "2024-01-15T18:00:00", "block_ts": "2024-01-15T09:00:00", "code": "600001", "reason_code": "sector_limit", "outcome": "true_positive", "reviewer": "a", "note": "确实超限"},
        {"ts": "2024-01-15T18:01:00", "block_ts": "2024-01-15T10:00:00", "code": "000002", "reason_code": "position_limit", "outcome": "false_positive", "reviewer": "a", "note": "误拦"},
        {"ts": "2024-01-16T18:00:00", "block_ts": "2024-01-16T09:30:00", "code": "600003", "reason_code": "sector_limit", "outcome": "true_positive", "reviewer": "b", "note": "对的"},
    ]
    m = match_audits_to_blocks(op_log, audits)
    logger.info("match: labeled=%d/%d, eff=%.2f, false=%.2f" % (m["labeled"], m["total_blocks"], m["effective_rate"] or 0, m["false_block_rate"] or 0))
    t = block_effectiveness_trend(op_log, audits, bucket_days=7)
    logger.info("trend: buckets=%d, direction=%s" % (len(t["trend"]), t["trend_direction"]))
    rb = block_reason_breakdown(op_log, audits)
    logger.info("reasons: worst=%s, best=%s" % (rb["worst_precision_reason"], rb["best_precision_reason"]))
    sug = suggest_block_rule_tuning(op_log, audits)
    logger.info("suggestions: %d items" % len(sug["suggestions"]))
    summary = summarize_block_audit(op_log, audits=audits)
    logger.info("headline:", summary["headline"])
