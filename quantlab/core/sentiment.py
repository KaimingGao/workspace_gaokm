"""观察名单舆情：标题缓存 · 规则情绪分 · 扫描告警 · as_of 历史（FS）。

Live 评分默认 ``sentiment.include_in_score=false``（不进 ŷ）；UI 徽章仍可用。
研究可用 ``sentiment_as_of`` 做 PIT 面板。LLM 分析不得写入 sub_scores。
"""


import logging

logger = logging.getLogger(__name__)
import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Set

from core.io_atomic import atomic_write_json
from core.paths import (
    LLM_SENTIMENT_DIR,
    NEWS_HISTORY_DIR,
    NEWS_STORE_DIR,
    SCHEDULE_LAST_RUN_PATH,
    SENTIMENT_LEXICON_PATH,
    WATCHING_PATH,
)

DEFAULT_TTL_SEC = 15 * 60
MAX_BATCH = 20
ALERT_SCORE_THRESHOLD = 0.6

# 业绩方向：同一标题内「指标 + 方向」，不把「减少」单独放进负向词。
# 指标按长短排列，短词（净利 / 利润）不得再命中长词内部。
_EARNINGS_METRICS: tuple = (
    "扣非归母净利润",
    "归属于上市公司股东的净利润",
    "扣非净利润",
    "归母净利润",
    "扣非利润",
    "扣非盈利",
    "归母净利",
    "扣非净利",
    "利润总额",
    "营业收入",
    "每股收益",
    "净利润",
    "净利",
    "利润",
    "营收",
)
_EARNINGS_POS_DIRS: tuple = (
    "暴增",
    "猛增",
    "大增",
    "翻倍",
    "倍增",
    "预增",
    "增长",
    "增加",
    "上升",
)
_EARNINGS_NEG_DIRS: tuple = ("腰斩", "大降", "预减", "减少", "下降", "下滑", "降低")
# 亏损收窄是正向；词典里的「亏损 / 预亏」若只出现在这些短语里，从负向拿掉。
_LOSS_NARROW_PHRASES: tuple = (
    "亏损同比减少",
    "亏损同比收窄",
    "亏损同比下降",
    "亏损同比降低",
    "预亏同比减少",
    "预亏同比收窄",
    "亏损减少",
    "亏损收窄",
    "亏损下降",
    "亏损降低",
    "预亏减少",
    "预亏收窄",
    "预亏下降",
    "减亏",
)
_LEXICON_NEG_SUPPRESS = frozenset({"亏损", "预亏"})
_LOSS_NARROW_GAP_RE = re.compile(r"(?:净)?亏损.{0,16}(?:收窄|减亏)")
_EARNINGS_FILLER_RE = re.compile(r"^(?:同比|环比|大幅|小幅|明显|显著|均){0,3}")
_EARNINGS_GAP_RE = re.compile(
    r"^(?:为|约|达|至|实现)?"
    r"[\d.，,、%％万元亿人民币\s]{1,40}"
    r"(?:同比|环比|较去年同期|较上年同期|较上年|较同期)"
    r"(?:同比|环比|大幅|小幅|明显|显著|均)?"
)
_SHORT_GROWTH_RE = re.compile(r"增(?!亏)(?:幅)?(?:约)?\d")
_SHORT_DROP_RE = re.compile(r"降(?!低)(?:\d|[一二三四五六七八九两]成)")
# 「突破半年线 / 只个股」是技术扫描，不是公司经营突破。
_TECH_BREAK_MARKERS: tuple = (
    "只个股",
    "均线",
    "半年线",
    "年线",
    "五日线",
    "月线",
    "盘中突破",
)
_NEGATION_MARKERS: tuple = ("不存在", "未涉及", "不涉及", "没有", "未发生", "不构成")
_NEGATION_GUARDED = frozenset(
    {"诉讼", "违规", "处罚", "减持", "立案", "亏损", "预亏", "问询", "警示"}
)
# 业绩事件与持仓披露。不把裸「下降」算利空，避免「毛利率下降」以外的无关下降被扫进去。
_BULLISH_EVENT_PHRASES: tuple = ("历史新高", "再创新高")
_STAKE_RE = re.compile(
    r"(好仓|淡仓)(?:比例)?(?:同比|环比|大幅|小幅)?(下降|减少|降低|上升|增加|增长)"
)
_STAKE_POS = {
    ("好仓", "上升"): "好仓上升",
    ("好仓", "增加"): "好仓上升",
    ("好仓", "增长"): "好仓上升",
    ("淡仓", "下降"): "淡仓下降",
    ("淡仓", "减少"): "淡仓下降",
    ("淡仓", "降低"): "淡仓下降",
}
_STAKE_NEG = {
    ("好仓", "下降"): "好仓下降",
    ("好仓", "减少"): "好仓下降",
    ("好仓", "降低"): "好仓下降",
    ("淡仓", "上升"): "淡仓上升",
    ("淡仓", "增加"): "淡仓上升",
    ("淡仓", "增长"): "淡仓上升",
}

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
        "停牌核查",
        "严重异常波动",
        "异常波动",
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
            with open(p, encoding="utf-8") as f:
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        out["include_in_score"] = False
        out["role"] = "prior"
    return out


def _match_earnings_dir(text: str) -> Optional[str]:
    """方向词，「增/降 + 数字」（同比增177%、同比降98%）。「增亏」不是增长。"""
    for word in _EARNINGS_POS_DIRS + _EARNINGS_NEG_DIRS:
        if text.startswith(word):
            return word
    if _SHORT_GROWTH_RE.match(text):
        return "增长"
    if _SHORT_DROP_RE.match(text):
        return "下降"
    return None


def _direction_after_metric(tail: str) -> Optional[str]:
    """指标之后：紧挨的方向，或金额后再接同比/环比。不跨到另一分句的「减少」。"""
    filler = _EARNINGS_FILLER_RE.match(tail)
    offset = filler.end() if filler else 0
    direction = _match_earnings_dir(tail[offset:])
    if direction:
        return direction
    gap = _EARNINGS_GAP_RE.match(tail)
    if not gap:
        return None
    return _match_earnings_dir(tail[gap.end() :])


def _loss_narrow_span(title: str) -> bool:
    """亏损与收窄/减亏之间可以隔金额。「扩大」不算收窄。"""
    for match in _LOSS_NARROW_GAP_RE.finditer(title):
        if "扩大" not in match.group(0):
            return True
    return any(phrase in title for phrase in _LOSS_NARROW_PHRASES)


def _loss_narrow_residue(title: str) -> str:
    residue = title
    for phrase in _LOSS_NARROW_PHRASES:
        residue = residue.replace(phrase, "")

    def _drop_gap(match: re.Match) -> str:
        if "扩大" in match.group(0):
            return match.group(0)
        return ""

    return _LOSS_NARROW_GAP_RE.sub(_drop_gap, residue)


def _lexicon_hit(title: str, word: str) -> bool:
    """词典整词。技术扫描里的「突破」、板块/持仓「下跌」、否定句不算。"""
    if not word or word not in title:
        return False
    if word == "突破" and any(marker in title for marker in _TECH_BREAK_MARKERS):
        return False
    start = 0
    while True:
        idx = title.find(word, start)
        if idx < 0:
            return False
        pre = title[max(0, idx - 8) : idx]
        if word == "下跌" and (
            (pre.endswith("股") and not pre.endswith("股价"))
            or any(marker in pre for marker in ("概念", "持股", "价格", "指数", "板块"))
        ):
            start = idx + len(word)
            continue
        if word == "下滑" and title[max(0, idx - 2) : idx] == "需求":
            start = idx + len(word)
            continue
        if word in _NEGATION_GUARDED and any(
            marker in title[max(0, idx - 18) : idx] for marker in _NEGATION_MARKERS
        ):
            start = idx + len(word)
            continue
        return True


def _earnings_phrase_hits(title: str) -> tuple:
    """返回 (正向合成词, 负向合成词)。费用/成本/负债的增减不在这里。"""
    pos: List[str] = []
    neg: List[str] = []
    for phrase in _BULLISH_EVENT_PHRASES:
        if phrase in title:
            pos.append(phrase)
            break
    stake = _STAKE_RE.search(title)
    if stake:
        key = (stake.group(1), stake.group(2))
        if key in _STAKE_POS:
            pos.append(_STAKE_POS[key])
        elif key in _STAKE_NEG:
            neg.append(_STAKE_NEG[key])
    if "净资产为负" in title:
        neg.append("净资产为负")
    elif "资不抵债" in title:
        neg.append("资不抵债")
    if _loss_narrow_span(title):
        pos.append("亏损收窄")
    if "增亏" in title:
        neg.append("增亏")
    occupied: List[tuple] = []
    for metric in _EARNINGS_METRICS:
        start = 0
        while True:
            idx = title.find(metric, start)
            if idx < 0:
                break
            end = idx + len(metric)
            if any(s <= idx and end <= e for s, e in occupied):
                start = idx + 1
                continue
            nxt = title[end : end + 2]
            if metric == "净利" and title[end : end + 1] in ("率", "息"):
                start = end
                continue
            if metric == "利润" and (nxt in ("分配", "贡献") or title[end : end + 1] == "率"):
                start = end
                continue
            occupied.append((idx, end))
            direction = _direction_after_metric(title[end:])
            if direction in _EARNINGS_POS_DIRS:
                pos.append(f"{metric}{direction}")
            elif direction in _EARNINGS_NEG_DIRS:
                neg.append(f"{metric}{direction}")
            start = end
    return pos, neg


def score_headlines(
    items: Sequence[dict],
    *,
    lexicon: Optional[Dict[str, List[str]]] = None,
    degraded: bool = False,
    degrade_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """规则情绪：有命中时打标签；强度按「命中标题占比」稀释。

    - label：由唯一关键词极性（仅负 / 仅正 / 混合）
    - 关键词 = 词典整词 + 同一标题内的业绩方向（净利大增/翻倍、净利润为金额后再同比增长、同比降、亏损与收窄可隔金额）
    - 「突破均线 / 概念下跌 / 持股比例下跌 / 不存在诉讼」不记
    - score：``polar * coverage``，其中 polar=neg/(pos+neg)，
      coverage=命中标题数/总标题数。避免「5 条里 1 条分行违规」被打成 1.0
      虚高影响展示/标签。
    - 无命中 → neutral / score=null
    - degraded=True：超时/空源降级，徽章不标 active（prior_eligible=false）
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
        earn_pos, earn_neg = _earnings_phrase_hits(title)
        residue = _loss_narrow_residue(title)
        for w in pos_words:
            if _lexicon_hit(title, w):
                title_pos = True
                if w not in seen_pos:
                    seen_pos.add(w)
                    hit_pos.append(w)
        for w in neg_words:
            haystack = residue if w in _LEXICON_NEG_SUPPRESS else title
            if _lexicon_hit(haystack, w):
                title_neg = True
                if w not in seen_neg:
                    seen_neg.add(w)
                    hit_neg.append(w)
        for w in earn_pos:
            title_pos = True
            if w not in seen_pos:
                seen_pos.add(w)
                hit_pos.append(w)
        for w in earn_neg:
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
        with open(path, encoding="utf-8") as f:
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
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
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > max_lines:

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
            with open(path, encoding="utf-8") as f:
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
    return {
        "ok": n > 0,
        "code": str(code or "").strip(),
        "as_of": as_of_d,
        "sentiment": sent,
        "metrics": {"n_titles": n},
        "note": "PIT 标题 as_of；无日志则 missing。不进因子集。",
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
            logger.exception('unexpected error in warmup_sentiment_history')
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
        if items:
            # 标题缓存可复用；标签按当前规则重算，避免词典更新后 TTL 内仍是旧「中」。
            sent = _annotate_include_gate(_attach_sentiment(items, lexicon=lexicon))
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
        # 失败缓存仍按 TTL 复用，避免空源狂打网；ok=True 的空标题是毒缓存，须重拉
        if cached.get("ok") is False or cached.get("error"):
            sent = cached.get("sentiment")
            if not isinstance(sent, dict):
                sent = score_headlines([], lexicon=lexicon, degraded=True)
            sent = _annotate_include_gate(sent if isinstance(sent, dict) else {})
            return {
                "ok": False,
                "stock_code": cached.get("stock_code") or code_key,
                "stock_name": cached.get("stock_name") or "",
                "items": [],
                "sentiment": sent,
                "updated_at": cached.get("updated_at"),
                "from_cache": True,
                "error": cached.get("error") or "未获取到相关资讯",
            }

    from core.data.facade import get_news

    try:
        raw = get_news(code_key, limit=limit, use_cache=not force)
    except Exception as exc:
        logger.exception('unexpected error in fetch_stock_headlines')
        # 超时/网络异常：优先回退过期缓存，避免 UI 永久加载中
        reason = f"资讯源超时：{exc}"
        if cached and (cached.get("items") or []):
            items = list(cached.get("items") or [])[:limit]
            sent = _mark_sentiment_degraded(
                _attach_sentiment(items, lexicon=lexicon), reason=reason
            )
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
    live_items = list(raw.get("items") or [])[:limit]
    if not raw.get("success") or not live_items:
        err = str(raw.get("error") or "未获取到相关资讯")
        if cached and (cached.get("items") or []):
            items = list(cached.get("items") or [])[:limit]
            sent = _mark_sentiment_degraded(
                _attach_sentiment(items, lexicon=lexicon), reason=err
            )
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

    items = live_items
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
        with open(WATCHING_PATH, encoding="utf-8") as f:
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
    """批量观察舆情。先读本地新闻库，没有或过期再向东财要并写回。上限 MAX_BATCH。"""
    limit = max(1, min(int(limit or 3), 8))
    watch = _watchlist_codes(codes)
    from core.news_archive import serve_watch_headlines

    return serve_watch_headlines(
        watch,
        limit=limit,
        max_age_sec=ttl_sec,
        force=force,
        lexicon=load_lexicon(),
    )


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
        with open(SCHEDULE_LAST_RUN_PATH, encoding="utf-8") as f:
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
# 叙事对照。不进因子集，不得写入 sub_scores / ŷ。
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
        with open(path, encoding="utf-8") as f:
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
        logger.exception('unexpected error in score_headlines_llm')
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        pass

    return _annotate_include_gate(payload)
