## 日线/分钟线缓存 SQLite 改造方案

[← 文档索引](../README.md) · [架构总览](../architecture.md) · [工程结构轨](./engineering-track.md#工程结构轨a0a4-b0b4)

> 状态：**已落地（A1）** · 双后端 `QUANTLAB_BARS_BACKEND=sqlite|json`（默认 sqlite）  
> 范围：`core/store.py` + `core/store_bars_sqlite.py`；配置与账本保持 JSON  
> 迁移：`python3 scripts/migrate_bars_to_sqlite.py [--dry-run]`（不删原 JSON）  
> 工程轨：[§ 工程结构轨 A0–A4](./engineering-track.md#工程结构轨a0a4-b0b4)

## 一、背景与目标

### 现状痛点

1. **跨票/跨日聚合读慢**：因子 IC、panel 回归、组合回测需扫 `data/store/daily/**/*.json`（~100 文件），逐个 `json.load` 再内存合并，无索引。
2. **Web 并发读 vs cron/回测并发写互扰**：`atomic_write_json` 防覆盖但不防读半成品；JSONL 日志增长后扫描慢（二期再议，本次不含）。

### 目标

- 跨票/跨日查询从「扫 N 文件」降为单条 SQL；Web 读与 cron 写真正并发（WAL：读不阻塞写）
- **上层零改动**——被 `core/ports` 隔离，DataService / signal / backtest / paper 无感

### 非目标

- 不迁配置类 JSON（`paper.json` / `watching.json` / `signal_config.json`）：人审可改、git diff、写频低
- 不迁基本面/资讯快照：按标的读、单文件不大、PIT 已收口 `fundamentals_pit`
- 不引入 Redis / Postgres（单机单用户阶段过度设计）；不接 Tick / Level-2（公开免费源不提供）

## 二、实际调用链与改动面

### 调用链（已核对）

```text
DataService.get_bars (core/data/facade.py)
  → core.ports.market.fetch_daily_bars              ← port 分发
    → adapters.market.history.fetch_daily_bars      ← adapter（ports_bind 绑定）
      → core.store.{peek / load / merge / save}_daily_cache
```

DataService 只从 store 导入纯函数 `assess_quality`，bars 读写全走 ports；**上层无感靠 ports 隔离**。

### store 的 bar 函数全部调用方（已穷举）

| 调用方 | 用到的函数 | 读取的 meta 字段 |
|------|------|------|
| `adapters/market/history.py:310-411` | peek / load / merge / save | `adjust_policy`(L336)、`data_source`(L349) |
| `adapters/market/minute_history.py:14` | minute load/save/merge | 同上 |
| `core/data/coverage.py:82,96` | peek_daily_cache_meta | `quality`、`fetched_at`、`bar_count`(L111-114) |
| `core/research_universe.py:306` | list_cached_symbols | `market/code/quality/data_source/fetched_at` |

### 契约风险核查结论

| 契约项 | 风险 | 结论 |
|------|------|------|
| `save_daily_cache` 返回 `path:str` | history.py 未捕获返回值 | 可自由改 |
| `peek_daily_cache_meta` / `list_cached_symbols` 返回 `path` | 调用方只读 quality/fetched_at/bar_count，不读 path | **无人用，可砍** |
| `daily_cache_path` / `minute_cache_path` | 仅 store.py 内部调用，无外部引用 | 可重构 |
| `merge_bars_by_date` | 纯函数（existing+incoming→list） | 保留，upsert 在 save 内做 |

### 唯一外部契约泄漏

旧研究缓存 CLI 的 `--clear` 曾绕过 store 直接扫盘删 `.json`，迁库后失效；现已收口为 `core/store.py` 的 `clear_daily_cache`（见第八节）。

## 三、SQLite 建表 DDL

库文件路径：`{store_dir}/bars.db`（保留 `get_store_dir()` / `QUANTLAB_STORE_DIR` 语义，目录下多一个 db 文件）。

```sql
PRAGMA journal_mode=WAL;          -- 并发读不阻塞写
PRAGMA synchronous=NORMAL;        -- WAL 下安全，兼顾性能
PRAGMA busy_timeout=5000;         -- 写锁等待 5s

-- 日线 OHLCV（每行一根 K 线；code 为裸代码 600519 / 00700 / AAPL）
CREATE TABLE IF NOT EXISTS daily_bars (
  code TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL NOT NULL, volume REAL,
  adjust_policy TEXT NOT NULL,             -- qfq / raw / hfq，防混用
  data_source TEXT,                        -- 写入批次的源
  fetched_at TEXT NOT NULL,                -- 该行入库时间
  PRIMARY KEY (code, date, adjust_policy, market)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_bars(date);
CREATE INDEX IF NOT EXISTS idx_daily_code ON daily_bars(code, adjust_policy);

-- 日线缓存元数据（每 code+policy 一行；支撑 peek/list O(1)）
CREATE TABLE IF NOT EXISTS daily_cache_meta (
  market TEXT NOT NULL, code TEXT NOT NULL, adjust_policy TEXT NOT NULL,
  stock_code TEXT,                         -- 带前缀展示码（sh600519）
  data_source TEXT, fetched_at TEXT NOT NULL,  -- 最近一次 save 时间
  quality TEXT,                            -- assess_quality 的 JSON 串
  bar_count INTEGER, date_min TEXT, date_max TEXT,
  PRIMARY KEY (market, code, adjust_policy)
);

-- 分钟线（结构对称；date 派生 datetime[:10]）
CREATE TABLE IF NOT EXISTS minute_bars (
  code TEXT NOT NULL, market TEXT NOT NULL, datetime TEXT NOT NULL,
  date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
  adjust_policy TEXT NOT NULL,
  period TEXT NOT NULL,                    -- "5" 等
  data_source TEXT, fetched_at TEXT NOT NULL,
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

- `adjust_policy` 进主键：复刻「缓存 policy 与请求不一致则跳过」的防混用语义
- 单独 `*_cache_meta` 表：让 `peek_daily_cache_meta` / `list_cached_symbols` 是 O(1) 而非扫全表
- `quality` 存 JSON 串（嵌套 dict 整体读整体写）；`WITHOUT ROWID`（主键即聚簇索引，省空间且按 (code,date) 范围查询更快）

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

删除 `path` 字段（唯一可安全砍的字段，调用方只读 quality/fetched_at/bar_count）；其余字段由 meta 表直读。

### list_cached_symbols 返回 list[dict]

删除 `path` 字段；其余字段 `SELECT ... FROM daily_cache_meta`（可按 market 过滤）。

## 五、store.py 函数逐个改造（签名不变）

| 函数 | 当前实现 | 新实现 | 签名变化 |
|------|---------|--------|---------|
| `load_daily_cache` | open JSON → 校验 TTL → 返回 (bars, meta) | 查 meta 表校验 fetched_at TTL → 查 daily_bars `WHERE code=? AND adjust_policy=? ORDER BY date` → 组装 meta | **无** |
| `save_daily_cache` | 组装 payload → atomic_write_json → 返回 path | assess_quality → `INSERT OR REPLACE` 进 daily_bars → upsert daily_cache_meta → 返回 db 路径 | **无**（返回值无人用） |
| `peek_daily_cache_meta` | open JSON → 取字段 | `SELECT * FROM daily_cache_meta WHERE market=? AND code=?` | **无** |
| `list_cached_symbols` | os.listdir + 逐文件 json.load | `SELECT * FROM daily_cache_meta [WHERE market=?]` | **无** |
| `merge_bars_by_date` | 纯函数 | **不改**（upsert 在 save 内做） | 无 |
| `load_minute_cache` / `save_minute_cache` | JSON | 对称改造，走 minute_bars / minute_cache_meta | **无** |
| `merge_minute_bars_by_time` | 纯函数；默认同日整段替换（`lock_calendar_day`） | **已改**（禁同日跨源缝合） | 无 |
| `daily_cache_path` / `minute_cache_path` | 算文件路径 | 内部不再用于存储，保留导出用途 | 无 |

### save_daily_cache 内部 upsert 逻辑（伪代码）

在一个 `_write_lock` 内完成两件事：`INSERT OR REPLACE INTO daily_bars`（逐 bar，带 `adjust_policy` / `data_source` / `fetched_at`）+ `INSERT OR REPLACE INTO daily_cache_meta`（`quality` JSON 串、`bar_count`、`date_min/max`），最后 `commit` 并返回 db 路径（无调用方）。

## 六、连接管理（并发核心）

- 按 db 路径缓存**单个** `sqlite3.Connection`，`check_same_thread=False`（Web 多线程共享，驱动层自带串行化）；建立后设 WAL / `synchronous=NORMAL` / `busy_timeout=5000`
- 进程内 `threading.Lock` 串行写；WAL 下读不阻塞写——这才是解决「Web 并发读 vs cron 写」的关键
- 不引入连接池：单机单 db，一个连接足够，池反而增加锁竞争

## 七、迁移脚本设计

`scripts/migrate_bars_to_sqlite.py`，一次性运行：

- 扫 `data/store/daily/{market}/*.json` 与 `minute/**`，逐 payload 写入 `daily_bars` / `daily_cache_meta`（分钟同理）
- 幂等：全部 `INSERT OR REPLACE`；每 50 个 code 打印一行进度；`SELECT COUNT(*) FROM daily_bars` 与文件数 × 平均 bar 数校验

### 关键约束

- **不删原 JSON**：迁移后 `data/store/daily/**/*.json` 原地保留作回滚备份，稳定后再清理
- **`--dry-run`**：只统计不写入，先预估规模
- 不回填历史 `amount`（见 [bars-update-cache.md](./bars-update-cache.md) §8）

## 八、cache_cli 收口（唯一外部契约泄漏）

旧研究缓存 CLI 的 `--clear` 绕过 store 直接 `os.remove` 删 JSON，迁库后失效。

### 改法

`core/store.py` 提供 `clear_daily_cache(market=None, store_dir=None) -> int`：`DELETE FROM daily_bars [WHERE market=?]` + `DELETE FROM daily_cache_meta [WHERE market=?]`，返回删除行数。`--list` 走 `list_cached_symbols`；列表能力由 store 接口供 `core/research_universe.py` 等调用。

## 九、风险与回滚

| 风险 | 缓解 |
|------|------|
| SQLite 改造引入 bug 导致 bars 读不到 | `QUANTLAB_BARS_BACKEND=sqlite\|json`，出问题切回 `json`（旧函数保留为 `_json` 后缀）；迁移不删原 JSON |
| WAL 文件增长 / 并发写死锁 | 定期 `PRAGMA wal_checkpoint(TRUNCATE)`；`_write_lock` + `busy_timeout=5000` 双保险，实测无死锁 |
| 测试 mock 点失效 | mock 点在 `adapters.market.history.load_daily_cache`，store 内部改实现不影响 mock 路径 |
| meta 字段遗漏 | 映射表已逐字段核对调用方；`tests/test_store.py` 直测兜底 |

### 回滚路径

`git revert` store / CLI 改动即可；数据层因原 JSON 保留而无损。

## 十、改动清单总览

- `core/store.py`：6 函数内部重写 + 新增 `_conn` / `_db_path` / `clear_daily_cache` / 建表（~250 行）
- `scripts/migrate_bars_to_sqlite.py`：新增迁移脚本（~80 行）
- `adapters/market/history.py` · `minute_history.py` · `core/data/facade.py` / ports / signal / backtest / paper：**不改**

## 十一、决策点（已拍板 · A1）

1. `quality` 存 JSON 串 — **采用**
2. `QUANTLAB_BARS_BACKEND` 双后端 — **采用**（默认 `sqlite`，可切 `json`）
3. 分钟线同步迁 — **采用**（与日线同库）
