"""观察名单舆情：标题缓存 · 规则情绪分 · 扫描告警 · as_of 历史（FS）。

Live 评分默认 ``sentiment.include_in_score=false``（不进 ŷ）；UI 徽章仍可用。
研究可用 ``sentiment_as_of`` 做 PIT 面板。LLM 分析不得写入 sub_scores。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Set

from core.paths import (
    LLM_SENTIMENT_DIR,
    NEWS_HISTORY_DIR,
    NEWS_STORE_DIR,
    SCHEDULE_LAST_RUN_PATH,
    SENTIMENT_LEXICON_PATH,
    WATCHING_PATH,
)
from core.io_atomic import atomic_write_json
DEFAULT_TTL_SEC = 15 * 60
MAX_BATCH = 20
ALERT_SCORE_THRESHOLD = 0.6

# 规则词典（可被 data/sentiment_lexicon.json 覆盖）
_DEFAULT_LEXICON: Dict[str, List[str]] = {
    "positive": [
        "增持",
        "回购",
        "中标",
        "签约",
        "盈利预增",
        "业绩预增",
        "超预期",
        "获批",
        "突破",
        "分红",
        "扭亏",
        "分红派发",
        "战略合作",
        "回购注销",
    ],
    "negative": [
        "减持",
        "立案",
        "亏损",
        "预亏",
        "退市",
        "违规",
        "处罚",
        "问询",
        "警示",
        "暴雷",
        "爆仓",
        "停牌",
        "诉讼",
        "造假",
        "下跌",
        "下滑",
        "承压",
    ],
}


def _safe_code(code: str) -> str:
    raw = str(code or "").strip()
    cleaned = re.sub(r"[^\w.\-]", "_", raw)[:32]
    return cleaned or "unknown"


def _cache_path(code: str) -> str:
    return os.path.join(NEWS_STORE_DIR, f"{_safe_code(code)}.json")


def load_lexicon(path: Optional[str] = None) -> Dict[str, List[str]]:
    p = path or SENTIMENT_LEXICON_PATH
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                pos = data.get("positive") or data.get("bullish") or []
                neg = data.get("negative") or data.get("bearish") or []
                if isinstance(pos, list) and isinstance(neg, list):
                    return {
                        "positive": [str(x) for x in pos if str(x).strip()],
                        "negative": [str(x) for x in neg if str(x).strip()],
                    }
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return {
        "positive": list(_DEFAULT_LEXICON["positive"]),
        "negative": list(_DEFAULT_LEXICON["negative"]),
    }


def _annotate_include_gate(sent: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """给 UI 徽章挂上 live 闸 / 先验角色（与 score 行为一致）。"""
    out = dict(sent or {})
    try:
        from core.signal.config import load_signal_config

        cfg = (load_signal_config() or {}).get("sentiment") or {}
        out["include_in_score"] = bool(cfg.get("include_in_score", False))
        out["role"] = str(cfg.get("role") or "prior")
    except Exception:
        out["include_in_score"] = False
        out["role"] = "prior"
    return out


def score_headlines(
    items: Sequence[dict],
    *,
    lexicon: Optional[Dict[str, List[str]]] = None,
    degraded: bool = False,
    degrade_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """规则情绪：有命中时打标签；强度按「命中标题占比」稀释。

    - label：由唯一关键词极性（仅负 / 仅正 / 混合）
    - score：``polar * coverage``，其中 polar=neg/(pos+neg)，
      coverage=命中标题数/总标题数。避免「5 条里 1 条分行违规」被打成 1.0
      误触发 gate 缩仓。
    - 无命中 → neutral / score=null
    - degraded=True：超时/空源降级，prior 不触发 gate（prior_eligible=false）
    """
    lex = lexicon or load_lexicon()
    pos_words = [w for w in (lex.get("positive") or []) if w]
    neg_words = [w for w in (lex.get("negative") or []) if w]
    hit_pos: List[str] = []
    hit_neg: List[str] = []
    seen_pos: Set[str] = set()
    seen_neg: Set[str] = set()
    n_titles = 0
    n_hit_titles = 0
    for it in items or []:
        title = str((it or {}).get("title") or "")
        if not title:
            continue
        n_titles += 1
        title_pos = False
        title_neg = False
        for w in pos_words:
            if w in title:
                title_pos = True
                if w not in seen_pos:
                    seen_pos.add(w)
                    hit_pos.append(w)
        for w in neg_words:
            if w in title:
                title_neg = True
                if w not in seen_neg:
                    seen_neg.add(w)
                    hit_neg.append(w)
        if title_pos or title_neg:
            n_hit_titles += 1
    n_pos, n_neg = len(hit_pos), len(hit_neg)
    total = n_pos + n_neg
    if total == 0 or n_titles <= 0:
        out = _annotate_include_gate(
            {
                "label": "neutral",
                "score": None,
                "hit_pos": [],
                "hit_neg": [],
                "n_titles": n_titles,
                "n_hit_titles": 0,
                "coverage": 0.0,
                "note": "无关键词命中" if n_titles else "无标题",
            }
        )
    else:
        polar = n_neg / total
        coverage = n_hit_titles / float(n_titles)
        # 强度 = 极性 × 标题覆盖；稀疏负向不再虚高到 1.0
        score = round(polar * coverage, 4)
        if n_pos and n_neg:
            label = "mixed"
        elif n_neg:
            label = "bearish"
        else:
            label = "bullish"
        out = _annotate_include_gate(
            {
                "label": label,
                "score": score,
                "hit_pos": hit_pos,
                "hit_neg": hit_neg,
                "n_titles": n_titles,
                "n_hit_titles": n_hit_titles,
                "coverage": round(coverage, 4),
                "polar": round(polar, 4),
                "note": "规则关键词×标题覆盖，非模型",
            }
        )
    if degraded:
        out["degraded"] = True
        out["prior_eligible"] = False
        if degrade_reason:
            out["degrade_reason"] = str(degrade_reason)[:200]
        note = str(out.get("note") or "")
        out["note"] = (note + " · 降级不触发 prior").strip(" ·")
    else:
        out.setdefault("degraded", False)
        out.setdefault("prior_eligible", True)
    return out


def _mark_sentiment_degraded(
    sent: Dict[str, Any], *, reason: str
) -> Dict[str, Any]:
    out = _annotate_include_gate(dict(sent or {}))
    out["degraded"] = True
    out["prior_eligible"] = False
    out["degrade_reason"] = str(reason or "")[:200]
    return out


def _read_cache(code: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(code)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _write_cache(code: str, payload: Dict[str, Any]) -> str:
    os.makedirs(NEWS_STORE_DIR, exist_ok=True)
    path = _cache_path(code)
    atomic_write_json(path, payload)
    try:
        append_headline_history(code, payload)
    except Exception:
        pass
    return path


def _history_path(code: str) -> str:
    return os.path.join(NEWS_HISTORY_DIR, f"{_safe_code(code)}.jsonl")


def append_headline_history(code: str, payload: Dict[str, Any]) -> Optional[str]:
    """FS2：成功/失败拉取均追加一行，供 as_of 研究（不改 live ŷ）。"""
    from core.signal.config import load_signal_config

    cfg = (load_signal_config() or {}).get("sentiment") or {}
    if not bool(cfg.get("append_history", True)):
        return None
    os.makedirs(NEWS_HISTORY_DIR, exist_ok=True)
    path = _history_path(code)
    sent = payload.get("sentiment") if isinstance(payload.get("sentiment"), dict) else {}
    items = list(payload.get("items") or [])
    titles = []
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        titles.append(
            {
                "title": str(it.get("title") or "")[:200],
                "time": str(it.get("time") or it.get("datetime") or "")[:32],
                "source": str(it.get("source") or "")[:40],
            }
        )
    row = {
        "code": str(code or "").strip(),
        "fetched_at": payload.get("updated_at")
        or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "ok": bool(payload.get("ok", True)),
        "titles": titles,
        "rule_label": sent.get("label"),
        "rule_score": sent.get("score"),
        "n_titles": len(titles),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    # 截断过长日志
    max_lines = max(50, min(int(cfg.get("history_max_lines_per_code") or 500), 5000))
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            import tempfile

            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines[-max_lines:])
            os.replace(tmp, path)
    except OSError:
        pass
    return path


def _parse_item_date(raw: Any) -> Optional[str]:
    s = str(raw or "").strip()
    if not s:
        return None
    # 常见：2024-01-02 / 2024-01-02 15:00:00 / 01-02
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def sentiment_as_of(
    code: str,
    as_of: str,
    *,
    lexicon: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """FS2：决策日 as_of 舆情——只用 history 中 time≤as_of 的标题重跑 lexicon。"""
    as_of_d = str(as_of or "")[:10]
    path = _history_path(code)
    titles: List[dict] = []
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for t in row.get("titles") or []:
                        if not isinstance(t, dict):
                            continue
                        td = _parse_item_date(t.get("time")) or _parse_item_date(
                            row.get("fetched_at")
                        )
                        if td and td <= as_of_d:
                            titles.append(t)
        except OSError:
            pass
    # 去重标题
    seen = set()
    uniq = []
    for t in titles:
        title = str(t.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        uniq.append(t)
    sent = score_headlines(uniq, lexicon=lexicon or load_lexicon())
    # 特征小升级：覆盖与强度（保留 score_headlines 的数值 coverage）
    n = len(uniq)
    sent = dict(sent)
    sent["n_titles"] = n
    sent["as_of"] = as_of_d
    sent["coverage_status"] = "ok" if n > 0 else "missing"
    try:
        from core.signal.factors.alt_sentiment import score_alt_sentiment

        factor_score, _meta = score_alt_sentiment([], sentiment=sent)
    except Exception:
        factor_score = 50.0
    return {
        "ok": n > 0,
        "code": str(code or "").strip(),
        "as_of": as_of_d,
        "sentiment": sent,
        "metrics": {"alt_sentiment": factor_score, "n_titles": n},
        "factor_score": factor_score,
        "note": "PIT 标题 as_of；无日志则 missing",
    }


def build_sentiment_panel_coverage(
    codes: Sequence[str],
    *,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """验证宇宙舆情 history 面板覆盖（只读）。"""
    as_of_d = str(as_of or "").strip()[:10] or time.strftime("%Y-%m-%d")
    items: List[Dict[str, Any]] = []
    ok_n = 0
    missing_n = 0
    for code in codes or []:
        c = str(code or "").strip()
        if not c:
            continue
        row = sentiment_as_of(c, as_of_d)
        status = str((row.get("sentiment") or {}).get("coverage_status") or "missing")
        has = bool(row.get("ok")) and status == "ok"
        if has:
            ok_n += 1
        else:
            missing_n += 1
        items.append(
            {
                "stock_code": c,
                "ok": has,
                "coverage_status": status,
                "n_titles": (row.get("metrics") or {}).get("n_titles") or 0,
                "label": (row.get("sentiment") or {}).get("label"),
            }
        )
    total = ok_n + missing_n
    return {
        "ok": True,
        "as_of": as_of_d,
        "total": total,
        "covered": ok_n,
        "missing_count": missing_n,
        "coverage": round(ok_n / total, 3) if total else 1.0,
        "items": items,
        "note": "history jsonl as_of 面板；缺失不进 ŷ，仅诊断。",
    }


def warmup_sentiment_history(
    codes: Sequence[str],
    *,
    limit: int = 5,
) -> Dict[str, Any]:
    """强制拉取标题并写入 history，供 as_of 面板。"""
    lexicon = load_lexicon()
    scanned: List[Dict[str, Any]] = []
    ok_n = 0
    for code in list(codes or [])[:MAX_BATCH]:
        c = str(code or "").strip()
        if not c:
            continue
        try:
            row = fetch_stock_headlines(
                c, limit=limit, force=True, lexicon=lexicon
            )
        except Exception as exc:
            row = {
                "ok": False,
                "stock_code": c,
                "error": str(exc),
                "sentiment": score_headlines([], lexicon=lexicon, degraded=True),
            }
        if row.get("ok"):
            ok_n += 1
        scanned.append(
            {
                "stock_code": row.get("stock_code") or c,
                "ok": row.get("ok"),
                "stale": row.get("stale"),
                "label": (row.get("sentiment") or {}).get("label"),
                "degraded": (row.get("sentiment") or {}).get("degraded"),
                "n_titles": len(row.get("items") or []),
            }
        )
    return {
        "ok": True,
        "kind": "sentiment_history_warmup",
        "scanned": len(scanned),
        "ok_count": ok_n,
        "items": scanned,
        "note": "写入 news/history；prior-only，不进 ŷ。",
    }


def _cache_fresh(cached: Dict[str, Any], *, ttl_sec: int) -> bool:
    try:
        ts = float(cached.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return False
    return (time.time() - ts) <= max(0, int(ttl_sec))


def _attach_sentiment(items: List[dict], lexicon: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    return score_headlines(items, lexicon=lexicon)


def fetch_stock_headlines(
    code: str,
    *,
    limit: int = 5,
    force: bool = False,
    ttl_sec: int = DEFAULT_TTL_SEC,
    lexicon: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """单票标题；默认读短 TTL 缓存，force 时绕过。"""
    limit = max(1, min(int(limit or 5), 15))
    code_key = str(code or "").strip()
    if not code_key:
        return {
            "ok": False,
            "stock_code": "",
            "stock_name": "",
            "items": [],
            "sentiment": score_headlines([], lexicon=lexicon),
            "error": "缺少股票代码",
            "updated_at": None,
            "from_cache": False,
        }

    cached = _read_cache(code_key)
    if cached and not force and _cache_fresh(cached, ttl_sec=ttl_sec):
        items = list(cached.get("items") or [])[:limit]
        sent = cached.get("sentiment")
        if not isinstance(sent, dict):
            sent = _attach_sentiment(items, lexicon=lexicon)
        sent = _annotate_include_gate(sent if isinstance(sent, dict) else {})
        sent.setdefault("degraded", False)
        sent.setdefault("prior_eligible", True)
        return {
            "ok": True,
            "stock_code": cached.get("stock_code") or code_key,
            "stock_name": cached.get("stock_name") or "",
            "items": items,
            "sentiment": sent,
            "updated_at": cached.get("updated_at"),
            "from_cache": True,
            "error": None,
        }

    from core.data_service import get_news

    try:
        raw = get_news(code_key, limit=limit)
    except Exception as exc:
        # 超时/网络异常：优先回退过期缓存，避免 UI 永久加载中
        reason = f"资讯源超时：{exc}"
        if cached:
            items = list(cached.get("items") or [])[:limit]
            sent = cached.get("sentiment")
            if not isinstance(sent, dict):
                sent = _attach_sentiment(items, lexicon=lexicon)
            sent = _mark_sentiment_degraded(sent, reason=reason)
            return {
                "ok": True,
                "stock_code": cached.get("stock_code") or code_key,
                "stock_name": cached.get("stock_name") or "",
                "items": items,
                "sentiment": sent,
                "updated_at": cached.get("updated_at"),
                "from_cache": True,
                "stale": True,
                "error": f"资讯源超时，已显示缓存：{exc}",
            }
        return {
            "ok": False,
            "stock_code": code_key,
            "stock_name": "",
            "items": [],
            "sentiment": score_headlines(
                [], lexicon=lexicon, degraded=True, degrade_reason=reason
            ),
            "error": f"资讯拉取失败：{exc}",
            "updated_at": None,
            "from_cache": False,
        }
    now = time.time()
    updated_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    if not raw.get("success"):
        err = str(raw.get("error") or "未获取到相关资讯")
        if cached and (cached.get("items") or []):
            items = list(cached.get("items") or [])[:limit]
            sent = cached.get("sentiment")
            if not isinstance(sent, dict):
                sent = _attach_sentiment(items, lexicon=lexicon)
            sent = _mark_sentiment_degraded(sent, reason=err)
            return {
                "ok": True,
                "stock_code": cached.get("stock_code") or code_key,
                "stock_name": cached.get("stock_name") or "",
                "items": items,
                "sentiment": sent,
                "updated_at": cached.get("updated_at"),
                "from_cache": True,
                "stale": True,
                "error": f"资讯源不可用，已显示缓存：{err}",
            }
        payload = {
            "ok": False,
            "stock_code": raw.get("stock_code") or code_key,
            "stock_name": raw.get("stock_name") or "",
            "items": [],
            "sentiment": score_headlines(
                [], lexicon=lexicon, degraded=True, degrade_reason=err
            ),
            "error": err,
            "updated_at": updated_at,
            "fetched_at": now,
            "from_cache": False,
        }
        _write_cache(code_key, payload)
        return {
            "ok": False,
            "stock_code": payload["stock_code"],
            "stock_name": payload["stock_name"],
            "items": [],
            "sentiment": payload["sentiment"],
            "error": err,
            "updated_at": updated_at,
            "from_cache": False,
        }

    items = list(raw.get("items") or [])[:limit]
    sent = _attach_sentiment(items, lexicon=lexicon)
    if not items:
        sent = score_headlines(
            [], lexicon=lexicon, degraded=True, degrade_reason="空标题"
        )
    payload = {
        "ok": True,
        "stock_code": raw.get("stock_code") or code_key,
        "stock_name": raw.get("stock_name") or "",
        "items": items,
        "sentiment": sent,
        "error": None,
        "updated_at": updated_at,
        "fetched_at": now,
        "from_cache": False,
    }
    _write_cache(code_key, payload)
    out = dict(payload)
    out.pop("fetched_at", None)
    return out


def _watchlist_codes(codes: Optional[Sequence[str]] = None) -> List[str]:
    if codes is not None:
        return [str(c).strip() for c in codes if str(c).strip()][:MAX_BATCH]
    if not os.path.isfile(WATCHING_PATH):
        return []
    try:
        with open(WATCHING_PATH, "r", encoding="utf-8") as f:
            uni = json.load(f)
        wl = uni.get("watchlist") or []
        return [str(c).strip() for c in wl if str(c).strip()][:MAX_BATCH]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def list_watchlist_sentiment(
    codes: Optional[Sequence[str]] = None,
    *,
    limit: int = 3,
    force: bool = False,
    ttl_sec: int = DEFAULT_TTL_SEC,
) -> Dict[str, Any]:
    """批量观察舆情（串行，上限 MAX_BATCH）。"""
    limit = max(1, min(int(limit or 3), 8))
    watch = _watchlist_codes(codes)
    lexicon = load_lexicon()
    items: List[Dict[str, Any]] = []
    for code in watch:
        try:
            row = fetch_stock_headlines(
                code,
                limit=limit,
                force=force,
                ttl_sec=ttl_sec,
                lexicon=lexicon,
            )
        except Exception as exc:
            row = {
                "ok": False,
                "stock_code": code,
                "stock_name": "",
                "items": [],
                "sentiment": score_headlines([], lexicon=lexicon),
                "error": f"资讯拉取失败：{exc}",
                "updated_at": None,
                "from_cache": False,
            }
        items.append(row)
    return {
        "ok": True,
        "count": len(items),
        "limit": limit,
        "items": items,
        "note": "观察舆情标题与规则情绪；默认不参与 score/ŷ（sentiment.include_in_score）。",
    }


def _title_set(items: Sequence[dict]) -> Set[str]:
    return {str(it.get("title") or "").strip() for it in items if str(it.get("title") or "").strip()}


def detect_alerts(
    before: Dict[str, Any],
    after: Dict[str, Any],
    *,
    score_threshold: float = ALERT_SCORE_THRESHOLD,
) -> Optional[Dict[str, Any]]:
    """相对上次缓存：新标题且 bearish/mixed，或 score 跃迁 ≥ 阈值。"""
    if not after.get("ok"):
        return None
    old_items = list((before or {}).get("items") or [])
    new_items = list((after or {}).get("items") or [])
    old_titles = _title_set(old_items)
    new_titles = _title_set(new_items)
    added = sorted(new_titles - old_titles)

    old_sent = (before or {}).get("sentiment") or {}
    new_sent = (after or {}).get("sentiment") or {}
    if not isinstance(old_sent, dict):
        old_sent = {}
    if not isinstance(new_sent, dict):
        new_sent = {}

    label = str(new_sent.get("label") or "neutral")
    try:
        old_score = old_sent.get("score")
        old_score_f = float(old_score) if old_score is not None else None
    except (TypeError, ValueError):
        old_score_f = None
    try:
        new_score = new_sent.get("score")
        new_score_f = float(new_score) if new_score is not None else None
    except (TypeError, ValueError):
        new_score_f = None

    reasons: List[str] = []
    if added and label in ("bearish", "mixed"):
        reasons.append(f"新标题·{label}")
    crossed = (
        new_score_f is not None
        and new_score_f >= score_threshold
        and (old_score_f is None or old_score_f < score_threshold)
    )
    if crossed:
        reasons.append(f"风险分≥{score_threshold}")

    if not reasons:
        return None
    return {
        "stock_code": after.get("stock_code"),
        "stock_name": after.get("stock_name"),
        "label": label,
        "score": new_score_f,
        "new_titles": added[:5],
        "reasons": reasons,
        "sentiment": new_sent,
    }


def scan_watchlist_sentiment(
    codes: Optional[Sequence[str]] = None,
    *,
    limit: int = 5,
    score_threshold: float = ALERT_SCORE_THRESHOLD,
) -> Dict[str, Any]:
    """强制刷新观察名单舆情并生成告警。"""
    limit = max(1, min(int(limit or 5), 8))
    watch = _watchlist_codes(codes)
    lexicon = load_lexicon()
    alerts: List[Dict[str, Any]] = []
    scanned: List[Dict[str, Any]] = []
    for code in watch:
        before = _read_cache(code) or {}
        after = fetch_stock_headlines(
            code,
            limit=limit,
            force=True,
            lexicon=lexicon,
        )
        scanned.append(
            {
                "stock_code": after.get("stock_code") or code,
                "stock_name": after.get("stock_name"),
                "ok": after.get("ok"),
                "label": (after.get("sentiment") or {}).get("label"),
                "score": (after.get("sentiment") or {}).get("score"),
            }
        )
        alert = detect_alerts(before, after, score_threshold=score_threshold)
        if alert:
            alerts.append(alert)
    return {
        "ok": True,
        "kind": "sentiment_scan",
        "scanned": len(watch),
        "alert_count": len(alerts),
        "alerts": alerts,
        "items": scanned,
        "note": "仅页内提醒骨架，非推送通道；不代客下单；不进 score/stance。",
    }


def read_last_sentiment_alerts() -> Dict[str, Any]:
    """读取上次 schedule/scan 写入的舆情告警。"""
    if not os.path.isfile(SCHEDULE_LAST_RUN_PATH):
        return {
            "ok": True,
            "kind": None,
            "alert_count": 0,
            "alerts": [],
            "scanned": 0,
            "updated_at": None,
            "note": "尚无扫描记录",
        }
    try:
        with open(SCHEDULE_LAST_RUN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return {
            "ok": True,
            "kind": None,
            "alert_count": 0,
            "alerts": [],
            "scanned": 0,
            "updated_at": None,
            "note": "扫描记录不可读",
        }
    if not isinstance(data, dict) or data.get("kind") != "sentiment_scan":
        return {
            "ok": True,
            "kind": data.get("kind") if isinstance(data, dict) else None,
            "alert_count": 0,
            "alerts": [],
            "scanned": 0,
            "updated_at": None,
            "note": "最近一次调度不是舆情扫描",
        }
    alerts = list(data.get("alerts") or [])
    ts = data.get("ts")
    updated_at = None
    if isinstance(ts, (int, float)):
        updated_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(ts)))
    return {
        "ok": True,
        "kind": "sentiment_scan",
        "alert_count": int(data.get("alert_count") or len(alerts)),
        "alerts": alerts,
        "scanned": int(data.get("scanned") or 0),
        "updated_at": updated_at,
        "path": SCHEDULE_LAST_RUN_PATH,
        "note": data.get("note") or "",
    }


# --------------------------------------------------------------------------- #
# LLM 情绪打分（研究轨）：Qwen 对新闻标题+正文语义分析；带磁盘缓存。
# 不进生产 ŷ（prior_only）；仅作 alt_sentiment 的研究对照。
# --------------------------------------------------------------------------- #

LLM_SENTIMENT_TTL_SEC = 30 * 60  # LLM 调用昂贵，缓存 30 分钟
LLM_SENTIMENT_MAX_ITEMS = 8

_LLM_SYSTEM_PROMPT = (
    "你是一位严谨的 A 股/港股/美股舆情分析师。"
    "请根据提供的新闻标题和正文摘要，判断该股票近期整体舆情倾向。"
    "只输出 JSON，不要输出其他内容。"
    '格式: {"label": "bullish|bearish|neutral|mixed", '
    '"score": 0.0到1.0, "reasoning": "简要理由"}。'
    "score 含义: 0.0=极度利空, 0.5=中性, 1.0=极度利好。"
    "多条新闻混合正负时 label=mixed，score 取加权中值。"
)


def _items_fingerprint(items: Sequence[dict]) -> str:
    """对标题集做稳定哈希（content 不参与，避免正文抓取波动导致缓存失效）。"""
    titles = sorted(
        str((it or {}).get("title") or "").strip()
        for it in (items or [])
        if str((it or {}).get("title") or "").strip()
    )
    raw = "\n".join(titles)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _llm_cache_path(code: str) -> str:
    safe = _safe_code(code)
    return os.path.join(LLM_SENTIMENT_DIR, f"{safe}.json")


def _read_llm_cache(code: str) -> Optional[Dict[str, Any]]:
    path = _llm_cache_path(code)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _llm_cache_fresh(cached: Dict[str, Any], *, ttl_sec: int) -> bool:
    try:
        ts = float(cached.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return False
    return (time.time() - ts) <= max(0, int(ttl_sec))


def _build_llm_user_prompt(items: Sequence[dict]) -> str:
    lines = ["请分析以下新闻的舆情倾向：", ""]
    for i, it in enumerate(items[:LLM_SENTIMENT_MAX_ITEMS], 1):
        title = str(it.get("title") or "").strip()
        content = str(it.get("content") or "").strip()
        line = f"{i}. {title}"
        if content:
            line += f"（正文摘要: {content}）"
        lines.append(line)
    lines.append("")
    lines.append("请输出 JSON。")
    return "\n".join(lines)


def _parse_llm_response(content: str) -> Dict[str, Any]:
    """从 Qwen 响应中解析 JSON；容忍 markdown 代码块包裹。"""
    text = str(content or "").strip()
    # 去除 ```json ... ``` 包裹
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取第一个 {...} 片段
        match = re.search(r"\{[^{}]*\}", text, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
    label = str(data.get("label") or "neutral").lower()
    if label not in ("bullish", "bearish", "neutral", "mixed"):
        label = "neutral"
    try:
        score = float(data.get("score"))
        if not (0.0 <= score <= 1.0):
            score = 0.5
    except (TypeError, ValueError):
        score = 0.5
    reasoning = str(data.get("reasoning") or "")[:200]
    return {"label": label, "score": round(score, 4), "reasoning": reasoning}


def score_headlines_llm(
    code: str,
    items: Sequence[dict],
    *,
    force: bool = False,
    ttl_sec: int = LLM_SENTIMENT_TTL_SEC,
) -> Dict[str, Any]:
    """用 Qwen LLM 对新闻标题+正文打情绪分；带磁盘缓存。

    定位：研究轨因子（prior_only），不进生产 ŷ。
    与 ``score_headlines``（规则词典）并行，供研究对照。

    Returns:
        ``{label, score, reasoning, source, n_titles, from_cache, fetched_at}``
    """
    code_key = str(code or "").strip()
    titles = [
        str((it or {}).get("title") or "").strip()
        for it in (items or [])
        if str((it or {}).get("title") or "").strip()
    ]
    n_titles = len(titles)

    if n_titles == 0:
        return _annotate_include_gate(
            {
                "label": "neutral",
                "score": 0.5,
                "reasoning": "无新闻标题",
                "source": "qwen",
                "n_titles": 0,
                "from_cache": False,
                "fetched_at": time.time(),
            }
        )

    fp = _items_fingerprint(items)

    # 读缓存
    if not force:
        cached = _read_llm_cache(code_key)
        if cached and _llm_cache_fresh(cached, ttl_sec=ttl_sec):
            if cached.get("fingerprint") == fp:
                out = dict(cached)
                out["from_cache"] = True
                return _annotate_include_gate(out)

    # 调 LLM
    try:
        from agent.llm_client import LLMClient

        client = LLMClient()
        if not client.is_available():
            return _annotate_include_gate(
                {
                    "label": "neutral",
                    "score": 0.5,
                    "reasoning": f"LLM 不可用: {client.get_last_error()}",
                    "source": "qwen",
                    "n_titles": n_titles,
                    "from_cache": False,
                    "fetched_at": time.time(),
                }
            )

        messages = [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {"role": "user", "content": _build_llm_user_prompt(items)},
        ]
        response = client.chat(messages, enable_search=False)
        content = client.get_response_content(response)
        parsed = _parse_llm_response(content)
    except Exception as e:
        return _annotate_include_gate(
            {
                "label": "neutral",
                "score": 0.5,
                "reasoning": f"LLM 调用失败: {e}",
                "source": "qwen",
                "n_titles": n_titles,
                "from_cache": False,
                "fetched_at": time.time(),
            }
        )

    now = time.time()
    payload = {
        "code": code_key,
        "fingerprint": fp,
        "label": parsed["label"],
        "score": parsed["score"],
        "reasoning": parsed["reasoning"],
        "source": "qwen",
        "n_titles": n_titles,
        "from_cache": False,
        "fetched_at": now,
    }

    # 写缓存（原子写）
    try:
        os.makedirs(LLM_SENTIMENT_DIR, exist_ok=True)
        atomic_write_json(_llm_cache_path(code_key), payload)
    except Exception:
        pass

    return _annotate_include_gate(payload)
