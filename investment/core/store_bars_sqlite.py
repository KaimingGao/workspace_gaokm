"""日线/分钟线 bars 的 SQLite WAL 后端（A1）。

配置/账本/基本面快照仍走 JSON；本模块仅服务 core.store 的 bars 读写。
上层经 ports 无感；可用 INVESTMENT_BARS_BACKEND=json 回滚。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS daily_bars (
  code          TEXT NOT NULL,
  market        TEXT NOT NULL,
  date          TEXT NOT NULL,
  open          REAL,
  high          REAL,
  low           REAL,
  close         REAL NOT NULL,
  volume        REAL,
  adjust_policy TEXT NOT NULL,
  data_source   TEXT,
  fetched_at    TEXT NOT NULL,
  PRIMARY KEY (code, date, adjust_policy, market)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_bars(date);
CREATE INDEX IF NOT EXISTS idx_daily_code ON daily_bars(code, adjust_policy);

CREATE TABLE IF NOT EXISTS daily_cache_meta (
  market        TEXT NOT NULL,
  code          TEXT NOT NULL,
  adjust_policy TEXT NOT NULL,
  stock_code    TEXT,
  data_source   TEXT,
  fetched_at    TEXT NOT NULL,
  quality       TEXT,
  bar_count     INTEGER,
  date_min      TEXT,
  date_max      TEXT,
  PRIMARY KEY (market, code, adjust_policy)
);

CREATE TABLE IF NOT EXISTS minute_bars (
  code          TEXT NOT NULL,
  market        TEXT NOT NULL,
  datetime      TEXT NOT NULL,
  date          TEXT,
  open          REAL,
  high          REAL,
  low           REAL,
  close         REAL,
  volume        REAL,
  adjust_policy TEXT NOT NULL,
  period        TEXT NOT NULL,
  data_source   TEXT,
  fetched_at    TEXT NOT NULL,
  PRIMARY KEY (code, datetime, period, adjust_policy, market)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_minute_date ON minute_bars(date);

CREATE TABLE IF NOT EXISTS minute_cache_meta (
  market        TEXT NOT NULL,
  code          TEXT NOT NULL,
  period        TEXT NOT NULL,
  adjust_policy TEXT NOT NULL,
  stock_code    TEXT,
  data_source   TEXT,
  fetched_at    TEXT NOT NULL,
  bar_count     INTEGER,
  date_min      TEXT,
  date_max      TEXT,
  PRIMARY KEY (market, code, period, adjust_policy)
);
"""

_conn_cache: Dict[str, sqlite3.Connection] = {}
_conn_guard = threading.Lock()
_write_lock = threading.Lock()


def db_path(store_dir: str) -> str:
    return os.path.join(store_dir, "bars.db")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    conn.commit()


def get_conn(store_dir: str) -> sqlite3.Connection:
    path = db_path(store_dir)
    with _conn_guard:
        c = _conn_cache.get(path)
        if c is not None:
            return c
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        c = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        c.row_factory = sqlite3.Row
        _ensure_schema(c)
        _conn_cache[path] = c
        return c


def reset_conn_cache() -> None:
    """测试用：关闭并清空连接缓存。"""
    with _conn_guard:
        for path, c in list(_conn_cache.items()):
            try:
                c.close()
            except Exception:  # noqa: BLE001
                logger.debug("close bars.db failed path=%s", path, exc_info=True)
        _conn_cache.clear()


def _norm_policy(adjust_policy: Optional[str]) -> str:
    p = str(adjust_policy or "qfq").strip().lower() or "qfq"
    return p


def _parse_fetched(fetched_s: str) -> Optional[datetime]:
    if not fetched_s:
        return None
    try:
        return datetime.fromisoformat(str(fetched_s))
    except ValueError:
        return None


def load_daily(
    market: str,
    code: str,
    *,
    min_bars: int,
    max_age_hours: float,
    store_dir: str,
    ignore_age: bool,
    assess_quality,
) -> Optional[Tuple[List[dict], Dict[str, Any]]]:
    mkt = market.upper()
    code_s = str(code)
    conn = get_conn(store_dir)
    row = conn.execute(
        """
        SELECT * FROM daily_cache_meta
        WHERE market=? AND code=?
        ORDER BY CASE adjust_policy WHEN 'qfq' THEN 0 ELSE 1 END, fetched_at DESC
        LIMIT 1
        """,
        (mkt, code_s),
    ).fetchone()
    if row is None:
        return None

    fetched_at = _parse_fetched(row["fetched_at"]) or datetime.now()
    if not ignore_age and max_age_hours > 0:
        if datetime.now() - fetched_at > timedelta(hours=max_age_hours):
            return None

    policy = row["adjust_policy"] or "qfq"
    bars_rows = conn.execute(
        """
        SELECT date, open, high, low, close, volume
        FROM daily_bars
        WHERE market=? AND code=? AND adjust_policy=?
        ORDER BY date
        """,
        (mkt, code_s, policy),
    ).fetchall()
    bars = [
        {
            "date": r["date"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
        }
        for r in bars_rows
    ]
    if len(bars) < min_bars:
        return None

    quality = row["quality"]
    if isinstance(quality, str) and quality:
        try:
            quality = json.loads(quality)
        except json.JSONDecodeError:
            quality = assess_quality(
                bars, data_source=row["data_source"] or "cache", fetched_at=fetched_at
            )
    elif not quality:
        quality = assess_quality(
            bars, data_source=row["data_source"] or "cache", fetched_at=fetched_at
        )

    meta = {
        "market": row["market"] or mkt,
        "code": row["code"] or code_s,
        "stock_code": row["stock_code"],
        "data_source": row["data_source"] or "cache",
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "quality": quality,
        "from_cache": True,
        "date_min": row["date_min"] or (bars[0].get("date") if bars else None),
        "date_max": row["date_max"] or (bars[-1].get("date") if bars else None),
        "bar_count": row["bar_count"] or len(bars),
        "adjust_policy": policy,
        "bars_backend": "sqlite",
    }
    return bars, meta


def save_daily(
    market: str,
    code: str,
    bars: List[dict],
    *,
    data_source: str,
    stock_code: Optional[str],
    store_dir: str,
    adjust_policy: Optional[str],
    assess_quality,
    trim_daily_bars,
) -> str:
    from core.store import daily_bar_complete, merge_bars_by_date

    mkt = market.upper()
    code_s = str(code)
    policy = _norm_policy(adjust_policy)
    incoming = [b for b in (bars or []) if daily_bar_complete(b)]
    path = db_path(store_dir)
    if not incoming:
        return path
    fetched_at = datetime.now()
    fetched_s = fetched_at.isoformat(timespec="seconds")
    conn = get_conn(store_dir)

    with _write_lock:
        rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume
            FROM daily_bars
            WHERE market=? AND code=? AND adjust_policy=?
            ORDER BY date
            """,
            (mkt, code_s, policy),
        ).fetchall()
        existing = [
            {
                "date": r["date"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
            }
            for r in rows
        ]
        bars = trim_daily_bars(merge_bars_by_date(existing, incoming))
        quality = assess_quality(bars, data_source=data_source, fetched_at=fetched_at)
        date_min = bars[0].get("date") if bars else None
        date_max = bars[-1].get("date") if bars else None
        conn.execute(
            "DELETE FROM daily_bars WHERE market=? AND code=? AND adjust_policy=?",
            (mkt, code_s, policy),
        )
        if bars:
            conn.executemany(
                """
                INSERT OR REPLACE INTO daily_bars
                (code, market, date, open, high, low, close, volume,
                 adjust_policy, data_source, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        code_s,
                        mkt,
                        b.get("date"),
                        b.get("open"),
                        b.get("high"),
                        b.get("low"),
                        b.get("close"),
                        b.get("volume"),
                        policy,
                        data_source,
                        fetched_s,
                    )
                    for b in bars
                    if b.get("date")
                ],
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_cache_meta
            (market, code, adjust_policy, stock_code, data_source, fetched_at,
             quality, bar_count, date_min, date_max)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mkt,
                code_s,
                policy,
                stock_code or code_s,
                data_source,
                fetched_s,
                json.dumps(quality, ensure_ascii=False),
                len(bars),
                date_min,
                date_max,
            ),
        )
        conn.commit()
    return path


def merge_save_daily(
    market: str,
    code: str,
    incoming: List[dict],
    *,
    data_source: str,
    stock_code: Optional[str],
    store_dir: str,
    adjust_policy: Optional[str],
    assess_quality,
    trim_daily_bars,
    merge_bars_by_date,
) -> Tuple[str, List[dict]]:
    policy = _norm_policy(adjust_policy)
    existing: List[dict] = []
    loaded = load_daily(
        market,
        code,
        min_bars=0,
        max_age_hours=0,
        store_dir=store_dir,
        ignore_age=True,
        assess_quality=assess_quality,
    )
    if loaded:
        existing, meta = loaded
        if str(meta.get("adjust_policy") or "qfq") != policy:
            existing = []
    merged = merge_bars_by_date(existing, list(incoming or []))
    path = save_daily(
        market,
        code,
        merged,
        data_source=data_source,
        stock_code=stock_code,
        store_dir=store_dir,
        adjust_policy=policy,
        assess_quality=assess_quality,
        trim_daily_bars=trim_daily_bars,
    )
    return path, merged


def peek_daily_meta(
    market: str,
    code: str,
    store_dir: str,
) -> Optional[Dict[str, Any]]:
    mkt = market.upper()
    code_s = str(code)
    conn = get_conn(store_dir)
    row = conn.execute(
        """
        SELECT * FROM daily_cache_meta
        WHERE market=? AND code=?
        ORDER BY CASE adjust_policy WHEN 'qfq' THEN 0 ELSE 1 END, fetched_at DESC
        LIMIT 1
        """,
        (mkt, code_s),
    ).fetchone()
    if row is None:
        return None
    quality = row["quality"]
    if isinstance(quality, str) and quality:
        try:
            quality = json.loads(quality)
        except json.JSONDecodeError:
            quality = None
    return {
        "market": row["market"] or mkt,
        "code": row["code"] or code_s,
        "stock_code": row["stock_code"],
        "data_source": row["data_source"],
        "fetched_at": row["fetched_at"],
        "quality": quality,
        "bar_count": row["bar_count"],
        "date_min": row["date_min"],
        "date_max": row["date_max"],
        "adjust_policy": row["adjust_policy"],
        "bars_backend": "sqlite",
    }


def list_daily_symbols(
    market: Optional[str],
    store_dir: str,
) -> List[Dict[str, Any]]:
    conn = get_conn(store_dir)
    if market:
        rows = conn.execute(
            "SELECT * FROM daily_cache_meta WHERE market=? ORDER BY code",
            (market.upper(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM daily_cache_meta ORDER BY market, code"
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        quality = row["quality"]
        if isinstance(quality, str) and quality:
            try:
                quality = json.loads(quality)
            except json.JSONDecodeError:
                quality = None
        out.append(
            {
                "market": row["market"],
                "code": row["code"],
                "stock_code": row["stock_code"],
                "data_source": row["data_source"],
                "fetched_at": row["fetched_at"],
                "quality": quality,
                "bars_backend": "sqlite",
            }
        )
    return out


def clear_daily(market: Optional[str], store_dir: str) -> int:
    """删除日线 bars + meta；返回删除的 meta 行数。"""
    conn = get_conn(store_dir)
    with _write_lock:
        if market:
            mkt = market.upper()
            cur = conn.execute(
                "SELECT COUNT(*) FROM daily_cache_meta WHERE market=?", (mkt,)
            )
            n = int(cur.fetchone()[0])
            conn.execute("DELETE FROM daily_bars WHERE market=?", (mkt,))
            conn.execute("DELETE FROM daily_cache_meta WHERE market=?", (mkt,))
        else:
            cur = conn.execute("SELECT COUNT(*) FROM daily_cache_meta")
            n = int(cur.fetchone()[0])
            conn.execute("DELETE FROM daily_bars")
            conn.execute("DELETE FROM daily_cache_meta")
        conn.commit()
    return n


def clear_minute(
    market: Optional[str] = None,
    *,
    period: Optional[str] = None,
    store_dir: str,
) -> int:
    conn = get_conn(store_dir)
    with _write_lock:
        clauses = []
        args: List[Any] = []
        if market:
            clauses.append("market=?")
            args.append(market.upper())
        if period:
            clauses.append("period=?")
            args.append(str(period))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = conn.execute(f"SELECT COUNT(*) FROM minute_cache_meta{where}", args)
        n = int(cur.fetchone()[0])
        conn.execute(f"DELETE FROM minute_bars{where}", args)
        conn.execute(f"DELETE FROM minute_cache_meta{where}", args)
        conn.commit()
    return n


def _minute_meta_row(
    conn: sqlite3.Connection, mkt: str, code_s: str, per: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM minute_cache_meta
        WHERE market=? AND code=? AND period=?
        ORDER BY CASE adjust_policy WHEN 'qfq' THEN 0 ELSE 1 END, fetched_at DESC
        LIMIT 1
        """,
        (mkt, code_s, per),
    ).fetchone()


def _minute_bar_dicts(rows: List[sqlite3.Row]) -> List[dict]:
    return [
        {
            "datetime": r["datetime"],
            "date": r["date"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
        }
        for r in rows
    ]


def load_minute(
    market: str,
    code: str,
    period: str,
    *,
    min_bars: int,
    max_age_hours: float,
    store_dir: str,
    ignore_age: bool,
) -> Optional[Tuple[List[dict], Dict[str, Any]]]:
    mkt = market.upper()
    code_s = str(code)
    per = str(period or "5")
    conn = get_conn(store_dir)
    # 单连接跨线程：读也必须与写串行，否则 SELECT 可能空结果 → 增量补齐把长历史整表覆盖掉
    with _write_lock:
        row = _minute_meta_row(conn, mkt, code_s, per)
        if row is None:
            return None
        fetched_at = _parse_fetched(row["fetched_at"]) or datetime.now()
        if not ignore_age and max_age_hours > 0:
            if datetime.now() - fetched_at > timedelta(hours=max_age_hours):
                return None
        policy = row["adjust_policy"] or "qfq"
        bars_rows = conn.execute(
            """
            SELECT datetime, date, open, high, low, close, volume
            FROM minute_bars
            WHERE market=? AND code=? AND period=? AND adjust_policy=?
            ORDER BY datetime
            """,
            (mkt, code_s, per, policy),
        ).fetchall()
        bars = _minute_bar_dicts(list(bars_rows))
        if len(bars) < min_bars:
            return None
        meta = {
            "market": row["market"] or mkt,
            "code": row["code"] or code_s,
            "stock_code": row["stock_code"],
            "period": per,
            "data_source": row["data_source"] or "cache",
            "fetched_at": fetched_at.isoformat(timespec="seconds"),
            "from_cache": True,
            "date_min": row["date_min"],
            "date_max": row["date_max"],
            "bar_count": row["bar_count"] or len(bars),
            "adjust_policy": policy,
            "bars_backend": "sqlite",
        }
        return bars, meta


def load_minute_span_snapshot(
    market: str,
    code: str,
    period: str,
    *,
    store_dir: str,
) -> Optional[Dict[str, Any]]:
    """覆盖状态用：不拉全历史，只取跨度 + 末日 bars。"""
    mkt = market.upper()
    code_s = str(code)
    per = str(period or "5")
    conn = get_conn(store_dir)
    with _write_lock:
        row = _minute_meta_row(conn, mkt, code_s, per)
        if row is None:
            return None
        policy = row["adjust_policy"] or "qfq"
        stats = conn.execute(
            """
            SELECT COUNT(DISTINCT date) AS span_days,
                   COUNT(*) AS bar_count,
                   MIN(date) AS date_min,
                   MAX(date) AS date_max
            FROM minute_bars
            WHERE market=? AND code=? AND period=? AND adjust_policy=?
            """,
            (mkt, code_s, per, policy),
        ).fetchone()
        span_days = int((stats["span_days"] if stats else 0) or 0)
        if span_days < 1:
            return None
        date_max = str((stats["date_max"] if stats else "") or "")[:10] or None
        day_rows = []
        if date_max:
            day_rows = conn.execute(
                """
                SELECT datetime, date, open, high, low, close, volume
                FROM minute_bars
                WHERE market=? AND code=? AND period=? AND adjust_policy=? AND date=?
                ORDER BY datetime
                """,
                (mkt, code_s, per, policy, date_max),
            ).fetchall()
        fetched_at = _parse_fetched(row["fetched_at"])
        return {
            "span_days": span_days,
            "bar_count": int((stats["bar_count"] if stats else 0) or 0),
            "fetched_at": fetched_at.isoformat(timespec="seconds") if fetched_at else (row["fetched_at"] or None),
            "date_min": str((stats["date_min"] if stats else "") or "")[:10] or None,
            "date_max": date_max,
            "date_max_bars": _minute_bar_dicts(list(day_rows)),
            "adjust_policy": policy,
            "bars_backend": "sqlite",
        }


def save_minute(
    market: str,
    code: str,
    bars: List[dict],
    *,
    period: str,
    data_source: str,
    stock_code: Optional[str],
    store_dir: str,
    adjust_policy: Optional[str],
    trim_minute_bars,
) -> str:
    from core.data.policy import MINUTE_BARS_MAX_KEEP

    mkt = market.upper()
    code_s = str(code)
    per = str(period or "5")
    policy = _norm_policy(adjust_policy)
    bars = trim_minute_bars(list(bars or []))
    path = db_path(store_dir)
    if not bars:
        return path
    fetched_at = datetime.now()
    fetched_s = fetched_at.isoformat(timespec="seconds")
    conn = get_conn(store_dir)
    touch_days = sorted(
        {
            str(b.get("date") or str(b.get("datetime") or "")[:10]).strip()[:10]
            for b in bars
            if isinstance(b, dict)
            and len(str(b.get("date") or str(b.get("datetime") or "")[:10]).strip()[:10])
            == 10
        }
    )
    with _write_lock:
        if touch_days:
            from core.store import drop_thinner_minute_days

            placeholders = ",".join("?" * len(touch_days))
            rows = conn.execute(
                f"""
                SELECT datetime, date FROM minute_bars
                WHERE market=? AND code=? AND period=? AND adjust_policy=?
                  AND date IN ({placeholders})
                """,
                (mkt, code_s, per, policy, *touch_days),
            ).fetchall()
            existing = [
                {"datetime": r["datetime"], "date": r["date"]} for r in rows
            ]
            bars = drop_thinner_minute_days(existing, bars, period=per)
            touch_days = sorted(
                {
                    str(b.get("date") or str(b.get("datetime") or "")[:10]).strip()[:10]
                    for b in bars
                    if isinstance(b, dict)
                    and len(
                        str(b.get("date") or str(b.get("datetime") or "")[:10]).strip()[:10]
                    )
                    == 10
                }
            )
        if not bars:
            return path
        else:
            # 只替换 incoming 覆盖到、且时刻不少于本地的交易日
            if touch_days:
                placeholders = ",".join("?" * len(touch_days))
                conn.execute(
                    f"""
                    DELETE FROM minute_bars
                    WHERE market=? AND code=? AND period=? AND adjust_policy=?
                      AND date IN ({placeholders})
                    """,
                    (mkt, code_s, per, policy, *touch_days),
                )
            conn.executemany(
                """
                INSERT OR REPLACE INTO minute_bars
                (code, market, datetime, date, open, high, low, close, volume,
                 adjust_policy, period, data_source, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        code_s,
                        mkt,
                        str(b.get("datetime") or b.get("date") or ""),
                        b.get("date") or str(b.get("datetime") or "")[:10],
                        b.get("open"),
                        b.get("high"),
                        b.get("low"),
                        b.get("close"),
                        b.get("volume"),
                        policy,
                        per,
                        data_source,
                        fetched_s,
                    )
                    for b in bars
                    if (b.get("datetime") or b.get("date"))
                ],
            )
            total = conn.execute(
                """
                SELECT COUNT(*) AS n FROM minute_bars
                WHERE market=? AND code=? AND period=? AND adjust_policy=?
                """,
                (mkt, code_s, per, policy),
            ).fetchone()
            n_keep = int(total["n"] if total else 0)
            max_keep = max(1, int(MINUTE_BARS_MAX_KEEP))
            extra = n_keep - max_keep
            if extra > 0:
                # WITHOUT ROWID 表无 rowid；按 datetime 切掉最旧 extra 根
                cut = conn.execute(
                    """
                    SELECT datetime FROM minute_bars
                    WHERE market=? AND code=? AND period=? AND adjust_policy=?
                    ORDER BY datetime
                    LIMIT 1 OFFSET ?
                    """,
                    (mkt, code_s, per, policy, extra),
                ).fetchone()
                if cut and cut["datetime"]:
                    conn.execute(
                        """
                        DELETE FROM minute_bars
                        WHERE market=? AND code=? AND period=? AND adjust_policy=?
                          AND datetime < ?
                        """,
                        (mkt, code_s, per, policy, cut["datetime"]),
                    )
        stats = conn.execute(
            """
            SELECT COUNT(*) AS bar_count, MIN(date) AS date_min, MAX(date) AS date_max
            FROM minute_bars
            WHERE market=? AND code=? AND period=? AND adjust_policy=?
            """,
            (mkt, code_s, per, policy),
        ).fetchone()
        bar_count = int((stats["bar_count"] if stats else 0) or 0)
        date_min = str((stats["date_min"] if stats else "") or "")[:10] or None
        date_max = str((stats["date_max"] if stats else "") or "")[:10] or None
        conn.execute(
            """
            INSERT OR REPLACE INTO minute_cache_meta
            (market, code, period, adjust_policy, stock_code, data_source,
             fetched_at, bar_count, date_min, date_max)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mkt,
                code_s,
                per,
                policy,
                stock_code or code_s,
                data_source,
                fetched_s,
                bar_count,
                date_min,
                date_max,
            ),
        )
        conn.commit()
    return path


def touch_daily_fetched_at(
    market: str,
    code: str,
    fetched_at: datetime,
    *,
    store_dir: str,
    adjust_policy: Optional[str] = "qfq",
) -> bool:
    """测试辅助：改写 meta.fetched_at。"""
    mkt = market.upper()
    code_s = str(code)
    policy = _norm_policy(adjust_policy)
    conn = get_conn(store_dir)
    with _write_lock:
        cur = conn.execute(
            """
            UPDATE daily_cache_meta SET fetched_at=?
            WHERE market=? AND code=? AND adjust_policy=?
            """,
            (fetched_at.isoformat(timespec="seconds"), mkt, code_s, policy),
        )
        conn.commit()
        return cur.rowcount > 0
