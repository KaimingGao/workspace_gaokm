# 日线契约修补规划

[← 文档索引](../README.md) · 现状见 [行情更新、缓存与预处理](./bars-update-cache.md)

**日期**：2026-10-16  
**状态**：P1–P4 代码已落地。2026-10-16 收盘后已跑观察池日线 **topup**（798/800 齐 as-of；`000004`/`000016` 源侧停更已移出观察池）。全池 `mode=full` 仍可选（清从未触发重锚的旧复权头、补全 `amount`）。分钟 Ready 未在本轮扫尾。  
**定位**：修补四处已对上的契约缝。不新加管道类型，不把回测读仓并进日更。  
**运维脚本**：`python3 scripts/run_bars_topup_once.py`（会清同会话误写标记再 topup）。

## 一句话

```text
记账先诚实 → 面板指纹看见中间 K 线 → 成交额进库 → 前复权重叠日变了就整段替换
已拼坏的仓靠一次 full 修掉；之后每天仍走缺口，不把 1000 只整窗当成日课
```

## 锁定

| 做 | 不做 |
|----|------|
| 前复权在重叠日价位变化时整段替换 | 每个交易日对全池整窗重拉 |
| `daily_bars.amount`；旧行空着继续用 `volume × close` | 分钟线加成交额；换 Parquet / 另起数仓 |
| 面板指纹覆盖窗内每一根；schema 升到 3 | 把开盘日打分缓存并进面板缓存 |
| `cache:stale` 算失败；有失败不写当日标记 | 用标记拦住下一次拉取 |
| 回测继续只看根数、忽略 TTL | 读仓时顺便刷新末根 |

四处可以分成四个小变更，按下面的顺序合。P4 的行为依赖 P3 的列已经存在，但不依赖 P1、P2。

---

## P1 · 刷新记账

**问题**：远端失败退回 `cache:stale` 时，有旧 K 线，来源以 `cache` 开头，既不是远端成功，也不算失败。函数末尾仍调用 `mark_force_latest_bars_done`。界面 `needs_force_latest_bars` 变成否，覆盖率可以仍是末根未齐。

**改** `quant/research/bars_status.py` · `refresh_bars_only`：

- 来源为 `cache:stale`，或包为空、K 线为空：`failed_n += 1`。
- `failed_n > 0` 时不写当日标记。成功的票下次 topup 会因末根已齐而跳过，失败的票还会被拉。
- 标记本身仍然只表示「本会话日整批成功过」，不在拉取前读它来跳过。

**测试** `tests/test_bars_status.py`：

- 一批里混入 `data_source=cache:stale` 且有 K 线：`failed_count` 增加，`mark_force_latest_bars_done` 不被调用。
- 全是 `akshare` 且有 K 线：标记照旧写入。

**验收**：不碰库、不打远端。现有 topup / 短仓用例保持通过。

---

## P2 · 面板指纹

**问题**：`bars_fingerprint` 只哈希根数、首日、末日和最后一根。前复权改写中间价、最后一根不变时，`panel_matrix` / `oo_rank_panel` / `oo_ridge_compact` 命中旧矩阵。开盘日打分的 `day_open_inputs_fingerprint` 已经按窗内每根取屑。

**改** `core/research/panel_disk_cache.py`：

- 日线：每根的 `date|open|high|low|close|volume` 按顺序拼起来再哈希。分钟线同样覆盖每一根，不只最后一根。
- `PANEL_CACHE_SCHEMA` 从 2 升到 3。旧目录 `v2/` 不再读。算法变化本身也会使指纹对不上，升 schema 是为了目录不混。
- 空序列仍是 `empty`。新的一天、最后一根价量变化，指纹必须变（现有测试已经锁这个）。

**测试** `tests/test_panel_disk_cache.py`：

- 只改中间一根的 `close`，首尾和根数不变：指纹变。
- 新的一天：指纹变（已有）。
- 分片写到 `v3/`。

**验收**：下一次拟合全池 miss 一次，重算后按新指纹落盘。不迁移 `v2` 文件。调仓打分缓存的键不含这枚面板指纹，不用清 `replay_score_cache`。

---

## P3 · 成交额列

**问题**：`normalize_bars` 写出 `amount`。SQLite `daily_bars` 的插入和查询都没有这一列。`bar_amount` 退回 `volume × close`。JSON 后端把整个 bar 留着，换后端 Amihud 会变。

**改** `core/store_bars_sqlite.py`，沿用 `minute_cache_meta.span_days` 的方式，不另做迁移框架：

- `_ensure_schema` 里 `PRAGMA table_info(daily_bars)`，没有 `amount` 就 `ALTER TABLE daily_bars ADD COLUMN amount REAL`。
- `save_daily` / 批量读 / 单票读把 `amount` 写回、读出。缺字段或 NULL 不写 0，读出来的 bar 不带 `amount`。
- `core/bar_fields.bar_amount` 保持「有 `amount` 用它，没有则 `volume × close`」。不改 Amihud 公式。
- JSON 路径已经保留 `amount`，不动。

**不回填历史。** 旧行的 `amount` 保持 NULL，直到该日被下一次成功写入覆盖。P4 的整段替换会带上新窗里的成交额。

**测试**：

- 新库与「没有 amount 列的旧库」打开后列存在。
- 写入带 `amount` 的 bar，读回相等。
- 不带 `amount` 的 bar 读回没有这个键，`bar_amount` 仍等于 `volume × close`。

**验收**：`QUANTLAB_BARS_BACKEND=json` 的现有用例不变。不扫全库补数。

---

## P4 · 前复权整段替换

**问题**：topup 对够长的票只拉最后一根到今天，再按日期盖上去。前复权以最新价为锚，分红会改写重叠日之前的全部历史价，本地旧日期留在旧价位上。末根日期看起来是齐的。

每天对未齐的票整窗重拉能避免拼接，但观察池上限 1000、窗长 1062，不能当每个交易日的默认路径。

**规则**：平时仍走缺口。缺口结果里若重叠日（本地最后一根的日期）的开高低收与本地不一致，视为复权锚变了，再整窗拉一次，并且**替换**该 `(market, code, adjust_policy)` 的日线，不保留更早的旧价。

重叠日一致（含成交量修订、价格不变）则只合并尾巴。

`raw` / `hfq` 不走这套检测。`hfq` 的锚不在最新价，另说；当前默认策略是 `qfq`。

### 检测

放在 `adapters/market/history.py`，增量拉取返回之后、`merge_bars_by_date` 之前：

- 只在 `adjust=qfq` 且本地有末根时做。
- 在远端结果里找与本地末根同一天的 bar。找不到同一天：不当作复权变化，维持现在的合并（远端可能没给到重叠日）。
- 开、高、低、收任一相对差超过 `1e-4`，或绝对差超过 `0.01`：判为重锚。
- 重锚后第二次请求不带 `start_date`，`fetch_limit = max(请求 limit, len(本地) + 5)`，与今天长缺口分支同一量级。

### 替换，而不是再合并

现在的 `save_daily` 是「同日覆盖，没带到的日期留着」，超过 1200 根再从最早裁。整窗若比库里短，头部旧复权会留下。

重锚和第二种入口（`refresh_bars_only` 的 `mode=full`，以及短仓整窗）改为替换写：

- 仅当新序列的根数 ≥ `min(本地根数, 本次请求 limit)` 的 90%，或新序列的 `date_min` ≤ 本地 `date_min`，才删除该键下全部日线再插入新序列。
- 新序列更短且盖不住本地起点：不删库，返回失败，来源保持 `cache:stale`。P1 会把它计为失败，不写当日标记。
- 替换成功后的裁剪仍是 `DAILY_BARS_MAX_KEEP`。

JSON 后端同一规则：换文件内容为新序列，不用「按日合并后留着文件里更早的 bar」。

`QFQ_LONG_GAP_DAYS = 40` 保留，作为「很久没更新、重叠日检测不可靠」时直接整窗的入口。短缺口不再靠它发现分红。

### 已经拼坏的仓

末根已齐的序列不会进入 topup，重叠日检测也不会跑到它们。上线后做**一次** `mode=full`。这一次走替换写，把旧头部去掉。不要靠之后的每日 topup 自愈。

这一次放在收盘后、人手看着跑。池子大时按现有每批 32、单票 60 秒。失败的票留在 `failed_count` 里，标记不写，可以再跑一轮，已替换成功且末根已齐的票会被 topup 跳过。

### 测试

`tests/test_history.py`（可新用例，不打真网）：

- 重叠日收盘价一致：只合并新日期，更早的 bar 原样还在。
- 重叠日收盘价差一截：发生第二次无 `start_date` 的拉取；库里不存在新序列 `date_min` 之前的 bar。
- 第二次拉取明显短于本地：库不被清空，来源为 `cache:stale`。
- `adjust=raw` 时重叠日价格不同也不整段删除。

**验收**：日常 topup 的请求窗仍是缺口天数量级。只有重锚或 `mode=full` 或短仓才拉约 1062 根。

---

## 上线顺序

| 步 | 内容 | 数据影响 |
|----|------|----------|
| 1 | P1 记账 | 无。失败的刷新不再显示「今天已刷过」 |
| 2 | P2 指纹 | 下一次拟合重算面板。不删 `v2`，只是不再读 |
| 3 | P3 加列 | 打开库时 `ALTER`。旧行 `amount` 为空，Amihud 行为与现在相同 |
| 4 | P4 代码 | 当日若无新 bar，topup 仍跳过已齐的票 |
| 5 | 一次 `mode=full` | 替换各票日线窗，写入 `amount`，末根对齐。面板在下一步拟合时因指纹变化重算 |

P1–P3 可以独立合入。P4 的替换写合入之后、全池 full 之前，不要把「今天已刷过」理解成复权已经干净。

## 验收命令

```bash
cd quantlab
python3 -m unittest tests.test_bars_status tests.test_bars_daily tests.test_history tests.test_panel_disk_cache tests.test_store tests.test_data_service -v
```

全池 full 之后抽几只近期有分红的票：本地首日到末日的收盘价与一次整窗前复权结果对齐；`amount` 在新窗内非空；`needs_force_latest_bars` 只在本批 `failed_count = 0` 时为否。

## 明确不改

- `load_portfolio_stock_bars` 的 `ignore_age` 和「根数不够才补远端」。
- 分钟线的同日整段替换、Ready 闸、东财/新浪。
- 研究服务拒绝 `quote_fallback`、24 小时日线 TTL、复权策略不一致则跳过缓存。
- `replay_score_cache` 的开盘日指纹算法。
