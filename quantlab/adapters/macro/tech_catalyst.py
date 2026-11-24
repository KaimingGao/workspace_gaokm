"""海外科技隔夜涨因：东财全球快讯为主，CNBC Tech RSS 兜底（非 PIT）。"""

from __future__ import annotations

import html
import logging
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SEC = 15.0
_UA = {"User-Agent": "Mozilla/5.0 QuantLabTechCatalyst/1.0"}

# 美股科技 / 宏观相关（中英）
_TECH_RE = re.compile(
    r"纳斯达克|纳指|美股|费城半导体|半导体|英伟达|黄仁勋|英偉達|"
    r"苹果|微軟|微软|谷歌|Meta|特斯拉|高通|AMD|台积电|台積電|"
    r"芯片|晶片|人工智能|AI\b|QQQ|SOX|NDX|Nasdaq|Nvidia|Huang|"
    r"美联储|聯準會|降息|加息|FOMC|CPI|非农|非農",
    re.I,
)

# 更像「隔夜涨跌原因」的信号
_CATALYST_RE = re.compile(
    r"大涨|大跌|飙升|暴涨|暴跌|走高|走低|收涨|收跌|反弹|跳水|"
    r"复职|復職|财报|財報|指引|加息|降息|关税|關稅|制裁|"
    r"surge|soar|plunge|rally|jump|drop|beat|miss|CEO|earnings",
    re.I,
)

_CNBC_TECH_RSS = (
    "https://search.cnbc.com/rs/search/combinedcms/view.xml"
    "?partnerId=wrss01&id=19854910"
)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _call_with_timeout(fn, *args, timeout: Optional[float] = None, **kwargs):
    lim = float(timeout if timeout is not None else _FETCH_TIMEOUT_SEC)
    if lim <= 0:
        return fn(*args, **kwargs)
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=lim)
        except FuturesTimeoutError:
            logger.warning("tech catalyst timeout after %.0fs: %s", lim, getattr(fn, "__name__", fn))
            return None
        except Exception:
            logger.debug(
                "tech catalyst raised: %s",
                getattr(fn, "__name__", fn),
                exc_info=True,
            )
            return None


def _clean_text(raw: Any, *, limit: int = 160) -> str:
    s = html.unescape(str(raw or "")).strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) > limit:
        return s[: limit - 1].rstrip() + "…"
    return s


def _score_item(title: str, summary: str = "") -> int:
    blob = f"{title} {summary}"
    if not _TECH_RE.search(blob):
        return 0
    score = 2
    if _CATALYST_RE.search(blob):
        score += 3
    if re.search(r"纳斯达克|纳指|费城|美股科技|Nasdaq|SOX|NDX", blob, re.I):
        score += 2
    if re.search(r"英伟达|黄仁勋|Nvidia|Huang", blob, re.I):
        score += 2
    if re.search(r"[+\-]?\d+(\.\d+)?\s*%", blob):
        score += 1
    return score


def _fetch_em_items(*, limit: int = 12) -> Tuple[List[Dict[str, Any]], str]:
    from core.data.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "stock_info_global_em", None)
    if not fn:
        return [], "em_missing"
    df = fn()
    if df is None or getattr(df, "empty", True):
        return [], "em_empty"
    items: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        title = _clean_text(row.get("标题"), limit=100)
        summary = _clean_text(row.get("摘要") or row.get("内容"), limit=180)
        score = _score_item(title, summary)
        if score <= 0:
            continue
        items.append(
            {
                "title": title,
                "summary": summary,
                "time": str(row.get("发布时间") or "")[:19],
                "url": str(row.get("链接") or "")[:200] or None,
                "source": "em_global",
                "score": score,
            }
        )
    items.sort(key=lambda x: (-int(x.get("score") or 0), str(x.get("time") or "")), reverse=False)
    return items[:limit], "stock_info_global_em"


def _fetch_cls_items(*, limit: int = 6) -> Tuple[List[Dict[str, Any]], str]:
    from core.data.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "stock_info_global_cls", None)
    if not fn:
        return [], "cls_missing"
    df = fn()
    if df is None or getattr(df, "empty", True):
        return [], "cls_empty"
    items: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        title = _clean_text(row.get("标题"), limit=100)
        content = _clean_text(row.get("内容"), limit=180)
        # CLS 常把正文当标题
        head = title or content[:60]
        score = _score_item(head, content)
        if score <= 0:
            continue
        tm = f"{str(row.get('发布日期') or '')[:10]} {str(row.get('发布时间') or '')}".strip()
        items.append(
            {
                "title": head,
                "summary": content,
                "time": tm[:19],
                "url": None,
                "source": "cls_global",
                "score": score,
            }
        )
    items.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
    return items[:limit], "stock_info_global_cls"


def _fetch_cnbc_items(*, limit: int = 6) -> Tuple[List[Dict[str, Any]], str]:
    req = urllib.request.Request(_CNBC_TECH_RSS, headers=_UA)
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read(120_000)
    root = ET.fromstring(raw)
    items: List[Dict[str, Any]] = []
    for node in root.findall("./channel/item"):
        title = _clean_text((node.findtext("title") or ""), limit=120)
        desc = _clean_text((node.findtext("description") or ""), limit=180)
        link = (node.findtext("link") or "").strip() or None
        pub = (node.findtext("pubDate") or "").strip()[:32]
        score = _score_item(title, desc)
        if score <= 0:
            # CNBC tech 频道本身已偏科技，弱相关也保留低分
            if not title:
                continue
            score = 1
        items.append(
            {
                "title": title,
                "summary": desc,
                "time": pub,
                "url": link[:200] if link else None,
                "source": "cnbc_tech_rss",
                "score": score,
            }
        )
        if len(items) >= limit:
            break
    items.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
    return items, "cnbc_tech_rss"


def _dedupe_items(items: List[Dict[str, Any]], *, limit: int = 6) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        key = re.sub(r"\W+", "", str(it.get("title") or "").lower())[:40]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({k: v for k, v in it.items() if k != "score"})
        if len(out) >= limit:
            break
    return out


def _infer_tone(items: List[Dict[str, Any]], tech_1d_pct: Optional[float]) -> str:
    if tech_1d_pct is not None:
        try:
            v = float(tech_1d_pct)
            if v >= 1.5:
                return "up"
            if v <= -1.5:
                return "down"
        except (TypeError, ValueError):
            pass
    blob = " ".join(f"{i.get('title') or ''} {i.get('summary') or ''}" for i in items[:3])
    up = len(re.findall(r"大涨|飙升|暴涨|走高|surge|soar|rally|jump", blob, re.I))
    down = len(re.findall(r"大跌|暴跌|走低|跳水|plunge|drop|selloff", blob, re.I))
    if up > down:
        return "up"
    if down > up:
        return "down"
    return "flat"


def _pick_reason_sentence(text: str) -> str:
    """从摘要里抽出最像隔夜涨因的一句（含纳指/英伟达/% 优先）。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    # 去掉【标题】前缀噪音
    raw = re.sub(r"^【[^】]{0,40}】", "", raw).strip()
    parts = re.split(r"(?<=[。！？；;])\s*", raw)
    parts = [p.strip() for p in parts if p and p.strip()]
    if not parts:
        return _clean_text(raw, limit=120)
    best = ""
    best_score = -1
    for p in parts:
        sc = 0
        if re.search(r"隔夜|美股|纳指|纳斯达克|费城|英伟达|Nvidia|Nasdaq", p, re.I):
            sc += 3
        if re.search(r"\d+(\.\d+)?\s*%", p):
            sc += 2
        if _CATALYST_RE.search(p):
            sc += 2
        if sc > best_score:
            best_score = sc
            best = p
    if best_score <= 0:
        best = parts[0]
    # 截掉「点燃 A 股…」一类尾随映射，只留隔夜事实
    cut = re.search(r"([，,][^，,]*(?:A股|沪深|国内|内资)[^。]*)", best)
    if cut and cut.start() > 12:
        best = best[: cut.start()].rstrip("，,") + "。"
    best = re.sub(r"^(可以看到|据悉|报道称)[，,]\s*", "", best)
    return _clean_text(best, limit=120)


def _build_reason(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    top = items[0]
    title = str(top.get("title") or "").strip()
    summary = str(top.get("summary") or "").strip()
    from_summary = _pick_reason_sentence(summary)
    if from_summary and (
        re.search(r"隔夜|纳指|纳斯达克|费城|英伟达|美股|\d+(\.\d+)?\s*%", from_summary, re.I)
        or _CATALYST_RE.search(from_summary)
    ):
        return from_summary
    if title and (_CATALYST_RE.search(title) or re.search(r"\d+(\.\d+)?\s*%", title)):
        return _clean_text(title, limit=100)
    return from_summary or _clean_text(title or summary, limit=100)


def build_tech_catalyst_snapshot(
    *,
    tech_1d_pct: Optional[float] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """拼海外科技隔夜涨因；失败时 success=False，不抛。"""
    errors: List[str] = []
    sources: List[str] = []
    pooled: List[Dict[str, Any]] = []

    em = _call_with_timeout(_fetch_em_items, limit=12, timeout=16)
    if isinstance(em, tuple):
        rows, src = em
        if rows:
            pooled.extend(rows)
            sources.append(src)
        else:
            errors.append(f"em:{src or 'empty'}")
    else:
        errors.append("em:timeout_or_fail")

    if len(pooled) < 2:
        cls = _call_with_timeout(_fetch_cls_items, limit=8, timeout=12)
        if isinstance(cls, tuple):
            rows, src = cls
            if rows:
                pooled.extend(rows)
                sources.append(src)
            else:
                errors.append(f"cls:{src or 'empty'}")
        else:
            errors.append("cls:timeout_or_fail")

    if len(pooled) < 1:
        cnbc = _call_with_timeout(_fetch_cnbc_items, limit=8, timeout=12)
        if isinstance(cnbc, tuple):
            rows, src = cnbc
            if rows:
                pooled.extend(rows)
                sources.append(src)
            else:
                errors.append("cnbc:empty")
        else:
            errors.append("cnbc:timeout_or_fail")
    elif not any(i.get("source") == "cnbc_tech_rss" for i in pooled):
        # 有中文主源时仍可选补一条英文（失败忽略）
        cnbc = _call_with_timeout(_fetch_cnbc_items, limit=3, timeout=10)
        if isinstance(cnbc, tuple) and cnbc[0]:
            pooled.extend(cnbc[0][:2])
            sources.append(cnbc[1])

    items = _dedupe_items(pooled, limit=max(1, int(limit or 5)))
    reason = _build_reason(items)
    tone = _infer_tone(items, tech_1d_pct)
    return {
        "success": bool(items and reason),
        "as_of": _today(),
        "reason": reason,
        "tone": tone,
        "items": items,
        "tech_1d_pct": tech_1d_pct,
        "sources": sources,
        "errors": errors,
        "note": "海外科技隔夜涨因·东财全球快讯优先，CNBC Tech RSS 兜底；非 PIT，不改 ŷ。",
    }
