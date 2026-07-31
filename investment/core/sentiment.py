"""观察名单舆情：标题缓存 · 规则情绪分 · 扫描告警（不进 score/stance）。"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Set

from core.paths import NEWS_STORE_DIR, SCHEDULE_LAST_RUN_PATH, SENTIMENT_LEXICON_PATH, WATCHING_PATH

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


def score_headlines(
    items: Sequence[dict],
    *,
    lexicon: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """规则情绪：有命中时 score=neg/(pos+neg)；无命中 → neutral / score=null。"""
    lex = lexicon or load_lexicon()
    pos_words = [w for w in (lex.get("positive") or []) if w]
    neg_words = [w for w in (lex.get("negative") or []) if w]
    hit_pos: List[str] = []
    hit_neg: List[str] = []
    seen_pos: Set[str] = set()
    seen_neg: Set[str] = set()
    for it in items or []:
        title = str((it or {}).get("title") or "")
        if not title:
            continue
        for w in pos_words:
            if w in title and w not in seen_pos:
                seen_pos.add(w)
                hit_pos.append(w)
        for w in neg_words:
            if w in title and w not in seen_neg:
                seen_neg.add(w)
                hit_neg.append(w)
    n_pos, n_neg = len(hit_pos), len(hit_neg)
    total = n_pos + n_neg
    if total == 0:
        return {
            "label": "neutral",
            "score": None,
            "hit_pos": [],
            "hit_neg": [],
            "note": "无关键词命中",
        }
    score = round(n_neg / total, 4)
    if n_pos and n_neg:
        label = "mixed"
    elif n_neg:
        label = "bearish"
    else:
        label = "bullish"
    return {
        "label": label,
        "score": score,
        "hit_pos": hit_pos,
        "hit_neg": hit_neg,
        "note": "规则关键词，非模型",
    }


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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


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

    from core.ports.market import build_news

    raw = build_news(code_key, limit=limit)
    now = time.time()
    updated_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    if not raw.get("success"):
        err = str(raw.get("error") or "未获取到相关资讯")
        payload = {
            "ok": False,
            "stock_code": raw.get("stock_code") or code_key,
            "stock_name": raw.get("stock_name") or "",
            "items": [],
            "sentiment": score_headlines([], lexicon=lexicon),
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
        row = fetch_stock_headlines(
            code,
            limit=limit,
            force=force,
            ttl_sec=ttl_sec,
            lexicon=lexicon,
        )
        items.append(row)
    return {
        "ok": True,
        "count": len(items),
        "limit": limit,
        "items": items,
        "note": "观察舆情标题与规则情绪；不参与 score/stance。",
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
