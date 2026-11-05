"""本地新闻舆情库（data/store/news.db）。

只向前追加标题，供调仓舆情软先验和回测按决策日重放。不进 ŷ，不回补东财当前页。
一条标题进入的决策日，是发布时间之后、且本地当时已经拿到它的第一根 fill_clock。
勾选闸时：空/多调入场 fuse±0.1% 与名次 ×2/×0.5；明细写原始值，不因舆情清仓。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# 历史明细 reason 前缀（硬闸已退役）；desk/sim 仍认「舆情闸*」以便回放旧行。
SENTIMENT_GATE_REASON = "舆情闸 空"
# 软先验：空/多调 fuse 与名次；不清仓、不硬禁买。
SENTIMENT_FUSE_DELTA = 0.001  # ±0.1%
SENTIMENT_RANK_MULT_BEAR = 2.0
SENTIMENT_RANK_MULT_BULL = 0.5
_FILL_CLOCK = "09:30"
_SOURCE = "eastmoney"
POLL_SLOTS_TRADING = ("09:20", "11:30", "15:10", "20:30")
POLL_SLOTS_CLOSED = ("20:30",)
_SAMPLE_LIMIT = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS headline (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  title TEXT NOT NULL,
  title_hash TEXT NOT NULL,
  published_at TEXT,
  published_date TEXT NOT NULL,
  clock_quality TEXT NOT NULL,
  source TEXT,
  url TEXT,
  fetched_at TEXT NOT NULL,
  UNIQUE(code, title_hash)
);
CREATE INDEX IF NOT EXISTS idx_headline_code_fetched
  ON headline(code, fetched_at);
CREATE INDEX IF NOT EXISTS idx_headline_code_pub
  ON headline(code, published_date);

CREATE TABLE IF NOT EXISTS fetch_log (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  ok INTEGER NOT NULL,
  n_rows INTEGER NOT NULL,
  n_inserted INTEGER NOT NULL,
  error TEXT,
  degraded INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetch_code_time
  ON fetch_log(code, fetched_at);

CREATE TABLE IF NOT EXISTS decision_label (
  code TEXT NOT NULL,
  decision_date TEXT NOT NULL,
  fill_clock TEXT NOT NULL,
  lexicon_version TEXT NOT NULL,
  label TEXT NOT NULL,
  n_titles INTEGER NOT NULL,
  n_hit INTEGER NOT NULL,
  degraded INTEGER NOT NULL,
  coverage_status TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (code, decision_date, fill_clock, lexicon_version)
);

CREATE TABLE IF NOT EXISTS poll_slot (
  slot_key TEXT PRIMARY KEY,
  ran_at TEXT NOT NULL,
  n_codes INTEGER NOT NULL,
  n_ok INTEGER NOT NULL,
  n_fail INTEGER NOT NULL,
  n_skipped INTEGER NOT NULL DEFAULT 0,
  stop_reason TEXT NOT NULL DEFAULT ''
);
"""

_write_lock = threading.Lock()
_DATE_RE = re.compile(r"^(\d{4})[-/](\d{2})[-/](\d{2})")
_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
)


def archive_store_dir(store_dir: Optional[str] = None) -> str:
    if store_dir:
        return store_dir
    override = os.environ.get("QUANTLAB_NEWS_ARCHIVE_DIR", "").strip()
    if override:
        return override
    from core.paths import STORE_DIR

    return STORE_DIR


def news_db_path(store_dir: Optional[str] = None) -> str:
    return os.path.join(archive_store_dir(store_dir), "news.db")


def bare_a_share(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if not s:
        return ""
    for prefix in ("SH", "SZ", "BJ"):
        if s.startswith(prefix) and len(s) > 2:
            s = s[2:]
            break
    s = s.split(".")[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6)


def title_hash(title: str) -> str:
    norm = re.sub(r"\s+", "", str(title or "").strip())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def parse_publish_clock(raw: Any) -> tuple:
    """返回 (published_at, published_date, clock_quality)。无法识别则全空。"""
    s = str(raw or "").strip()
    if not s:
        return "", "", ""
    for fmt in _TS_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        day = dt.strftime("%Y-%m-%d")
        clock = "timestamp" if "%H" in fmt else "date_only"
        if clock == "date_only":
            return "", day, "date_only"
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), day, "timestamp"
    m = _DATE_RE.match(s)
    if m and len(s) <= 10:
        day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return "", "", ""
        return "", day, "date_only"
    return "", "", ""


def _parse_hm(fill_clock: str) -> tuple:
    text = str(fill_clock or _FILL_CLOCK).strip()
    parts = text.split(":")
    try:
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return 9, 30
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return 9, 30
    return hh, mm


def _fill_dt(day: str, fill_clock: str) -> Optional[datetime]:
    try:
        base = datetime.strptime(str(day)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    hh, mm = _parse_hm(fill_clock)
    return base.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _parse_stored_ts(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    published_at, day, quality = parse_publish_clock(s.replace("T", " ") if "T" in s else s)
    if quality == "timestamp" and published_at:
        try:
            return datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if day:
        try:
            return datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return None
    return None


def first_session_at_or_after(
    ts: datetime,
    fill_clock: str = _FILL_CLOCK,
    holidays: Optional[Set[str]] = None,
) -> str:
    """第一个 fill_clock 不早于 ts 的交易日。"""
    from core.market.calendar import is_trading_day, next_trading_day

    day = ts.strftime("%Y-%m-%d")
    if is_trading_day(day, holidays=holidays):
        fill = _fill_dt(day, fill_clock)
        if fill is not None and ts <= fill:
            return day
    return next_trading_day(day, holidays=holidays) or ""


def assign_decision_day(
    *,
    published_at: str = "",
    published_date: str = "",
    clock_quality: str = "",
    fetched_at: str = "",
    fill_clock: str = _FILL_CLOCK,
    holidays: Optional[Set[str]] = None,
    respect_fetch: bool = True,
) -> str:
    """标题进入的决策日。不可用返回空串。

    ``respect_fetch`` 为真时，抓取晚于该发布时点的开盘，标题改记到抓取之后的下一档。
    历史回测传假，只按发布时间落入第一个不早于它的 fill。
    """
    quality = str(clock_quality or "").strip()
    fetched = _parse_stored_ts(fetched_at)
    if fetched is None or quality not in ("timestamp", "date_only"):
        return ""
    if quality == "timestamp":
        published = _parse_stored_ts(published_at)
        if published is None or published > fetched:
            return ""
        slot = first_session_at_or_after(published, fill_clock, holidays)
    else:
        day = str(published_date or "")[:10]
        if not day:
            return ""
        from core.market.calendar import next_trading_day

        slot = next_trading_day(day, holidays=holidays) or ""
    if not slot:
        return ""
    slot_fill = _fill_dt(slot, fill_clock)
    if respect_fetch and slot_fill is not None and fetched > slot_fill:
        slot = first_session_at_or_after(fetched, fill_clock, holidays)
    return slot or ""


def _quarantine_corrupt_db(path: str) -> None:
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    dest = f"{path}.corrupt.{ts}"
    try:
        os.replace(path, dest)
        logger.warning("quarantined corrupt news.db -> %s", dest)
    except OSError:
        logger.exception("failed to quarantine corrupt news.db at %s", path)
    for suffix in ("-wal", "-shm"):
        side = path + suffix
        if os.path.isfile(side):
            try:
                os.replace(side, f"{side}.corrupt.{ts}")
            except OSError:
                logger.debug("failed to quarantine %s", side, exc_info=True)


def _connect(store_dir: Optional[str], *, create: bool) -> Optional[sqlite3.Connection]:
    path = news_db_path(store_dir)
    if not create and not os.path.isfile(path):
        return None
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    def _open() -> sqlite3.Connection:
        conn = sqlite3.connect(path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        _migrate_poll_slot(conn)
        return conn

    try:
        return _open()
    except sqlite3.DatabaseError:
        logger.exception("news.db open failed at %s", path)
        if not create:
            return None
        _quarantine_corrupt_db(path)
        try:
            return _open()
        except sqlite3.DatabaseError:
            logger.exception("news.db recreate failed at %s", path)
            return None


def _migrate_poll_slot(conn: sqlite3.Connection) -> None:
    cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(poll_slot)")}
    if "n_skipped" not in cols:
        conn.execute(
            "ALTER TABLE poll_slot ADD COLUMN n_skipped INTEGER NOT NULL DEFAULT 0"
        )
    if "stop_reason" not in cols:
        conn.execute(
            "ALTER TABLE poll_slot ADD COLUMN stop_reason TEXT NOT NULL DEFAULT ''"
        )


def _window_bounds(
    decision_date: str,
    fill_clock: str,
    holidays: Optional[Set[str]],
) -> Optional[tuple]:
    from core.market.calendar import prev_trading_day

    end = _fill_dt(decision_date, fill_clock)
    if end is None:
        return None
    prev = prev_trading_day(decision_date, holidays=holidays) or ""
    if prev:
        start = _fill_dt(prev, fill_clock)
        if start is None:
            return None
    else:
        start = datetime(1970, 1, 1)
    return (
        start.strftime("%Y-%m-%dT%H:%M:%S"),
        end.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def upsert_headline(
    conn: sqlite3.Connection,
    *,
    code: str,
    title: str,
    published_raw: str,
    fetched_at: str,
    source: str = _SOURCE,
    url: str = "",
) -> str:
    """插入或保持原发布时间。返回 inserted / upgraded / kept / rejected。"""
    bare = bare_a_share(code)
    text = str(title or "").strip()
    published_at, published_date, quality = parse_publish_clock(published_raw)
    fetched = str(fetched_at or "").strip()
    if not bare or not text or not published_date or quality not in ("timestamp", "date_only"):
        return "rejected"
    if quality == "timestamp":
        if not published_at or published_at > fetched:
            return "rejected"
    th = title_hash(text)
    old = conn.execute(
        """
        SELECT published_at, published_date, clock_quality, fetched_at
        FROM headline WHERE code = ? AND title_hash = ?
        """,
        (bare, th),
    ).fetchone()
    if old is None:
        conn.execute(
            """
            INSERT INTO headline (
              code, title, title_hash, published_at, published_date,
              clock_quality, source, url, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bare,
                text[:500],
                th,
                published_at or None,
                published_date,
                quality,
                str(source or "")[:80],
                str(url or "")[:500],
                fetched,
            ),
        )
        return "inserted"
    new_at = published_at or ""
    old_at = str(old["published_at"] or "")
    first_fetch = str(old["fetched_at"] or "")
    better = (
        quality == "timestamp"
        and bool(new_at)
        and new_at <= first_fetch
        and (not old_at or new_at < old_at or str(old["clock_quality"]) == "date_only")
    )
    if not better:
        return "kept"
    conn.execute(
        """
        UPDATE headline
        SET published_at = ?, published_date = ?, clock_quality = ?
        WHERE code = ? AND title_hash = ?
        """,
        (new_at, published_date, "timestamp", bare, th),
    )
    return "upgraded"


def record_fetch(
    conn: sqlite3.Connection,
    *,
    code: str,
    fetched_at: str,
    ok: bool,
    n_rows: int,
    n_inserted: int,
    error: str = "",
    degraded: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log (
          code, fetched_at, ok, n_rows, n_inserted, error, degraded
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bare_a_share(code),
            fetched_at,
            1 if ok else 0,
            int(n_rows),
            int(n_inserted),
            str(error or "")[:200],
            1 if degraded else 0,
        ),
    )


def ingest_title_rows(
    code: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    fetched_at: str,
    source: str = _SOURCE,
    store_dir: Optional[str] = None,
    ok: bool = True,
    error: str = "",
    degraded: bool = False,
) -> Dict[str, Any]:
    """把一次拉取的标题写入库，并记 fetch_log。

    空成功仍记 ok（决策窗覆盖为 empty），但展示 TTL 要求非空标题，不会挡重拉。
    """
    fetched = str(fetched_at or "").strip() or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    items = [r for r in (rows or []) if isinstance(r, Mapping)]
    n_inserted = 0
    n_kept = 0
    n_rejected = 0
    empty_payload = bool(ok) and not items
    err_eff = str(error or "") or ("empty_payload" if empty_payload else "")
    with _write_lock:
        conn = _connect(store_dir, create=True)
        if conn is None:
            return {"ok": False, "error": "无法打开 news.db"}
        try:
            for row in items:
                title = row.get("title") or row.get("新闻标题") or row.get("标题")
                published = (
                    row.get("time")
                    or row.get("发布时间")
                    or row.get("时间")
                    or row.get("date")
                    or ""
                )
                url = str(row.get("url") or row.get("新闻链接") or "")
                src = str(row.get("source") or row.get("文章来源") or source)
                status = upsert_headline(
                    conn,
                    code=code,
                    title=str(title or ""),
                    published_raw=str(published or ""),
                    fetched_at=fetched,
                    source=src,
                    url=url,
                )
                if status == "inserted":
                    n_inserted += 1
                elif status == "rejected":
                    n_rejected += 1
                else:
                    n_kept += 1
            record_fetch(
                conn,
                code=code,
                fetched_at=fetched,
                ok=bool(ok) and not degraded,
                n_rows=len(items),
                n_inserted=n_inserted,
                error=err_eff,
                degraded=bool(degraded) or not ok,
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "ok": True,
        "code": bare_a_share(code),
        "n_inserted": n_inserted,
        "n_kept": n_kept,
        "n_rejected": n_rejected,
        "fetched_at": fetched,
        "empty_payload": empty_payload,
    }


def _lexicon_version(lexicon: Optional[Mapping[str, Any]] = None) -> str:
    if lexicon is not None:
        blob = json.dumps(dict(lexicon), ensure_ascii=False, sort_keys=True).encode("utf-8")
    else:
        from core.paths import SENTIMENT_LEXICON_PATH

        try:
            with open(SENTIMENT_LEXICON_PATH, "rb") as f:
                blob = f.read()
        except OSError:
            blob = b""
    return hashlib.sha256(blob).hexdigest()[:16]


def _coverage_for_code(
    conn: sqlite3.Connection,
    code: str,
    start: str,
    end: str,
) -> str:
    rows = conn.execute(
        """
        SELECT ok, degraded FROM fetch_log
        WHERE code = ? AND fetched_at > ? AND fetched_at <= ?
        """,
        (code, start, end),
    ).fetchall()
    if not rows:
        return "gap"
    if any(int(r["ok"] or 0) == 1 and int(r["degraded"] or 0) == 0 for r in rows):
        return "ok"
    return "degraded"


def _titles_for_decision(
    conn: sqlite3.Connection,
    code: str,
    decision_date: str,
    fill_clock: str,
    start: str,
    end: str,
    holidays: Optional[Set[str]],
) -> List[dict]:
    rows = conn.execute(
        """
        SELECT title, published_at, published_date, clock_quality, fetched_at
        FROM headline
        WHERE code = ? AND fetched_at > ? AND fetched_at <= ?
        """,
        (code, start, end),
    ).fetchall()
    out: List[dict] = []
    for r in rows:
        day = assign_decision_day(
            published_at=str(r["published_at"] or ""),
            published_date=str(r["published_date"] or ""),
            clock_quality=str(r["clock_quality"] or ""),
            fetched_at=str(r["fetched_at"] or ""),
            fill_clock=fill_clock,
            holidays=holidays,
        )
        if day == decision_date:
            out.append({"title": str(r["title"] or "")})
    return out


def _titles_by_publish(
    conn: sqlite3.Connection,
    codes: Sequence[str],
    decision_date: str,
    fill_clock: str,
    start: str,
    end: str,
    holidays: Optional[Set[str]],
) -> Dict[str, List[dict]]:
    """发布时间落在 (上一档 fill, 当日 fill] 的标题。不看抓取时间。"""
    wanted = {str(code) for code in codes}
    grouped: Dict[str, List[dict]] = {code: [] for code in wanted}
    rows = conn.execute(
        """
        SELECT code, title, published_at, published_date, clock_quality, fetched_at
        FROM headline
        WHERE clock_quality = 'timestamp'
          AND published_at > ? AND published_at <= ?
        """,
        (start, end),
    ).fetchall()
    for row in rows:
        code = str(row["code"] or "")
        if code not in wanted:
            continue
        title = str(row["title"] or "").strip()
        if title:
            grouped[code].append({"title": title})
    if wanted:
        marks = ",".join("?" for _ in wanted)
        date_only = conn.execute(
            f"""
            SELECT code, title, published_at, published_date, clock_quality, fetched_at
            FROM headline
            WHERE clock_quality = 'date_only'
              AND code IN ({marks})
            """,
            tuple(wanted),
        ).fetchall()
    else:
        date_only = []
    for row in date_only:
        code = str(row["code"] or "")
        if code not in wanted:
            continue
        slot = assign_decision_day(
            published_at=str(row["published_at"] or ""),
            published_date=str(row["published_date"] or ""),
            clock_quality="date_only",
            fetched_at=str(row["fetched_at"] or ""),
            fill_clock=fill_clock,
            holidays=holidays,
            respect_fetch=False,
        )
        if slot != decision_date:
            continue
        title = str(row["title"] or "").strip()
        if title:
            grouped[code].append({"title": title})
    return grouped


def load_decision_labels(
    codes: Sequence[str],
    decision_date: str,
    fill_clock: str = _FILL_CLOCK,
    *,
    store_dir: Optional[str] = None,
    lexicon: Optional[Mapping[str, Any]] = None,
    holidays: Optional[Set[str]] = None,
    align: str = "fetch",
) -> Dict[str, dict]:
    """按决策日重算闸输入。库不存在时全部缺测，不创建文件。

    ``align="fetch"``：标题必须在该 fill 前已经入库，抓取窗外是缺测。
    ``align="publish"``：只看发布时间是否落入该 fill。没有标题仍是缺测，不当成中性。
    发布时点结果不写入 decision_label，避免盖掉按抓取时点算的当日闸。
    """
    from core.market.calendar import _load_holidays
    from core.sentiment import load_lexicon, score_headlines

    day = str(decision_date or "")[:10]
    clock = str(fill_clock or _FILL_CLOCK)[:5]
    bare_codes = []
    for raw in codes or []:
        code = bare_a_share(raw)
        if code and code not in bare_codes:
            bare_codes.append(code)
    if not day or not bare_codes:
        return {}
    hol = holidays if holidays is not None else _load_holidays(archive_store_dir(store_dir))
    version = _lexicon_version(lexicon)
    lex = dict(lexicon) if lexicon is not None else load_lexicon()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    bounds = _window_bounds(day, clock, hol)
    out: Dict[str, dict] = {}
    conn = _connect(store_dir, create=False)
    if conn is None or bounds is None:
        for code in bare_codes:
            out[code] = _label_row(
                code=code,
                decision_date=day,
                fill_clock=clock,
                lexicon_version=version,
                label="missing",
                n_titles=0,
                n_hit=0,
                degraded=0,
                coverage_status="gap",
                computed_at=now,
            )
        if conn is not None:
            conn.close()
        return out
    start, end = bounds
    by_publish = str(align or "").strip() == "publish"
    pending: List[dict] = []
    publish_titles: Dict[str, List[dict]] = {}
    try:
        if by_publish:
            publish_titles = _titles_by_publish(conn, bare_codes, day, clock, start, end, hol)
        for code in bare_codes:
            if by_publish:
                coverage = "ok"
                titles = list(publish_titles.get(code) or [])
                degraded = 0
            else:
                coverage = _coverage_for_code(conn, code, start, end)
                titles = _titles_for_decision(conn, code, day, clock, start, end, hol)
                degraded = 1 if coverage == "degraded" else 0
            if coverage == "gap":
                label, n_hit, status = "missing", 0, "gap"
                n_titles = 0
            elif coverage == "degraded":
                label, n_hit, status = "missing", 0, "degraded"
                n_titles = 0
            elif not titles:
                if by_publish:
                    label, n_hit, status = "missing", 0, "gap"
                else:
                    label, n_hit, status = "neutral", 0, "empty"
                n_titles = 0
            else:
                sent = score_headlines(titles, lexicon=lex)
                label = str(sent.get("label") or "neutral")
                n_titles = int(sent.get("n_titles") or len(titles))
                n_hit = int(sent.get("n_hit_titles") or 0)
                status = "ok"
                if sent.get("degraded"):
                    degraded = 1
                    status = "degraded"
                    label = "missing"
            row = _label_row(
                code=code,
                decision_date=day,
                fill_clock=clock,
                lexicon_version=version,
                label=label,
                n_titles=n_titles,
                n_hit=n_hit,
                degraded=degraded,
                coverage_status=status,
                computed_at=now,
            )
            out[code] = row
            if not by_publish:
                pending.append(row)
        if pending:
            with _write_lock:
                for row in pending:
                    conn.execute(
                        """
                        INSERT INTO decision_label (
                          code, decision_date, fill_clock, lexicon_version, label,
                          n_titles, n_hit, degraded, coverage_status, computed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(code, decision_date, fill_clock, lexicon_version)
                        DO UPDATE SET
                          label = excluded.label,
                          n_titles = excluded.n_titles,
                          n_hit = excluded.n_hit,
                          degraded = excluded.degraded,
                          coverage_status = excluded.coverage_status,
                          computed_at = excluded.computed_at
                        """,
                        (
                            row["code"],
                            row["decision_date"],
                            row["fill_clock"],
                            row["lexicon_version"],
                            row["label"],
                            row["n_titles"],
                            row["n_hit"],
                            row["degraded"],
                            row["coverage_status"],
                            row["computed_at"],
                        ),
                    )
                conn.commit()
    finally:
        conn.close()
    return out


def _label_row(**kwargs: Any) -> dict:
    return dict(kwargs)


def load_library_labels(
    codes: Sequence[str],
    *,
    store_dir: Optional[str] = None,
    lexicon: Optional[Mapping[str, Any]] = None,
    limit: int = 10,
) -> Dict[str, dict]:
    """库内已有标题的当前页标签。不访问东财。没有标题是 missing。"""
    from core.sentiment import load_lexicon, score_headlines

    bare: List[str] = []
    for raw in codes or []:
        code = bare_a_share(raw)
        if code and code not in bare:
            bare.append(code)
    if not bare:
        return {}
    cap = max(1, min(int(limit or 10), 20))
    lex = dict(lexicon) if lexicon is not None else load_lexicon()
    grouped: Dict[str, List[dict]] = {code: [] for code in bare}
    conn = _connect(store_dir, create=False)
    if conn is not None:
        try:
            marks = ",".join("?" for _ in bare)
            rows = conn.execute(
                f"""
                SELECT code, title, published_at
                FROM headline
                WHERE code IN ({marks})
                ORDER BY code, published_at DESC, title
                """,
                bare,
            ).fetchall()
            for row in rows:
                code = str(row["code"] or "")
                bucket = grouped.get(code)
                if bucket is None or len(bucket) >= cap:
                    continue
                title = str(row["title"] or "").strip()
                if title:
                    bucket.append({"title": title})
        finally:
            conn.close()
    out: Dict[str, dict] = {}
    for code in bare:
        items = grouped.get(code) or []
        if not items:
            out[code] = {"code": code, "label": "missing", "n_titles": 0}
            continue
        sent = score_headlines(items, lexicon=lex)
        out[code] = {
            "code": code,
            "label": str((sent or {}).get("label") or "neutral"),
            "n_titles": len(items),
        }
    return out


def sentiment_gate_enabled(cfg: Optional[Mapping[str, Any]]) -> bool:
    """总开关。缺键视为关。"""
    from core.t0.config import coerce_cfg_bool

    d = cfg if isinstance(cfg, Mapping) else {}
    if "sentiment_gate_enabled" not in d:
        return False
    try:
        return bool(coerce_cfg_bool(d.get("sentiment_gate_enabled"), False))
    except Exception:  # noqa: BLE001
        logger.debug("sentiment_gate_enabled coerce failed", exc_info=True)
        return False


def _sentiment_row_eligible(row: Optional[Mapping[str, Any]]) -> bool:
    """窗口完整、未降级、有标题。缺测 / 空窗 / 降级不进先验。"""
    if not isinstance(row, Mapping):
        return False
    if str(row.get("coverage_status") or "") != "ok":
        return False
    try:
        degraded = int(row.get("degraded") or 0)
        n_titles = int(row.get("n_titles") or 0)
    except (TypeError, ValueError):
        return False
    return not degraded and n_titles > 0


def sentiment_prior_of(row: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """空/多软先验。返回 fuse_delta（净收益）与 rank_mult；缺测不调。

    空：fuse −0.1%，rank ×2。多：fuse +0.1%，rank ×0.5。
    中性 / 混合 / missing 不调。不进 ŷ，不单独清仓。
    """
    if not _sentiment_row_eligible(row):
        return None
    label = str((row or {}).get("label") or "").strip()
    if label == "bearish":
        return {
            "label": "bearish",
            "fuse_delta": -float(SENTIMENT_FUSE_DELTA),
            "rank_mult": float(SENTIMENT_RANK_MULT_BEAR),
        }
    if label == "bullish":
        return {
            "label": "bullish",
            "fuse_delta": float(SENTIMENT_FUSE_DELTA),
            "rank_mult": float(SENTIMENT_RANK_MULT_BULL),
        }
    return None


def sentiment_prior_note(prior: Optional[Mapping[str, Any]]) -> str:
    """拼进跳过/入场说明。"""
    if not isinstance(prior, Mapping):
        return ""
    label = str(prior.get("label") or "").strip()
    if label == "bearish":
        lab = "空"
    elif label == "bullish":
        lab = "多"
    else:
        return ""
    try:
        delta = float(prior.get("fuse_delta") or 0.0) * 100.0
        mult = float(prior.get("rank_mult") or 1.0)
    except (TypeError, ValueError):
        return f"舆情{lab}"
    return f"舆情{lab} fuse{delta:+.1f}% rank×{mult:g}"


def is_sentiment_gate_reason(reason: Any) -> bool:
    """desk 钉住：旧硬闸 reason「舆情闸*」或软先验说明含「舆情空/多」。"""
    text = str(reason or "").strip()
    return text.startswith("舆情闸") or "舆情空" in text or "舆情多" in text


def row_is_sentiment_gate(row: Optional[Mapping[str, Any]]) -> bool:
    """desk 钉住舆情相关跳过/标注行（含历史硬闸行与软先验行）。"""
    if not isinstance(row, Mapping):
        return False
    if row.get("sentiment_gate") is True:
        return True
    if row.get("sentiment_prior"):
        return True
    return is_sentiment_gate_reason(row.get("reason"))


def due_poll_slot(
    now: Optional[datetime] = None,
    *,
    holidays: Optional[Set[str]] = None,
) -> str:
    """当前已到、尚未被调用方判定的最晚槽。未到任何槽返回空串。"""
    from core.market.calendar import is_trading_day

    dt = now or datetime.now()
    day = dt.strftime("%Y-%m-%d")
    slots = POLL_SLOTS_TRADING if is_trading_day(day, holidays=holidays) else POLL_SLOTS_CLOSED
    hhmm = dt.hour * 60 + dt.minute
    due = ""
    for slot in slots:
        hh, mm = _parse_hm(slot)
        if hhmm >= hh * 60 + mm:
            due = slot
    if not due:
        return ""
    return f"{day}T{due}"


_POLL_CLAIM_STALE_SEC = 45 * 60


def _slot_done(
    conn: sqlite3.Connection,
    slot_key: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    row = conn.execute(
        "SELECT stop_reason, ran_at FROM poll_slot WHERE slot_key = ?",
        (slot_key,),
    ).fetchone()
    if row is None:
        return False
    if str(row["stop_reason"] or "") != "running":
        return True
    # running 认领：未过期视为占用；崩溃遗留超时后可抢回
    ran = _parse_stored_ts(str(row["ran_at"] or ""))
    if ran is None:
        return False
    ref = now or datetime.now()
    return (ref - ran).total_seconds() <= _POLL_CLAIM_STALE_SEC


def archive_universe_codes(*, cap: Optional[int] = None) -> List[str]:
    """观察池并上仍有持仓、但不在池内的代码。cap 为空则不截断。"""
    seen: List[str] = []

    def add(raw: Any) -> None:
        code = bare_a_share(raw)
        if code and code not in seen:
            seen.append(code)

    try:
        from core.paths import WATCHING_PATH

        if os.path.isfile(WATCHING_PATH):
            with open(WATCHING_PATH, encoding="utf-8") as f:
                doc = json.load(f)
            for raw in (doc.get("watchlist") or []) if isinstance(doc, dict) else []:
                add(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        logger.debug("news archive watchlist read failed", exc_info=True)
    try:
        from core.paper import load_paper
        from core.paths import PAPER_PATH

        if os.path.isfile(PAPER_PATH):
            paper = load_paper(PAPER_PATH)
            for h in (paper.get("holdings") or []) if isinstance(paper, dict) else []:
                if not isinstance(h, dict):
                    continue
                try:
                    shares = float(h.get("shares") or 0)
                except (TypeError, ValueError):
                    shares = 0.0
                if shares > 0:
                    add(h.get("stock_code"))
    except Exception:  # noqa: BLE001
        logger.debug("news archive holdings read failed", exc_info=True)
    if cap is None:
        return seen
    return seen[: max(1, int(cap))]


_PACE_INTERVAL_SEC = 1.2
_BLOCK_STOP = 2
_FAIL_STOP = 6
_pace_lock = threading.Lock()
_pace_next = 0.0
_poll_run_lock = threading.Lock()
_poll_state_lock = threading.Lock()
_poll_state: Dict[str, Any] = {
    "running": False,
    "started_at": "",
    "finished_at": "",
    "n_ok": 0,
    "n_fail": 0,
    "n_done": 0,
    "n_total": 0,
    "n_skipped": 0,
    "code": "",
    "stop_reason": "",
}


def poll_progress() -> Dict[str, Any]:
    """整池更新的进度。只读内存，不打网。"""
    with _poll_state_lock:
        snap = dict(_poll_state)
    total = int(snap.get("n_total") or 0)
    done = int(snap.get("n_done") or 0)
    snap["pct"] = round(100.0 * done / total, 1) if total else 0.0
    return snap


def _set_poll_state(**kwargs: Any) -> None:
    with _poll_state_lock:
        _poll_state.update(kwargs)


def _reset_poll_state(total: int, started_at: str) -> None:
    _set_poll_state(
        running=True,
        started_at=started_at,
        finished_at="",
        n_ok=0,
        n_fail=0,
        n_done=0,
        n_total=int(total),
        n_skipped=0,
        code="",
        stop_reason="",
    )


def _interval_sec() -> float:
    raw = os.environ.get("QUANTLAB_NEWS_ARCHIVE_INTERVAL", str(_PACE_INTERVAL_SEC))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return _PACE_INTERVAL_SEC


def _is_block_error(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    try:
        code = int(status) if status is not None else None
    except (TypeError, ValueError):
        code = None
    if code in (403, 418, 429, 503):
        return True
    msg = str(exc).lower()
    return any(token in msg for token in ("429", "403", "too many", "频繁", "forbidden", "rate limit"))


def _pace_before_request() -> None:
    """相邻两次东财请求至少隔开间隔，并带一点抖动。"""
    global _pace_next
    interval = _interval_sec()
    if interval <= 0:
        return
    with _pace_lock:
        now = time.monotonic()
        wait = _pace_next - now
        jitter = random.uniform(0, min(0.45, interval * 0.35))
        _pace_next = max(now, _pace_next) + interval + jitter
    if wait > 0:
        time.sleep(wait)


def _pace_penalize(seconds: float) -> None:
    global _pace_next
    with _pace_lock:
        _pace_next = max(_pace_next, time.monotonic() + max(0.0, float(seconds)))


def poll_news_archive(
    *,
    codes: Optional[Sequence[str]] = None,
    now: Optional[datetime] = None,
    store_dir: Optional[str] = None,
    fetcher: Optional[Callable[[str], Sequence[Mapping[str, Any]]]] = None,
    slot_key: str = "",
    holidays: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """拉当前页标题入库。fetcher 缺省走东财搜索。已有一轮在跑时不再开第二轮。"""
    if not _poll_run_lock.acquire(blocking=False):
        return {"ok": True, "ran": False, "reason": "busy", "progress": poll_progress()}
    try:
        out = _poll_news_archive_body(
            codes=codes,
            now=now,
            store_dir=store_dir,
            fetcher=fetcher,
            slot_key=slot_key,
            holidays=holidays,
        )
        _set_poll_state(running=False, finished_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        if isinstance(out, dict):
            out["progress"] = poll_progress()
        return out
    except Exception:
        _set_poll_state(running=False, finished_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        raise
    finally:
        _poll_run_lock.release()


def _poll_news_archive_body(
    *,
    codes: Optional[Sequence[str]] = None,
    now: Optional[datetime] = None,
    store_dir: Optional[str] = None,
    fetcher: Optional[Callable[[str], Sequence[Mapping[str, Any]]]] = None,
    slot_key: str = "",
    holidays: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    dt = now or datetime.now()
    fetched_at = dt.strftime("%Y-%m-%dT%H:%M:%S")
    universe = [bare_a_share(c) for c in (codes if codes is not None else archive_universe_codes())]
    universe = [c for c in universe if c]
    fetch = fetcher or _default_fetcher
    n_ok = 0
    n_fail = 0
    failed_codes: List[str] = []
    stop_reason = ""
    block_streak = 0
    fail_streak = 0
    _reset_poll_state(len(universe), fetched_at)
    for code in universe:
        _set_poll_state(code=code)
        _pace_before_request()
        try:
            rows = list(fetch(code) or [])
            ingest_title_rows(
                code,
                rows,
                fetched_at=fetched_at,
                store_dir=store_dir,
                ok=True,
                degraded=False,
            )
            n_ok += 1
            block_streak = 0
            fail_streak = 0
            _set_poll_state(n_ok=n_ok, n_fail=n_fail, n_done=n_ok + n_fail, code=code)
        except Exception as exc:  # noqa: BLE001
            logger.debug("news archive fetch failed for %s", code, exc_info=True)
            ingest_title_rows(
                code,
                [],
                fetched_at=fetched_at,
                store_dir=store_dir,
                ok=False,
                degraded=True,
                error=str(exc),
            )
            n_fail += 1
            fail_streak += 1
            failed_codes.append(code)
            _set_poll_state(n_ok=n_ok, n_fail=n_fail, n_done=n_ok + n_fail, code=code)
            if _is_block_error(exc):
                block_streak += 1
                _pace_penalize(min(45.0, 8.0 * (2 ** (block_streak - 1))))
                if block_streak >= _BLOCK_STOP:
                    stop_reason = "rate_limit"
                    break
            elif fail_streak >= _FAIL_STOP:
                stop_reason = "fail_streak"
                break
    n_skipped = max(0, len(universe) - n_ok - n_fail)
    _set_poll_state(n_skipped=n_skipped, stop_reason=stop_reason, code="")
    key = slot_key or due_poll_slot(dt, holidays=holidays) or fetched_at[:16]
    with _write_lock:
        conn = _connect(store_dir, create=True)
        if conn is not None:
            try:
                conn.execute(
                    """
                    INSERT INTO poll_slot (
                      slot_key, ran_at, n_codes, n_ok, n_fail, n_skipped, stop_reason
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(slot_key) DO UPDATE SET
                      ran_at = excluded.ran_at,
                      n_codes = excluded.n_codes,
                      n_ok = excluded.n_ok,
                      n_fail = excluded.n_fail,
                      n_skipped = excluded.n_skipped,
                      stop_reason = excluded.stop_reason
                    """,
                    (key, fetched_at, len(universe), n_ok, n_fail, n_skipped, stop_reason),
                )
                conn.commit()
            finally:
                conn.close()
    return {
        "ok": True,
        "ran": True,
        "slot": key,
        "n_codes": len(universe),
        "n_ok": n_ok,
        "n_fail": n_fail,
        "n_skipped": n_skipped,
        "stop_reason": stop_reason,
        "failed_codes": failed_codes,
        "fetched_at": fetched_at,
        "progress": poll_progress(),
    }


def _default_fetcher(code: str) -> List[dict]:
    from adapters.news.engine import fetch_news_rows

    return list(fetch_news_rows(code) or [])


_DISPLAY_TTL_SEC = 15 * 60
_DISPLAY_BATCH = 20


def _latest_fetch_row(
    conn: sqlite3.Connection, code: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT fetched_at, ok, degraded, error
        FROM fetch_log
        WHERE code = ?
        ORDER BY fetched_at DESC, id DESC
        LIMIT 1
        """,
        (code,),
    ).fetchone()


def _display_items(conn: sqlite3.Connection, code: str, limit: int) -> List[dict]:
    rows = conn.execute(
        """
        SELECT title, published_at, published_date, source, url
        FROM headline
        WHERE code = ?
        ORDER BY COALESCE(NULLIF(published_at, ''), published_date) DESC, id DESC
        LIMIT ?
        """,
        (code, int(limit)),
    ).fetchall()
    items: List[dict] = []
    for row in rows:
        published = str(row["published_at"] or "")
        when = published.replace("T", " ") if published else str(row["published_date"] or "")
        title = str(row["title"] or "").strip()
        if not title:
            continue
        items.append(
            {
                "title": title,
                "time": when,
                "source": str(row["source"] or ""),
                "url": str(row["url"] or ""),
            }
        )
    return items


def load_headlines_as_of(
    code: str,
    as_of: str,
    *,
    store_dir: Optional[str] = None,
    limit: int = 200,
) -> List[dict]:
    """PIT：发布日 ≤ as_of 的标题；无发布时间则用 fetched_at 日。"""
    bare = bare_a_share(code)
    as_of_d = str(as_of or "").strip()[:10]
    if not bare or not as_of_d:
        return []
    conn = _connect(store_dir, create=False)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT title, published_at, published_date, fetched_at, source, url
            FROM headline
            WHERE code = ?
              AND (
                (published_date IS NOT NULL AND published_date != '' AND published_date <= ?)
                OR (
                  (published_date IS NULL OR published_date = '')
                  AND substr(fetched_at, 1, 10) <= ?
                )
              )
            ORDER BY COALESCE(NULLIF(published_at, ''), published_date) DESC, id DESC
            LIMIT ?
            """,
            (bare, as_of_d, as_of_d, max(1, min(int(limit or 200), 500))),
        ).fetchall()
        out: List[dict] = []
        seen: Set[str] = set()
        for row in rows:
            title = str(row["title"] or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            published = str(row["published_at"] or "")
            when = (
                published.replace("T", " ")
                if published
                else str(row["published_date"] or row["fetched_at"] or "")[:19]
            )
            out.append(
                {
                    "title": title,
                    "time": when,
                    "source": str(row["source"] or ""),
                    "url": str(row["url"] or ""),
                }
            )
        return out
    finally:
        conn.close()


def _read_code_archive(
    code: str, limit: int, store_dir: Optional[str]
) -> Dict[str, Any]:
    empty: Dict[str, Any] = {
        "items": [],
        "fetched_at": "",
        "ok": None,
        "degraded": None,
        "error": "",
    }
    conn = _connect(store_dir, create=False)
    if conn is None:
        return empty
    try:
        log = _latest_fetch_row(conn, code)
        return {
            "items": _display_items(conn, code, limit),
            "fetched_at": str(log["fetched_at"] or "") if log else "",
            "ok": None if log is None else bool(int(log["ok"] or 0)),
            "degraded": None if log is None else bool(int(log["degraded"] or 0)),
            "error": str(log["error"] or "") if log else "",
        }
    finally:
        conn.close()


def _display_cache_eligible(
    local: Mapping[str, Any],
    *,
    now: datetime,
    max_age_sec: int,
) -> bool:
    """展示缓存可用：时间新鲜 + 上次成功 + 有标题（空/失败不当新鲜）。"""
    if not _fetch_is_fresh(str(local.get("fetched_at") or ""), now, max_age_sec):
        return False
    if local.get("ok") is not True:
        return False
    if local.get("degraded") is True:
        return False
    return bool(local.get("items"))


def peek_code_headlines(
    code: str,
    *,
    limit: int = 5,
    store_dir: Optional[str] = None,
    lexicon: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """只读本地库快照（不拉网），供告警对比 before。"""
    bare = bare_a_share(code)
    names: Dict[str, str] = {}
    try:
        from core.t0.intraday import _load_a_code_name_map

        names = dict(_load_a_code_name_map() or {})
    except Exception:  # noqa: BLE001
        names = {}
    local = _read_code_archive(bare, limit, store_dir)
    return _watch_row(
        bare,
        local,
        names=names,
        lexicon=lexicon,
        from_cache=True,
    )


def _fetch_is_fresh(fetched_at: str, now: datetime, max_age_sec: int) -> bool:
    ts = _parse_stored_ts(fetched_at)
    if ts is None:
        return False
    age = (now - ts).total_seconds()
    return 0 <= age <= max(0, int(max_age_sec))


def _missing_sentiment(note: str) -> Dict[str, Any]:
    return {
        "label": "missing",
        "score": None,
        "hit_pos": [],
        "hit_neg": [],
        "n_titles": 0,
        "note": note,
        "include_in_score": False,
        "role": "prior",
        "degraded": False,
        "prior_eligible": False,
    }


def _watch_row(
    code: str,
    local: Mapping[str, Any],
    *,
    names: Mapping[str, str],
    lexicon: Optional[Mapping[str, Any]],
    from_cache: bool,
    error: Optional[str] = None,
    stale: bool = False,
) -> Dict[str, Any]:
    from core.sentiment import score_headlines

    items = list(local.get("items") or [])
    if items:
        sent = score_headlines(items, lexicon=dict(lexicon) if lexicon is not None else None)
        ok = True
        err = error
    else:
        note = error or "本地新闻库尚无标题"
        sent = _missing_sentiment(note)
        ok = False
        err = note
    return {
        "ok": ok,
        "stock_code": code,
        "stock_name": str(names.get(code) or ""),
        "items": items,
        "sentiment": sent,
        "updated_at": str(local.get("fetched_at") or "") or None,
        "from_cache": from_cache,
        "from_archive": True,
        "stale": stale,
        "error": err,
    }


def serve_watch_headlines(
    codes: Sequence[str],
    *,
    limit: int = 3,
    max_age_sec: int = _DISPLAY_TTL_SEC,
    force: bool = False,
    fetcher: Optional[Callable[[str], Sequence[Mapping[str, Any]]]] = None,
    store_dir: Optional[str] = None,
    now: Optional[datetime] = None,
    lexicon: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """数据中心表格：先读本地。没有或超过 TTL 再向东财要，并写回本地。

    单批最多 20 只。与整池轮询共用频控；连续封禁信号后，其余代码不请求、不记失败。
    """
    limit = max(1, min(int(limit or 3), 15))
    now_dt = now or datetime.now()
    fetched_at = now_dt.strftime("%Y-%m-%dT%H:%M:%S")
    fetch = fetcher or _default_fetcher
    bare: List[str] = []
    for raw in codes or []:
        code = bare_a_share(raw)
        if code and code not in bare:
            bare.append(code)
        if len(bare) >= _DISPLAY_BATCH:
            break
    names: Dict[str, str] = {}
    if bare:
        try:
            from core.t0.intraday import _load_a_code_name_map

            names = dict(_load_a_code_name_map() or {})
        except Exception:  # noqa: BLE001
            logger.debug("watch headline names failed", exc_info=True)
            names = {}
    rows_out: List[dict] = []
    stop_reason = ""
    block_streak = 0
    fail_streak = 0
    for code in bare:
        local = _read_code_archive(code, limit, store_dir)
        fresh = (not force) and _display_cache_eligible(
            local, now=now_dt, max_age_sec=max_age_sec
        )
        if fresh or stop_reason:
            rows_out.append(
                _watch_row(
                    code,
                    local,
                    names=names,
                    lexicon=lexicon,
                    from_cache=True,
                )
            )
            continue
        _pace_before_request()
        try:
            pulled = list(fetch(code) or [])
            ingest_title_rows(
                code,
                pulled,
                fetched_at=fetched_at,
                store_dir=store_dir,
                ok=True,
                degraded=False,
            )
            block_streak = 0
            fail_streak = 0
            local = _read_code_archive(code, limit, store_dir)
            rows_out.append(
                _watch_row(
                    code,
                    local,
                    names=names,
                    lexicon=lexicon,
                    from_cache=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("watch headline fetch failed for %s", code, exc_info=True)
            ingest_title_rows(
                code,
                [],
                fetched_at=fetched_at,
                store_dir=store_dir,
                ok=False,
                degraded=True,
                error=str(exc),
            )
            fail_streak += 1
            local = _read_code_archive(code, limit, store_dir)
            reason = f"资讯拉取失败：{exc}"
            rows_out.append(
                _watch_row(
                    code,
                    local,
                    names=names,
                    lexicon=lexicon,
                    from_cache=bool(local.get("items")),
                    error=reason if local.get("items") else reason,
                    stale=bool(local.get("items")),
                )
            )
            if _is_block_error(exc):
                block_streak += 1
                _pace_penalize(min(45.0, 8.0 * (2 ** (block_streak - 1))))
                if block_streak >= _BLOCK_STOP:
                    stop_reason = "rate_limit"
            elif fail_streak >= _FAIL_STOP:
                stop_reason = "fail_streak"
    note = "观察池先读本地新闻库；没有或超过 15 分钟再向东财要，并写回本地。不进 ŷ。"
    if stop_reason == "rate_limit":
        note += " 出现封禁信号，剩余股票未请求。"
    elif stop_reason == "fail_streak":
        note += " 连续失败，剩余股票未请求。"
    return {
        "ok": True,
        "count": len(rows_out),
        "limit": limit,
        "items": rows_out,
        "stop_reason": stop_reason,
        "note": note,
    }


def next_poll_slot(
    now: Optional[datetime] = None,
    *,
    holidays: Optional[Set[str]] = None,
) -> str:
    """严格晚于 now 的下一档。已到点的档不算下一档。"""
    from datetime import timedelta

    from core.market.calendar import is_trading_day

    dt = now or datetime.now()
    dt = dt.replace(second=0, microsecond=0)
    for offset in range(8):
        day_dt = (dt + timedelta(days=offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day = day_dt.strftime("%Y-%m-%d")
        slots = (
            POLL_SLOTS_TRADING
            if is_trading_day(day, holidays=holidays)
            else POLL_SLOTS_CLOSED
        )
        for slot in slots:
            hh, mm = _parse_hm(slot)
            if day_dt.replace(hour=hh, minute=mm) > dt:
                return f"{day}T{slot}"
    return ""


def _median_count(counts: Sequence[int]) -> int:
    ordered = sorted(int(x) for x in counts)
    if not ordered:
        return 0
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid] + 1) // 2


def _empty_portrait() -> Dict[str, Any]:
    return {
        "n_titles": 0,
        "n_codes": 0,
        "published_min": "",
        "published_max": "",
        "n_timestamp": 0,
        "n_date_only": 0,
        "titles_median": 0,
        "titles_max": 0,
        "n_rounds": 0,
        "n_seen": 0,
        "n_inserted": 0,
        "labels": {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0},
        "n_titles_today": 0,
        "n_hit_today": 0,
    }


def _apply_label_portrait(out: Dict[str, Any], labels: Mapping[str, Any]) -> None:
    counts = out["labels"]
    for row in labels.values():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("coverage_status") or "") != "ok":
            continue
        lab = str(row.get("label") or "")
        if lab in counts:
            counts[lab] += 1
        try:
            out["n_titles_today"] += int(row.get("n_titles") or 0)
            out["n_hit_today"] += int(row.get("n_hit") or 0)
        except (TypeError, ValueError):
            continue


def _archive_portrait(
    store_dir: Optional[str],
    labels: Mapping[str, Any],
) -> Dict[str, Any]:
    """库存与今日标签的只读统计。库不存在时全 0，不创建文件。"""
    out = _empty_portrait()
    _apply_label_portrait(out, labels)
    conn = _connect(store_dir, create=False)
    if conn is None:
        return out
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS n_titles,
              COUNT(DISTINCT code) AS n_codes,
              MIN(published_date) AS published_min,
              MAX(published_date) AS published_max,
              COALESCE(SUM(CASE WHEN clock_quality = 'timestamp' THEN 1 ELSE 0 END), 0) AS n_timestamp,
              COALESCE(SUM(CASE WHEN clock_quality = 'date_only' THEN 1 ELSE 0 END), 0) AS n_date_only
            FROM headline
            """
        ).fetchone()
        out["n_titles"] = int(row["n_titles"] or 0)
        out["n_codes"] = int(row["n_codes"] or 0)
        out["published_min"] = str(row["published_min"] or "")
        out["published_max"] = str(row["published_max"] or "")
        out["n_timestamp"] = int(row["n_timestamp"] or 0)
        out["n_date_only"] = int(row["n_date_only"] or 0)
        counts = [
            int(r[0] or 0)
            for r in conn.execute("SELECT COUNT(*) FROM headline GROUP BY code")
        ]
        out["titles_max"] = max(counts) if counts else 0
        out["titles_median"] = _median_count(counts)
        fetched = conn.execute(
            """
            SELECT
              COUNT(DISTINCT fetched_at) AS n_rounds,
              COALESCE(SUM(n_rows), 0) AS n_seen,
              COALESCE(SUM(CASE WHEN ok = 1 THEN n_inserted ELSE 0 END), 0) AS n_inserted
            FROM fetch_log
            """
        ).fetchone()
        out["n_rounds"] = int(fetched["n_rounds"] or 0)
        out["n_seen"] = int(fetched["n_seen"] or 0)
        out["n_inserted"] = int(fetched["n_inserted"] or 0)
    finally:
        conn.close()
    return out


def _sample_label(title: str, lexicon: Optional[Mapping[str, Any]]) -> str:
    try:
        from core.sentiment import score_headlines

        sent = score_headlines([{"title": title}], lexicon=lexicon)
        return str(sent.get("label") or "")
    except Exception:  # noqa: BLE001
        logger.debug("news sample score failed", exc_info=True)
        return ""


def _sample_catalog(store_dir: Optional[str]) -> Dict[str, Any]:
    """已入库的发布日和股票。库不存在时为空，不创建文件。"""
    conn = _connect(store_dir, create=False)
    if conn is None:
        return {"dates": [], "stocks": []}
    try:
        dates = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT published_date
                FROM headline
                WHERE published_date != ''
                ORDER BY published_date DESC
                """
            ).fetchall()
            if str(row[0] or "")
        ]
        codes = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT code FROM headline WHERE code != '' ORDER BY code
                """
            ).fetchall()
            if str(row[0] or "")
        ]
    finally:
        conn.close()
    names: Dict[str, str] = {}
    if codes:
        try:
            from core.t0.intraday import _load_a_code_name_map

            names = dict(_load_a_code_name_map() or {})
        except Exception:  # noqa: BLE001
            logger.debug("news sample names failed", exc_info=True)
            names = {}
    stocks = [
        {"code": code, "name": str(names.get(code) or "")} for code in codes
    ]
    stocks.sort(key=lambda item: (item["name"] or item["code"], item["code"]))
    return {"dates": dates, "stocks": stocks}


def _present_samples(
    items: Sequence[Mapping[str, Any]],
    *,
    fill_clock: str,
    holidays: Optional[Set[str]],
) -> List[dict]:
    names: Dict[str, str] = {}
    if items:
        try:
            from core.t0.intraday import _load_a_code_name_map

            names = dict(_load_a_code_name_map() or {})
        except Exception:  # noqa: BLE001
            logger.debug("news sample names failed", exc_info=True)
            names = {}
    out: List[dict] = []
    for item in items:
        code = str(item.get("code") or "")
        out.append(
            {
                "code": code,
                "name": str(names.get(code) or ""),
                "title": str(item.get("title") or ""),
                "published_at": str(item.get("published_at") or ""),
                "published_date": str(item.get("published_date") or ""),
                "clock_quality": str(item.get("clock_quality") or ""),
                "fetched_at": str(item.get("fetched_at") or ""),
                "url": str(item.get("url") or ""),
                "decision_date": assign_decision_day(
                    published_at=str(item.get("published_at") or ""),
                    published_date=str(item.get("published_date") or ""),
                    clock_quality=str(item.get("clock_quality") or ""),
                    fetched_at=str(item.get("fetched_at") or ""),
                    fill_clock=fill_clock,
                    holidays=holidays,
                ),
                "label": str(item.get("label") or ""),
            }
        )
    return out


def _query_sample_headlines(
    store_dir: Optional[str],
    *,
    published_date: str,
    code: str,
    fill_clock: str,
    holidays: Optional[Set[str]],
) -> List[dict]:
    """按发布日和股票筛选标题。任一条件为空表示该维不限制。"""
    conn = _connect(store_dir, create=False)
    if conn is None:
        return []
    day = str(published_date or "")[:10]
    bare = bare_a_share(code)
    try:
        rows = conn.execute(
            """
            SELECT code, title, published_at, published_date, clock_quality, fetched_at, url
            FROM headline
            WHERE (? = '' OR published_date = ?)
              AND (? = '' OR code = ?)
            ORDER BY published_date DESC,
                     CASE WHEN published_at = '' THEN 1 ELSE 0 END,
                     published_at DESC,
                     id DESC
            """,
            (day, day, bare, bare),
        ).fetchall()
    finally:
        conn.close()
    try:
        from core.sentiment import load_lexicon

        lexicon = load_lexicon()
    except Exception:  # noqa: BLE001
        logger.debug("news sample lexicon failed", exc_info=True)
        lexicon = None
    items = [
        {
            "code": str(row["code"] or ""),
            "title": str(row["title"] or "").strip(),
            "published_at": str(row["published_at"] or ""),
            "published_date": str(row["published_date"] or ""),
            "clock_quality": str(row["clock_quality"] or ""),
            "fetched_at": str(row["fetched_at"] or ""),
            "url": str(row["url"] or ""),
            "label": _sample_label(str(row["title"] or ""), lexicon),
        }
        for row in rows
        if str(row["title"] or "").strip()
    ]
    return _present_samples(items, fill_clock=fill_clock, holidays=holidays)


def _sample_headlines(
    store_dir: Optional[str],
    *,
    fill_clock: str,
    holidays: Optional[Set[str]],
    limit: int = _SAMPLE_LIMIT,
) -> List[dict]:
    """最近标题里抽几条不同的。优先带出多、空、混合。不创建文件。"""
    conn = _connect(store_dir, create=False)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT code, title, published_at, published_date, clock_quality, fetched_at, url
            FROM headline
            ORDER BY published_date DESC,
                     CASE WHEN published_at = '' THEN 1 ELSE 0 END,
                     published_at DESC,
                     id DESC
            LIMIT 400
            """
        ).fetchall()
    finally:
        conn.close()
    try:
        from core.sentiment import load_lexicon

        lexicon = load_lexicon()
    except Exception:  # noqa: BLE001
        logger.debug("news sample lexicon failed", exc_info=True)
        lexicon = None
    cap = max(1, int(limit or _SAMPLE_LIMIT))
    scored: List[dict] = []
    seen_titles: Set[str] = set()
    for row in rows:
        code = str(row["code"] or "")
        title = str(row["title"] or "").strip()
        if not code or not title:
            continue
        digest = title_hash(title)
        if digest in seen_titles:
            continue
        seen_titles.add(digest)
        scored.append(
            {
                "code": code,
                "title": title,
                "published_at": str(row["published_at"] or ""),
                "published_date": str(row["published_date"] or ""),
                "clock_quality": str(row["clock_quality"] or ""),
                "fetched_at": str(row["fetched_at"] or ""),
                "url": str(row["url"] or ""),
                "label": _sample_label(title, lexicon),
            }
        )
        if len(scored) >= 80:
            break
    picked: List[dict] = []
    used: Set[int] = set()
    for lab in ("bearish", "bullish", "mixed", "neutral"):
        for i, item in enumerate(scored):
            if i in used or item["label"] != lab:
                continue
            picked.append(item)
            used.add(i)
            break
        if len(picked) >= cap:
            break
    for i, item in enumerate(scored):
        if len(picked) >= cap:
            break
        if i in used:
            continue
        picked.append(item)
        used.add(i)
    picked.sort(
        key=lambda item: (item["published_at"] or item["published_date"], item["code"]),
        reverse=True,
    )
    return _present_samples(picked[:cap], fill_clock=fill_clock, holidays=holidays)


def archive_status(
    *,
    now: Optional[datetime] = None,
    store_dir: Optional[str] = None,
    holidays: Optional[Set[str]] = None,
    codes: Optional[Sequence[str]] = None,
    fill_clock: Optional[str] = None,
    sample_date: str = "",
    sample_code: str = "",
) -> Dict[str, Any]:
    """数据中心面板：最近一档、下一档、今日闸覆盖、库存统计画像。不打网。"""
    from core.market.calendar import resolve_session_date

    dt = now or datetime.now()
    if codes is None:
        universe = archive_universe_codes()
    else:
        universe = [bare_a_share(c) for c in codes if bare_a_share(c)]
    clock = str(fill_clock or "").strip()[:5]
    if not clock:
        try:
            from core.paper.rebalance.auto_worker import rebalance_fill_clock

            clock = rebalance_fill_clock()
        except Exception:  # noqa: BLE001
            logger.debug("news archive fill clock failed", exc_info=True)
            clock = _FILL_CLOCK
    day = resolve_session_date(now=dt)
    labels = (
        load_decision_labels(
            universe,
            day,
            clock,
            store_dir=store_dir,
            holidays=holidays,
        )
        if universe and day
        else {}
    )
    coverage = {"ok": 0, "empty": 0, "gap": 0, "degraded": 0}
    for code in universe:
        status = str((labels.get(code) or {}).get("coverage_status") or "gap")
        if status not in coverage:
            status = "gap"
        coverage[status] += 1
    last, failed = _last_poll_view(store_dir)
    catalog = _sample_catalog(store_dir)
    picked_date = str(sample_date or "")[:10]
    if picked_date and picked_date not in catalog["dates"]:
        picked_date = ""
    picked_code = bare_a_share(sample_code)
    known_codes = {item["code"] for item in catalog["stocks"]}
    if picked_code and picked_code not in known_codes:
        picked_code = ""
    if picked_date or picked_code:
        samples = _query_sample_headlines(
            store_dir,
            published_date=picked_date,
            code=picked_code,
            fill_clock=clock,
            holidays=holidays,
        )
    else:
        samples = _sample_headlines(
            store_dir,
            fill_clock=clock,
            holidays=holidays,
        )
    return {
        "ok": True,
        "last": last,
        "next_slot": next_poll_slot(dt, holidays=holidays),
        "decision_date": day,
        "fill_clock": clock,
        "n_universe": len(universe),
        "capped": False,
        "cap": len(universe),
        "coverage": coverage,
        "portrait": _archive_portrait(store_dir, labels),
        "samples": samples,
        "sample_dates": catalog["dates"],
        "sample_stocks": catalog["stocks"],
        "sample_date": picked_date,
        "sample_code": picked_code,
        "progress": poll_progress(),
        "failed_codes": failed,
        "note": (
            "表格先读本地；没有或超过 15 分钟再向东财要并写回。"
            "整池更新只追加当前页，不回补过去的交易日。"
            "每只间隔约 1.2 秒，出现封禁信号即停止。"
            "历史回测按发布时间卡到当日 09:30。手动执行只读 09:30 前已入库"
        ),
    }


def _last_poll_view(store_dir: Optional[str]) -> tuple:
    conn = _connect(store_dir, create=False)
    if conn is None:
        return None, []
    try:
        row = conn.execute(
            """
            SELECT slot_key, ran_at, n_codes, n_ok, n_fail, n_skipped, stop_reason
            FROM poll_slot
            ORDER BY ran_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None, []
        keys = set(row.keys())
        last = {
            "slot": str(row["slot_key"] or ""),
            "ran_at": str(row["ran_at"] or ""),
            "n_codes": int(row["n_codes"] or 0),
            "n_ok": int(row["n_ok"] or 0),
            "n_fail": int(row["n_fail"] or 0),
            "n_skipped": int(row["n_skipped"] or 0) if "n_skipped" in keys else 0,
            "stop_reason": str(row["stop_reason"] or "") if "stop_reason" in keys else "",
        }
        failed = [
            str(r["code"] or "")
            for r in conn.execute(
                """
                SELECT code FROM fetch_log
                WHERE fetched_at = ? AND (ok = 0 OR degraded = 1)
                ORDER BY code
                """,
                (last["ran_at"],),
            ).fetchall()
            if str(r["code"] or "")
        ]
        return last, failed
    finally:
        conn.close()


def poll_allowed() -> bool:
    flag = os.environ.get("QUANTLAB_NEWS_ARCHIVE_POLL", "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def _try_claim_poll_slot(
    slot_key: str,
    *,
    store_dir: Optional[str] = None,
    ran_at: str = "",
    now: Optional[datetime] = None,
) -> bool:
    """槽位抢占：INSERT 成功才算拿到；已有完成/新鲜认领则 False。过期 running 可抢回。"""
    key = str(slot_key or "").strip()
    if not key:
        return True
    stamp = str(ran_at or "").strip() or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    ref = now or datetime.now()
    with _write_lock:
        conn = _connect(store_dir, create=True)
        if conn is None:
            return False
        try:
            row = conn.execute(
                "SELECT stop_reason, ran_at FROM poll_slot WHERE slot_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                reason = str(row["stop_reason"] or "")
                if reason == "running":
                    ran = _parse_stored_ts(str(row["ran_at"] or ""))
                    stale = ran is None or (ref - ran).total_seconds() > _POLL_CLAIM_STALE_SEC
                    if stale:
                        conn.execute(
                            "DELETE FROM poll_slot WHERE slot_key = ? AND stop_reason = 'running'",
                            (key,),
                        )
                    else:
                        return False
                else:
                    return False
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO poll_slot (
                  slot_key, ran_at, n_codes, n_ok, n_fail, n_skipped, stop_reason
                ) VALUES (?, ?, 0, 0, 0, 0, 'running')
                """,
                (key, stamp),
            )
            conn.commit()
            return int(cur.rowcount or 0) > 0
        finally:
            conn.close()


def maybe_poll_news_archive(
    *,
    now: Optional[datetime] = None,
    force: bool = False,
    codes: Optional[Sequence[str]] = None,
    store_dir: Optional[str] = None,
    fetcher: Optional[Callable[[str], Sequence[Mapping[str, Any]]]] = None,
    holidays: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """到点拉一次。同一槽不重复打网。force 忽略槽位去重。"""
    if not force and not poll_allowed():
        return {"ok": True, "ran": False, "reason": "disabled"}
    dt = now or datetime.now()
    slot = due_poll_slot(dt, holidays=holidays)
    if not force and not slot:
        return {"ok": True, "ran": False, "reason": "before_slot"}
    if not force and slot:
        conn = _connect(store_dir, create=False)
        if conn is not None:
            try:
                done = _slot_done(conn, slot, now=dt)
            finally:
                conn.close()
            if done:
                return {"ok": True, "ran": False, "reason": "already", "slot": slot}
        # 开跑前抢占槽位，避免多进程 TOCTOU 双拉
        if not _try_claim_poll_slot(
            slot,
            store_dir=store_dir,
            ran_at=dt.strftime("%Y-%m-%dT%H:%M:%S"),
            now=dt,
        ):
            return {"ok": True, "ran": False, "reason": "already", "slot": slot}
    return poll_news_archive(
        codes=codes,
        now=dt,
        store_dir=store_dir,
        fetcher=fetcher,
        slot_key=slot or "",
        holidays=holidays,
    )
