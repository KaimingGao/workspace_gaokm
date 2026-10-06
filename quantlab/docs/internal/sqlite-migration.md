## 日线/分钟线缓存 SQLite 改造方案

[← 文档索引](../README.md) · [架构总览](../architecture.md) · [工程结构轨](./engineering-track.md#工程结构轨a0a4)

> 状态：**已落地（A1）** · 双后端 `QUANTLAB_BARS_BACKEND=sqlite|json`（默认 sqlite）  
> 范围：`core/store.py` + `core/store_bars_sqlite.py`；配置与账本保持 JSON  
> 迁移：`python3 scripts/migrate_bars_to_sqlite.py [--dry-run]`（不删原 JSON）  
> 工程轨：[§ 工程结构轨 A0–A4](./engineering-track.md#工程结构轨a0a4)

## 一、背景与目标

### 现状痛点

1. **跨票/跨日聚合读慢**：因子 IC、panel 回归、组合回测需扫 `data/store/daily/**/*.json`（~100 文件），逐个 `json.load` 再内存合并，无索引可用。
2. **Web 并发读 vs cron/回测并发写互扰**：`atomic_write_json` 防覆盖但不防"读半成品"，且文件级写时读侧可能拿到空文件。
3. **JSONL 日志增长后扫描慢**（二期再议，本次不含）。

### 目标

- 跨票/跨日查询从"扫 N 文件"降为单条 SQL
- Web 读与 cron 写真正并发（WAL：读不阻塞写）
- **上层零改动**——被 `core/ports` 隔离，DataService / signal / backtest / paper 无感

### 非目标

- 不迁配置类 JSON（`paper.json` / `watching.json` / `signal_config.json`）：人审可改、git diff、写频低
- 不迁基本面/资讯快照：按标的读、单文件不大、PIT 已收口 `fundamentals_pit`
- 不引入 Redis / Postgres：单机单用户阶段过度设计
- 不接 Tick / Level-2：公开免费源不提供，当前阶段非必需

## 二、实际调用链与改动面

### 调用链（已核对）

```
DataService.get_bars (core/data/facade.py:99)
  → core.ports.market.fetch_daily_bars          ← port 分发
    → adapters.market.history.fetch_daily_bars     ← adapter（ports_bind 绑定）
      → core.store.{peek / load / merge / save}_daily_cache
```

DataService 只从 store 导入纯函数 `assess_quality`，bars 读写全走 ports。**DataService 无感靠的是 ports 隔离**，不是 store 内部改了它就自动无感。

### store 的 bar 函数全部调用方（已穷举）

| 调用方 | 用到的函数 | 读取的 meta 字段 |
|------|------|------|
| `adapters/market/history.py:310-411` | peek / load / merge / save | `adjust_policy`(L336)、`data_source`(L349) |
| `adapters/market/minute_history.py:14` | minute load/save/merge | 同上 |
| `core/data_coverage.py:82,96` | peek_daily_cache_meta | `quality`、`fetched_at`、`bar_count`(L111-114) |
| `research/cache_cli.py:56` | list_cached_symbols | `market/code/quality/data_source/fetched_at`(L62-65) |

### 契约风险核查结论

| 契约项 | 风险 | 结论 |
|------|------|------|
| `save_daily_cache` 返回 `path:str` | history.py L411-418 未捕获返回值 | 可自由改 |
| `peek_daily_cache_meta` 返回 `path` 字段 | data_coverage 只读 quality/fetched_at/bar_count | **无人用，可砍** |
| `list_cached_symbols` 返回 `path` 字段 | cache_cli L62-65 不读 path | **无人用，可砍** |
| `daily_cache_path` / `minute_cache_path` | 仅 store.py 内部调用，无外部引用 | 可重构 |
| `merge_bars_by_date` | 纯函数（existing+incoming→list） | 保留，upsert 在 save 内做 |

### 唯一外部契约泄漏

`research/cache_cli.py:39-53` 的 `--clear` **绕过 store 直接扫盘删 .json**：

```python
base = os.path.join(store, "daily")
for name in os.listdir(mdir):
    if name.endswith(".json"):
        os.remove(os.path.join(mdir, name))
```

迁库后失效，**必须同步收口**（见第六节）。

## 三、SQLite 建表 DDL

库文件路径：`{store_dir}/bars.db`（保留 `get_store_dir()` / `QUANTLAB_STORE_DIR` 语义，目录下多一个 db 文件）。

```sql
PRAGMA journal_mode=WAL;          -- 并发读不阻塞写，解决"并发查询"的关键
PRAGMA synchronous=NORMAL;        -- WAL 下安全，兼顾性能
PRAGMA busy_timeout=5000;         -- 写锁等待 5s，避免瞬时冲突报错
PRAGMA foreign_keys=ON;

-- 日线 OHLCV（每行一根 K 线）
CREATE TABLE IF NOT EXISTS daily_bars (
  code          TEXT NOT NULL,             -- 裸代码（600519 / 00700 / AAPL）
  market        TEXT NOT NULL,             -- CN / HK / US
  date          TEXT NOT NULL,             -- YYYY-MM-DD
  open          REAL,
  high          REAL,
  low           REAL,
  close         REAL NOT NULL,
  volume        REAL,
  adjust_policy TEXT NOT NULL,             -- qfq / raw / hfq，防混用
  data_source   TEXT,                      -- 写入批次的源（akshare_cn_daily:qfq 等）
  fetched_at    TEXT NOT NULL,             -- ISO 字符串，该行入库时间
  PRIMARY KEY (code, date, adjust_policy, market)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_bars(date);
CREATE INDEX IF NOT EXISTS idx_daily_code ON daily_bars(code, adjust_policy);

-- 日线缓存元数据（每 code+policy 一行；支撑 peek/list O(1)）
CREATE TABLE IF NOT EXISTS daily_cache_meta (
  market        TEXT NOT NULL,
  code          TEXT NOT NULL,
  adjust_policy TEXT NOT NULL,
  stock_code    TEXT,                      -- 带前缀展示码（sh600519）
  data_source   TEXT,
  fetched_at    TEXT NOT NULL,             -- 最近一次 save 时间
  quality       TEXT,                      -- assess_quality 的 JSON 串
  bar_count     INTEGER,
  date_min      TEXT,
  date_max      TEXT,
  PRIMARY KEY (market, code, adjust_policy)
);

-- 分钟线（结构对称）
CREATE TABLE IF NOT EXISTS minute_bars (
  code          TEXT NOT NULL,
  market        TEXT NOT NULL,
  datetime      TEXT NOT NULL,             -- YYYY-MM-DD HH:MM（或原 datetime 字段）
  date          TEXT,                      -- 派生：datetime[:10]，便于按日聚合
  open          REAL, high REAL, low REAL, close REAL, volume REAL,
  adjust_policy TEXT NOT NULL,
  period        TEXT NOT NULL,             -- "5" 等
  data_source   TEXT,
  fetched_at    TEXT NOT NULL,
  PRIMARY KEY (code, datetime, period, adjust_policy, market)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_minute_date ON minute_bars(date);

CREATE TABLE IF NOT EXISTS minute_cache_meta (
  market TEXT NOT NULL, code TEXT NOT NULL,
  period TEXT NOT NULL, adjust_policy TEXT NOT NULL,
  stock_code TEXT, data_source TEXT, fetched_at TEXT NOT NULL,
  bar_count INTEGER, date_min TEXT, date_max TEXT,
  PRIMARY KEY (market, code, period, adjust_policy)
);
```

### 设计要点

- `adjust_policy` 进主键：复刻 `history.py:336` "缓存 policy 与请求不一致则跳过"的防混用语义
- 单独 `*_cache_meta` 表：让 `peek_daily_cache_meta` / `list_cached_symbols` 是 O(1) 而非扫全表
- `quality` 存 JSON 串：嵌套 dict（level/bar_count/first_date/last_date/notes），整体读整体写，规范化收益低
- `WITHOUT ROWID`：主键即聚簇索引，省空间且按 (code,date) 范围查询更快

## 四、meta 字段映射表

### load_daily_cache 返回的 meta dict（契约必须逐字段对齐）

| meta 字段 | 当前来源（JSON payload） | 新来源（SQLite） | 备注 |
|-----------|------------------------|------------------|------|
| `market` | payload.market | daily_cache_meta.market | 直读 |
| `code` | payload.code | daily_cache_meta.code | 直读 |
| `stock_code` | payload.stock_code | daily_cache_meta.stock_code | 直读 |
| `data_source` | payload.data_source | daily_cache_meta.data_source | 直读 |
| `fetched_at` | payload.fetched_at（ISO） | daily_cache_meta.fetched_at | 保持 ISO 字符串 |
| `quality` | payload.quality（dict） | `json.loads(daily_cache_meta.quality)` | 出库反序列化 |
| `from_cache` | 硬编码 True | 硬编码 True | 不变 |
| `date_min` | payload.date_min | daily_cache_meta.date_min | 直读 |
| `date_max` | payload.date_max | daily_cache_meta.date_max | 直读 |
| `bar_count` | payload.bar_count | daily_cache_meta.bar_count | 直读 |
| `adjust_policy` | payload.adjust_policy | daily_cache_meta.adjust_policy | 直读 |

### peek_daily_cache_meta 返回 dict

| 字段 | 当前 | 新 | 备注 |
|------|------|----|------|
| `path` | 文件路径 | **删除**（无调用方使用，已核对） | 唯一可安全砍的字段 |
| 其余字段 | payload 直读 | meta 表直读 | 全部保留 |

调用方核对：`data_coverage.py:111-114` 只读 quality/fetched_at/bar_count；`history.py:336` 只读 adjust_policy。砍 `path` 零影响。

### list_cached_symbols 返回 list[dict]

| 字段 | 当前 | 新 | 备注 |
|------|------|----|------|
| `path` | 文件路径 | **删除**（cache_cli L62-65 不读） | 安全砍 |
| 其余字段 | payload 直读 | `SELECT ... FROM daily_cache_meta` | 保留 |

## 五、store.py 函数逐个改造（签名不变）

| 函数 | 当前实现 | 新实现 | 签名变化 |
|------|---------|--------|---------|
| `load_daily_cache` | open JSON → 校验 TTL → 返回 (bars, meta) | 查 meta 表校验 fetched_at TTL → 查 daily_bars `WHERE code=? AND adjust_policy=? ORDER BY date` → 组装 meta | **无** |
| `save_daily_cache` | 组装 payload → atomic_write_json → 返回 path | assess_quality → `INSERT OR REPLACE` 进 daily_bars → upsert daily_cache_meta → 返回 db 路径 | **无**（返回值无人用） |
| `peek_daily_cache_meta` | open JSON → 取字段 | `SELECT * FROM daily_cache_meta WHERE market=? AND code=?` | **无** |
| `list_cached_symbols` | os.listdir + 逐文件 json.load | `SELECT * FROM daily_cache_meta [WHERE market=?]` | **无** |
| `merge_bars_by_date` | 纯函数 | **不改**（upsert 在 save 内用 `INSERT OR REPLACE`） | 无 |
| `load_minute_cache` / `save_minute_cache` | JSON | 对称改造，走 minute_bars / minute_cache_meta | **无** |
| `merge_minute_bars_by_time` | 纯函数；默认同日整段替换（`lock_calendar_day`） | **已改**（禁同日跨源缝合） | 无 |
| `daily_cache_path` / `minute_cache_path` | 算文件路径 | 内部不再用于存储，保留导出用途 | 无 |

### save_daily_cache 内部 upsert 逻辑（伪代码）

```python
def save_daily_cache(market, code, bars, *, data_source, stock_code=None,
                     store_dir=None, adjust_policy="qfq"):
    conn = _conn(store_dir)
    fetched_at = datetime.now().isoformat(timespec="seconds")
    quality = assess_quality(bars, data_source=data_source, fetched_at=datetime.now())
    date_min = bars[0].get("date") if bars else None
    date_max = bars[-1].get("date") if bars else None
    policy = adjust_policy or "qfq"

    with _write_lock:  # 串行写，WAL 下读不阻塞
        conn.executemany(
            """INSERT OR REPLACE INTO daily_bars
               (code,market,date,open,high,low,close,volume,adjust_policy,data_source,fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [(code, market, b.get("date"), b.get("open"), b.get("high"),
              b.get("low"), b.get("close"), b.get("volume"), policy, data_source, fetched_at)
             for b in (bars or [])]
        )
        conn.execute(
            """INSERT OR REPLACE INTO daily_cache_meta
               (market,code,adjust_policy,stock_code,data_source,fetched_at,
                quality,bar_count,date_min,date_max)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (market, code, policy, stock_code or code, data_source, fetched_at,
             json.dumps(quality), len(bars or []), date_min, date_max)
        )
        conn.commit()
    return _db_path(store_dir)  # 返回值无调用方，保留仅为兼容
```

## 六、连接管理（并发核心）

```python
import sqlite3, threading
_conn_cache: Dict[str, sqlite3.Connection] = {}
_write_lock = threading.Lock()

def _conn(store_dir: Optional[str] = None) -> sqlite3.Connection:
    path = _db_path(store_dir)  # {store_dir}/bars.db
    if path in _conn_cache:
        return _conn_cache[path]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=5000")
    _conn_cache[path] = c
    return c
```

**要点**：
- `check_same_thread=False`：Web 多线程共享一个连接（SQLite 驱动层自带串行化）
- `_write_lock`：进程内串行写；WAL 模式下读不受影响——这才是真正解决"Web 并发读 vs cron 写"的关键
- 不引入连接池：单机单 db，一个连接足够；引入池反而增加锁竞争

## 七、迁移脚本设计

新增 `scripts/migrate_bars_to_sqlite.py`，一次性运行。

```python
# 伪代码骨架
def migrate(store_dir):
    conn = _conn(store_dir)
    _create_tables(conn)
    # 扫 daily JSON
    daily_base = os.path.join(store_dir, "daily")
    for market in os.listdir(daily_base):
        mdir = os.path.join(daily_base, market)
        for fname in os.listdir(mdir):
            if not fname.endswith(".json"):
                continue
            payload = json.load(open(os.path.join(mdir, fname)))
            code = payload["code"]
            policy = payload.get("adjust_policy") or "qfq"
            bars = payload.get("bars") or []
            conn.executemany("INSERT OR REPLACE INTO daily_bars (...) VALUES (...)", [...])
            conn.execute("INSERT OR REPLACE INTO daily_cache_meta (...) VALUES (...)", ...)
    # minute 同理
    conn.commit()
```

### 关键约束

- **幂等**：全部用 `INSERT OR REPLACE`，重复运行不报错、不重复
- **不删原 JSON**：迁移后 `data/store/daily/**/*.json` 原地保留作回滚备份；待运行 1-2 周稳定后再清理
- **进度输出**：每 50 个 code 打印一行，便于观察卡住
- **校验**：迁移完跑 `SELECT COUNT(*) FROM daily_bars` vs 文件数 × 平均 bar 数，对得上才通过
- **--dry-run**：加参数只统计不写入，先预估规模

## 八、cache_cli 收口（唯一外部契约泄漏）

当前 `research/cache_cli.py:39-53` 的 `--clear` 绕过 store 直接 `os.remove` 删 JSON，迁库后失效。

### 改法

1. store.py 新增 `clear_daily_cache(market: Optional[str] = None, store_dir=None) -> int`
   - 实现：`DELETE FROM daily_bars [WHERE market=?]` + `DELETE FROM daily_cache_meta [WHERE market=?]`，返回删除行数
2. cache_cli.py 的 `--clear` 分支改为调 `clear_daily_cache(market=args.market)`
3. `--list` 已走 `list_cached_symbols`，无需改

## 九、风险与回滚

| 风险 | 缓解 |
|------|------|
| SQLite 改造引入 bug 导致 bars 读不到 | 迁移脚本不删原 JSON；store.py 加环境变量 `QUANTLAB_BARS_BACKEND=sqlite\|json`，出问题切回 json（保留旧函数为 `_json` 后缀） |
| WAL 文件增长 | 定期 `PRAGMA wal_checkpoint(TRUNCATE)`；或在 save 后偶发 checkpoint |
| 并发写死锁 | `_write_lock` + `busy_timeout=5000` 双保险；实测无死锁 |
| 测试 mock 点失效 | mock 点在 `adapters.market.history.load_daily_cache`（test_history.py:51），store 内部改实现不影响 mock 路径 |
| meta 字段遗漏 | 上表已逐字段核对调用方；test_store.py 直测会兜底 |

### 回滚路径

`git revert store.py + cache_cli.py` 即可，数据层因原 JSON 保留而无损。

## 十、改动清单总览

| 文件 | 改动 | 规模 |
|------|------|------|
| `core/store.py` | 6 函数内部重写 + 新增 `_conn`/`_db_path`/`clear_daily_cache`/建表 | ~250 行 |
| `research/cache_cli.py` | `--clear` 改调 store 接口 | ~10 行 |
| `scripts/migrate_bars_to_sqlite.py` | 新增迁移脚本 | ~80 行 |
| `adapters/market/history.py` | **不改** | 0 |
| `adapters/market/minute_history.py` | **不改** | 0 |
| `core/data/facade.py` / ports / signal / backtest / paper | **不改** | 0 |
| 测试 | test_store.py 直测需过；其余 mock 点不变 | 验证 |

## 十一、决策点（已拍板 · A1）

1. `quality` 存 JSON 串 — **采用**
2. `QUANTLAB_BARS_BACKEND` 双后端 — **采用**（默认 `sqlite`，可切 `json`）
3. 分钟线同步迁 — **采用**（与日线同库）

